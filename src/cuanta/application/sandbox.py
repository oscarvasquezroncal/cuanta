from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path

from cuanta.application.cross_engine import CrossEnginePipeline, CrossReport, CrossStep
from cuanta.application.mandate import MandateReport
from cuanta.application.mandate_flow import MandateFlow, MandateOptions, Prepared
from cuanta.application.routing import RoutePlan
from cuanta.application.trials import (
    BASE_DIR,
    FILES_DIR,
    GUARD_LIMIT,
    METRICS_FILE,
    PATCH_FILE,
    REPORT_FILE,
    TRIAL_FILE,
    Trial,
    TrialChange,
    digest,
    trial_payload,
)
from cuanta.domain.change_plan import ChangePlan, EditTarget, plan_metrics
from cuanta.domain.engine import EngineEvent
from cuanta.domain.handoff import COMMIT_FILE, PATHS_FILE, change_type, commit_subject
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import Message, msg
from cuanta.domain.patch import file_patch, join_patches
from cuanta.domain.progress import Status, note
from cuanta.domain.report import parse_sections, section
from cuanta.domain.sandbox import (
    SANDBOX_MODE,
    ChangeKind,
    FileChange,
    SandboxLaunch,
    diff_manifests,
    sandbox_launch,
    trial_folder,
    trial_of,
)
from cuanta.ports.ledger import Ledger
from cuanta.ports.progress import ProgressSink
from cuanta.ports.sandbox import ProjectSandbox, SandboxCopy
from cuanta.ports.workspace import Workspace

SHOWN_IGNORED = 5


def recorded_plan(payload: Mapping[str, object]) -> ChangePlan | None:
    raw = payload.get("change_plan")
    if not isinstance(raw, dict):
        return None
    guards = raw.get("guard")
    edits = raw.get("edit")
    return ChangePlan(
        edit=tuple(
            EditTarget(item["path"], 1.0)
            for item in edits
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        )
        if isinstance(edits, tuple | list)
        else (),
        guard=tuple(item for item in guards if isinstance(item, str))
        if isinstance(guards, tuple | list)
        else (),
        read_only=raw.get("read_only") is True,
    )


@dataclass(frozen=True, slots=True)
class SandboxResult:
    trial: Trial | None
    copy_root: str
    removed: bool
    report: MandateReport | None = None
    cross: CrossReport | None = None
    record_error: str = ""


class TrialRecorder:
    def __init__(
        self,
        sandbox: ProjectSandbox,
        storage: Workspace,
        clock_iso: Callable[[], str],
    ) -> None:
        self._sandbox = sandbox
        self._storage = storage
        self._clock_iso = clock_iso

    def _base(self, change: FileChange) -> bytes | None:
        data = self._storage.read_bytes(change.path)
        return data if data is not None and digest(data) == change.before else None

    def record(
        self,
        copy: SandboxCopy,
        run_id: str,
        request: MandateRequest,
        engine: str,
        report_text: str,
        payload: Mapping[str, object],
        kept: bool,
        state: Sequence[str] = (),
    ) -> Trial:
        folder = trial_folder(run_id)
        items: list[TrialChange] = []
        patches = []
        missing: list[str] = []
        for change in self._changes(copy):
            after = self._sandbox.read(copy, change.path) if change.after is not None else None
            before = self._base(change) if change.before is not None else None
            if after is not None:
                self._storage.write_bytes(f"{folder}/{FILES_DIR}/{change.path}", after)
            if before is not None:
                self._storage.write_bytes(f"{folder}/{BASE_DIR}/{change.path}", before)
            unknown = (change.before is not None and before is None) or (
                change.after is not None and after is None
            )
            if unknown:
                missing.append(change.path)
                items.append(TrialChange(change.path, change.kind, change.before, change.after))
                continue
            executable = change.after is not None and self._sandbox.executable(copy, change.path)
            known = copy.base.get(change.path)
            was_executable = known is not None and known.executable
            patch = file_patch(change.path, before, after, executable, was_executable)
            patches.append(patch)
            items.append(
                TrialChange(
                    change.path,
                    change.kind,
                    change.before,
                    change.after,
                    patch.added,
                    patch.removed,
                    patch.binary,
                    known.size if known is not None else -1,
                    known.mtime_ns if known is not None else -1,
                    executable,
                    was_executable,
                )
            )
        paths = tuple(item.path for item in items)
        plan = recorded_plan(payload)
        if plan is not None:
            payload = {
                **payload,
                **plan_metrics(plan, paths, engine),
                "change_plan": payload["change_plan"],
            }
        proposal = section(parse_sections(report_text), "commit")
        kind = change_type(request.type, paths)
        guard = self._sandbox.dependencies_changed(copy)
        ignored = self._sandbox.ignored_changes(copy)
        violations = payload.get("guard_violations")
        trial = Trial(
            run_id=run_id,
            task_type=request.type,
            what=request.what,
            engine=engine,
            subject=commit_subject(kind, request.what, proposal.body if proposal else "")
            if kind
            else "",
            changes=tuple(items),
            copy_root=str(copy.root),
            kept=kept,
            created_at=self._clock_iso(),
            linked=copy.linked,
            dependencies_changed=guard[:GUARD_LIMIT],
            dependencies_changed_count=len(guard),
            base_missing=tuple(missing),
            ignored_changes=ignored[:GUARD_LIMIT],
            ignored_changes_count=len(ignored),
            state_changed=tuple(state[:GUARD_LIMIT]),
            state_changed_count=len(state),
            guard_violations=tuple(path for path in violations if isinstance(path, str))
            if isinstance(violations, tuple | list)
            else (),
        )
        self._write(folder, trial, join_patches(patches), report_text, copy, payload)
        return trial

    def _changes(self, copy: SandboxCopy) -> list[FileChange]:
        changes = list(diff_manifests(copy.hashes(), self._sandbox.manifest(copy)))
        listed = {change.path for change in changes}
        for path in self._sandbox.mode_changes(copy):
            known = copy.base.get(path)
            if known is not None and path not in listed:
                changes.append(FileChange(path, ChangeKind.MODIFIED, known.sha256, known.sha256))
        return sorted(changes, key=lambda change: change.path)

    def _write(
        self,
        folder: str,
        trial: Trial,
        patch: str,
        report_text: str,
        copy: SandboxCopy,
        payload: Mapping[str, object],
    ) -> None:
        storage = self._storage
        storage.write_bytes(f"{folder}/{PATCH_FILE}", patch.encode("utf-8", "surrogateescape"))
        names = "".join(f"{path}\0" for path in trial.paths)
        storage.write_bytes(f"{folder}/{PATHS_FILE}", names.encode("utf-8", "surrogateescape"))
        if trial.subject:
            storage.write_bytes(f"{folder}/{COMMIT_FILE}", f"{trial.subject}\n".encode())
        if report_text:
            storage.write_text(f"{folder}/{REPORT_FILE}", report_text.rstrip("\n") + "\n")
        storage.write_text(f"{folder}/{TRIAL_FILE}", json.dumps(trial_payload(trial), indent=2))
        metrics = {
            **payload,
            "sandbox": {
                "copied_files": copy.copied_files,
                "copied_bytes": copy.copied_bytes,
                "linked": list(copy.linked),
                "linked_files": copy.linked_files,
                "copy_seconds": round(copy.seconds, 3),
                "skipped_links": list(copy.skipped_links),
                "skipped_caches": list(copy.skipped_caches),
                "kept": trial.kept,
                "copy_root": trial.copy_root,
            },
            "trial": {
                "files": len(trial.changes),
                "added_lines": trial.added,
                "removed_lines": trial.removed,
                "binary_files": sum(1 for change in trial.changes if change.binary),
                "by_kind": {
                    kind.value: sum(1 for change in trial.changes if change.kind is kind)
                    for kind in ChangeKind
                },
                "base_missing": list(trial.base_missing),
                "dependencies_changed_count": trial.dependencies_changed_count,
                "state_changed_count": trial.state_changed_count,
                "ignored_changes_count": trial.ignored_changes_count,
                "read_only_breach": trial.read_only_breach,
                "subject": trial.subject,
                "patch": f"{folder}/{PATCH_FILE}",
            },
        }
        storage.write_text(f"{folder}/{METRICS_FILE}", json.dumps(metrics, indent=2, default=str))


class SandboxRunner:
    def __init__(
        self,
        sandbox: ProjectSandbox,
        recorder: TrialRecorder,
        ledger: Ledger,
        origin: Path,
    ) -> None:
        self._sandbox = sandbox
        self._recorder = recorder
        self._ledger = ledger
        self._origin = origin

    def _open(self, progress: ProgressSink) -> tuple[SandboxCopy, SandboxLaunch]:
        copy = self._sandbox.create(self._origin)
        progress.publish(
            note(
                Status.INFO,
                msg(
                    "sandbox.copied",
                    files=copy.copied_files,
                    linked=copy.linked_files,
                    seconds=f"{copy.seconds:.1f}",
                    path=str(copy.root),
                ),
            )
        )
        if copy.skipped_links:
            progress.publish(
                note(
                    Status.WARN,
                    msg(
                        "sandbox.skipped_links",
                        count=len(copy.skipped_links),
                        paths=", ".join(copy.skipped_links[:SHOWN_IGNORED]),
                    ),
                )
            )
        if copy.outside_dependencies:
            progress.publish(
                note(
                    Status.WARN,
                    msg(
                        "sandbox.outside_dependencies",
                        paths=", ".join(copy.outside_dependencies),
                    ),
                )
            )
        if copy.unreadable:
            progress.publish(
                note(
                    Status.WARN,
                    msg(
                        "sandbox.unreadable",
                        count=len(copy.unreadable),
                        paths=", ".join(copy.unreadable[:SHOWN_IGNORED]),
                    ),
                )
            )
        if copy.skipped_outputs:
            progress.publish(
                note(
                    Status.INFO,
                    msg("sandbox.skipped_outputs", paths=", ".join(copy.skipped_outputs)),
                )
            )
        launch = sandbox_launch(
            str(copy.origin), str(copy.slot), bool(copy.linked), copy.python_path
        )
        return copy, launch

    def checkpoint(self, copy: SandboxCopy, run_id: str = "") -> Message | None:
        changed = self._sandbox.dependencies_changed(copy)
        if changed:
            return msg("sandbox.dependencies_changed", count=len(changed))
        state = self._state(copy, run_id)
        return _state_message(state, len(state)) if state else None

    def _state(self, copy: SandboxCopy, run_id: str) -> tuple[str, ...]:
        kept: list[str] = []
        for path in self._sandbox.state_changed(copy):
            owner = trial_of(path)
            if owner and owner == run_id:
                continue
            if owner and path not in copy.state and self._sandbox_run(owner):
                continue
            kept.append(path)
        return tuple(kept)

    def _sandbox_run(self, run_id: str) -> bool:
        run = self._ledger.get_run(run_id)
        return run is not None and run.mode == SANDBOX_MODE

    def _record(
        self,
        copy: SandboxCopy,
        run_id: str,
        request: MandateRequest,
        engine: str,
        report_text: str,
        payload: Mapping[str, object],
        keep: bool,
        progress: ProgressSink,
    ) -> Trial:
        state = self._state(copy, run_id)
        trial = self._recorder.record(
            copy, run_id, request, engine, report_text, payload, keep, state
        )
        folder = trial_folder(run_id)
        if trial.changes:
            progress.publish(
                note(
                    Status.INFO,
                    msg("sandbox.trial", files=len(trial.changes), path=f"{folder}/{PATCH_FILE}"),
                )
            )
        else:
            progress.publish(note(Status.INFO, msg("sandbox.no_changes")))
        if trial.read_only_breach:
            progress.publish(
                note(Status.WARN, msg("sandbox.read_only_breach", count=len(trial.changes)))
            )
        if trial.base_missing:
            progress.publish(
                note(Status.WARN, msg("sandbox.base_missing", count=len(trial.base_missing)))
            )
        if trial.ignored_changes_count:
            progress.publish(
                note(
                    Status.WARN,
                    msg(
                        "sandbox.ignored_changes",
                        count=trial.ignored_changes_count,
                        paths=", ".join(trial.ignored_changes[:SHOWN_IGNORED]),
                    ),
                )
            )
        if trial.dependencies_changed_count:
            progress.publish(
                note(
                    Status.FAIL,
                    msg("sandbox.dependencies_changed", count=trial.dependencies_changed_count),
                )
            )
        if trial.state_changed_count:
            progress.publish(
                note(Status.FAIL, _state_message(trial.state_changed, trial.state_changed_count))
            )
        return trial

    def _finish(
        self, copy: SandboxCopy, keep: bool, progress: ProgressSink, rescue: bool = False
    ) -> bool:
        if keep or rescue:
            with suppress(OSError):
                self._sandbox.keep(copy)
            progress.publish(note(Status.INFO, msg("sandbox.kept", path=str(copy.root))))
            return False
        try:
            removed = self._sandbox.remove(copy)
        except (OSError, ValueError):
            removed = False
        if not removed:
            progress.publish(note(Status.WARN, msg("sandbox.remove_failed", path=str(copy.slot))))
        return removed

    def _launched(self, run_id: str) -> bool:
        return bool(run_id) and self._ledger.get_run(run_id) is not None

    def _rescue(
        self,
        copy: SandboxCopy,
        run_id: str,
        request: MandateRequest,
        engine: str,
        report_text: str,
        keep: bool,
        progress: ProgressSink,
    ) -> Trial | None:
        if not self._launched(run_id):
            return None
        with suppress(Exception):
            return self._record(copy, run_id, request, engine, report_text, {}, keep, progress)
        self._guard(copy, progress, run_id)
        return None

    def _guard(self, copy: SandboxCopy, progress: ProgressSink, run_id: str = "") -> None:
        with suppress(Exception):
            guard = self.checkpoint(copy, run_id)
            if guard is not None:
                progress.publish(note(Status.FAIL, guard))

    def run_mandate(
        self,
        flow_for: Callable[[SandboxCopy, SandboxLaunch], MandateFlow],
        request: MandateRequest,
        signatures: int,
        options: MandateOptions,
        progress: ProgressSink,
        observer: Callable[[EngineEvent], None] | None = None,
        verdict: bool = True,
        payload: Callable[[MandateReport], Mapping[str, object]] | None = None,
        on_start: Callable[[MandateFlow, Prepared], None] | None = None,
    ) -> SandboxResult:
        copy, launch = self._open(progress)
        keep = options.keep_copy
        run_id, engine = "", ""
        trial: Trial | None = None
        rescue = False
        record_error = ""
        try:
            flow = flow_for(copy, launch)
            prepared = flow.prepare(request, signatures, replace(options, temporary_copy=True))
            run_id, engine = prepared.spec.run_id, prepared.engine_name
            if on_start is not None:
                on_start(flow, prepared)
            report = flow.run(prepared, progress, observer, verdict)
            extra = payload(report) if payload is not None else {}
            try:
                trial = self._record(
                    copy, report.run.id, request, engine, report.text, extra, keep, progress
                )
            except OSError as error:
                record_error = type(error).__name__
                failed = replace(report.run, status="failed", end_reason="error_sandbox_record")
                self._ledger.update_run(failed)
                report = replace(report, run=failed)
                rescue = True
                self._guard(copy, progress, report.run.id)
                progress.publish(
                    note(Status.FAIL, msg("sandbox.record_failed", error=record_error))
                )
        except BaseException:
            if trial is None:
                trial = self._rescue(copy, run_id, request, engine, "", keep, progress)
                rescue = trial is None and self._launched(run_id)
            raise
        finally:
            removed = self._finish(copy, keep, progress, rescue)
        return SandboxResult(
            trial, str(copy.root), removed, report=report, record_error=record_error
        )

    def run_cross(
        self,
        pipeline_for: Callable[
            [SandboxCopy, SandboxLaunch, Callable[[], Message | None]], CrossEnginePipeline
        ],
        request: MandateRequest,
        plan: RoutePlan,
        progress: ProgressSink,
        keep: bool,
        payload: Callable[[CrossReport], Mapping[str, object]] | None = None,
    ) -> SandboxResult:
        copy, launch = self._open(progress)
        trial: Trial | None = None
        rescue = False
        pipeline: CrossEnginePipeline | None = None
        record_error = ""
        try:
            pipeline = pipeline_for(copy, launch, lambda: self.checkpoint(copy))
            report = pipeline.run(request, plan, progress)
            if report.steps:
                extra = payload(report) if payload is not None else {}
                first = report.steps[0].run_id
                text = cross_text(report.steps)
                try:
                    trial = self._record(copy, first, request, "cross", text, extra, keep, progress)
                except OSError as error:
                    record_error = type(error).__name__
                    root_run = self._ledger.get_run(first)
                    if root_run is not None:
                        self._ledger.update_run(
                            replace(root_run, status="failed", end_reason="error_sandbox_record")
                        )
                    rescue = True
                    self._guard(copy, progress, first)
                    progress.publish(
                        note(Status.FAIL, msg("sandbox.record_failed", error=record_error))
                    )
        except BaseException:
            steps = tuple(pipeline.completed) if pipeline is not None else ()
            current = pipeline.current if pipeline is not None else ""
            first = steps[0].run_id if steps else current
            if trial is None and first:
                text = cross_text(steps)
                trial = self._rescue(copy, first, request, "cross", text, keep, progress)
                rescue = trial is None and self._launched(first)
            if trial is None and not rescue:
                self._guard(copy, progress)
            raise
        finally:
            removed = self._finish(copy, keep, progress, rescue)
        return SandboxResult(
            trial, str(copy.root), removed, cross=report, record_error=record_error
        )


def _state_message(paths: Sequence[str], count: int) -> Message:
    return msg("sandbox.state_changed", count=count, paths=", ".join(paths[:SHOWN_IGNORED]))


def cross_text(steps: Sequence[CrossStep]) -> str:
    return "\n\n".join(
        f"## {step.role.value.upper()}\n\n{step.handoff.strip()}"
        for step in steps
        if step.handoff.strip()
    )
