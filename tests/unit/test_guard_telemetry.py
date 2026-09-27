from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from cuanta.adapters.storage.capsule_store import FileCapsuleStore
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.cross_engine import CrossEnginePipeline, cross_metrics
from cuanta.application.engine_run import EngineLauncher
from cuanta.application.mandate import report_payload
from cuanta.application.mandate_flow import MandateFlow, MandateOptions
from cuanta.application.progress import RecordingSink
from cuanta.application.run_reports import RunReports
from cuanta.application.trials import parse_trial, trial_payload
from cuanta.domain.change_plan import ChangePlan, EditTarget
from cuanta.domain.detection import Stack
from cuanta.domain.errors import DomainFailure
from cuanta.domain.messages import Message
from cuanta.domain.sandbox import SandboxLaunch
from cuanta.ports.sandbox import SandboxCopy
from cuanta.ports.workspace import ScanResult
from tests.unit.test_cross_engine import plan
from tests.unit.test_sandbox_runs import FEATURE, EditingEngine, Harness, _write

PROTECTION = ChangePlan(
    edit=(EditTarget("src/app.ts", 1.0),),
    guard=("src/old.ts",),
)


def changed_paths(protected: bool, guard_path: str) -> tuple[str, ...]:
    return tuple(sorted(("src/app.ts", "src/sitemap.ts", *((guard_path,) if protected else ()))))


def unplanned_paths(protected: bool, guard_path: str) -> tuple[str, ...]:
    return tuple(sorted(("src/sitemap.ts", *((guard_path,) if protected else ()))))


def edits(protected: bool, guard_path: str) -> Callable[[Path], None]:
    def write(root: Path) -> None:
        _write(root, "src/app.ts", b"export const a = 2\r\n")
        _write(root, "src/sitemap.ts", b"export default []\n")
        if protected:
            _write(root, guard_path, b"protected file changed\n")

    return write


def snapshot(root: Path) -> dict[str, str]:
    workspace = LocalWorkspace(root)
    scan = workspace.scan(frozenset(), collect_files=True, all_files=True)
    return {
        path: f"{digest}:{scan.modes.get(path, 0)}"
        for path in scan.files
        if (digest := workspace.sha256(path)) is not None
    }


def mandate_flow(
    harness: Harness, copy: SandboxCopy, launch: SandboxLaunch, protection: ChangePlan
) -> MandateFlow:
    original = harness.flow(copy, launch)
    return MandateFlow(
        original.service,
        lambda _: harness.engine,
        harness.launcher,
        Stack,
        lambda _: ({}, None),
        str(copy.root),
        harness.engine.name,
        0.0,
        new_run_id=lambda: f"RUN{next(harness.ids)}",
        sandbox=launch,
        change_plan=lambda _: protection,
    )


@pytest.mark.parametrize(
    "guard_path",
    ["src/old.ts", "docs/protected.md", "styles/protected.css", ".config/protected.json"],
)
@pytest.mark.parametrize("protected", [False, True], ids=["unplanned", "protected"])
def test_cross_engine_records_actual_paths_and_protected_writes_fail(
    tmp_path: Path, protected: bool, guard_path: str
) -> None:
    harness = Harness(tmp_path, EditingEngine(edits(protected, guard_path)))
    _write(harness.project, guard_path, b"protected original\n")
    protection = replace(PROTECTION, guard=(guard_path,))
    reports = RunReports(harness.storage)
    persisted: list[str] = []

    def save(run_id: str, metrics: Mapping[str, object]) -> None:
        persisted.append(run_id)
        reports.save_meta(run_id, {**(reports.meta(run_id) or {}), **metrics})

    def pipeline_for(
        copy: SandboxCopy, launch: SandboxLaunch, checkpoint: Callable[[], Message | None]
    ) -> CrossEnginePipeline:
        def launcher(name: str) -> EngineLauncher:
            engine = EditingEngine(
                edits(protected, guard_path) if name == "codex" else lambda _: None, name
            )
            return EngineLauncher(
                engine,
                harness.ledger,
                harness.clock,
                lambda: f"RUN{next(harness.ids)}",
                lambda size: b"\x01" * size,
                "shop",
                4318,
                None,
            )

        return CrossEnginePipeline(
            launcher,
            tuple,
            FileCapsuleStore(harness.project / ".cuanta" / "capsules"),
            str(copy.root),
            5.0,
            sandbox=launch,
            checkpoint=checkpoint,
            change_plan=lambda _: protection,
            snapshot=lambda: snapshot(copy.root),
            save_metrics=save,
        )

    result = harness.runner.run_cross(
        pipeline_for, FEATURE, plan(), RecordingSink(), False, payload=cross_metrics
    )
    report = result.cross
    assert report is not None
    assert len(report.steps) == 3 and all(step.ok for step in report.steps)
    assert report.ok is not protected
    assert report.changed_files == changed_paths(protected, guard_path)
    metrics = cross_metrics(report)
    assert metrics["actual_edited_paths"] == changed_paths(protected, guard_path)
    assert metrics["out_of_plan_edits"] == unplanned_paths(protected, guard_path)
    assert metrics["guard_violations"] == ((guard_path,) if protected else ())
    assert persisted == [report.steps[0].run_id]
    saved = reports.meta(report.steps[0].run_id)
    assert saved is not None
    assert saved["actual_edited_paths"] == list(changed_paths(protected, guard_path))
    assert saved["out_of_plan_edits"] == list(unplanned_paths(protected, guard_path))
    assert saved["guard_violations"] == ([guard_path] if protected else [])
    assert (harness.project / "src" / "app.ts").read_bytes() == b"export const a = 1\r\n"


@pytest.mark.parametrize(
    "guard_path",
    ["src/old.ts", "docs/protected.md", "styles/protected.css", ".config/protected.json"],
)
@pytest.mark.parametrize("protected", [False, True], ids=["unplanned", "protected"])
def test_mandate_guard_metrics_roundtrip_and_protected_trials_cannot_apply(
    tmp_path: Path, protected: bool, guard_path: str
) -> None:
    harness = Harness(tmp_path, EditingEngine(edits(protected, guard_path)))
    _write(harness.project, guard_path, b"protected original\n")
    protection = replace(PROTECTION, guard=(guard_path,))

    result = harness.runner.run_mandate(
        lambda copy, launch: mandate_flow(harness, copy, launch, protection),
        FEATURE,
        0,
        MandateOptions(simple=True),
        RecordingSink(),
        payload=report_payload,
    )
    report = result.report
    assert report is not None
    assert report.run.status == "ok"
    assert report.ok is not protected
    assert report.changed_files == changed_paths(protected, guard_path)
    payload = report_payload(report)
    assert payload["actual_edited_paths"] == changed_paths(protected, guard_path)
    assert payload["out_of_plan_edits"] == unplanned_paths(protected, guard_path)
    assert payload["guard_violations"] == ((guard_path,) if protected else ())
    saved = RunReports(harness.storage).meta(report.run.id)
    assert saved is not None
    assert saved["actual_edited_paths"] == list(changed_paths(protected, guard_path))
    assert saved["guard_violations"] == ([guard_path] if protected else [])
    trial = result.trial
    assert trial is not None
    assert trial.guard_violations == ((guard_path,) if protected else ())
    assert parse_trial(trial_payload(trial)) == trial
    assert harness.store.load(trial.run_id) == trial
    assert trial.guard_tripped is protected
    assert trial.applicable is not protected
    summary = harness.store.summary(trial.run_id)
    assert summary is not None and summary.can_apply is not protected
    if protected:
        before = snapshot(harness.project)
        with pytest.raises(DomainFailure, match="protected"):
            harness.store.apply(trial.run_id)
        assert snapshot(harness.project) == before
        assert (harness.project / guard_path).read_bytes() == b"protected original\n"


def test_trial_derives_guard_violations_from_actual_changes_when_payload_omits_them(
    tmp_path: Path,
) -> None:
    def write_protected(root: Path) -> None:
        _write(root, "protected.py", b"value = 2\n")

    harness = Harness(tmp_path, EditingEngine(write_protected))
    _write(harness.project, "protected.py", b"value = 1\n")
    result = harness.runner.run_mandate(
        harness.flow,
        FEATURE,
        0,
        MandateOptions(simple=True),
        RecordingSink(),
        payload=lambda _: {
            "guard_violations": [],
            "change_plan": {"guard": ["protected.py"]},
        },
    )
    trial = result.trial
    assert trial is not None
    assert trial.paths == ("protected.py",)
    assert trial.guard_violations == ("protected.py",)
    loaded = harness.store.load(trial.run_id)
    assert loaded == trial
    assert loaded is not None and not loaded.applicable
    summary = harness.store.summary(trial.run_id)
    assert summary is not None and not summary.can_apply
    before = snapshot(harness.project)
    with pytest.raises(DomainFailure, match="protected"):
        harness.store.apply(trial.run_id)
    assert snapshot(harness.project) == before
    assert (harness.project / "protected.py").read_bytes() == b"value = 1\n"


def test_mode_only_protected_changes_fail_and_cannot_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changed = False

    def make_executable(root: Path) -> None:
        nonlocal changed
        changed = True
        if os.name != "nt":
            (root / "protected.py").chmod(0o755)

    harness = Harness(tmp_path, EditingEngine(make_executable))
    _write(harness.project, "protected.py", b"value = 1\n")
    (harness.project / "protected.py").chmod(0o644)
    if os.name == "nt":
        native_scan = LocalWorkspace.scan
        native_executable = harness.sandbox.executable

        def controlled_scan(
            workspace: LocalWorkspace,
            extra_exclusions: frozenset[str],
            collect_files: bool = False,
            all_files: bool = False,
        ) -> ScanResult:
            scan = native_scan(workspace, extra_exclusions, collect_files, all_files)
            if "protected.py" not in scan.files:
                return scan
            modes = dict(scan.modes)
            modes["protected.py"] = 0o111 if changed and workspace.root != harness.project else 0
            return replace(scan, modes=modes)

        def mode_changes(copy: SandboxCopy) -> tuple[str, ...]:
            return ("protected.py",) if changed else ()

        def executable(copy: SandboxCopy, relative: str) -> bool:
            return changed if relative == "protected.py" else native_executable(copy, relative)

        monkeypatch.setattr(LocalWorkspace, "scan", controlled_scan)
        monkeypatch.setattr(harness.sandbox, "mode_changes", mode_changes)
        monkeypatch.setattr(harness.sandbox, "executable", executable)
    protection = ChangePlan(guard=("protected.py",))
    result = harness.runner.run_mandate(
        lambda copy, launch: mandate_flow(harness, copy, launch, protection),
        FEATURE,
        0,
        MandateOptions(simple=True),
        RecordingSink(),
        payload=report_payload,
    )
    report = result.report
    assert report is not None and not report.ok
    assert report.changed_files == ("protected.py",)
    start = {item.path: item.sha256 for item in harness.ledger.snapshots(report.run.id, "start")}
    end = {item.path: item.sha256 for item in harness.ledger.snapshots(report.run.id, "end")}
    assert start["protected.py"] == end["protected.py"]
    trial = result.trial
    assert trial is not None and trial.guard_violations == ("protected.py",)
    change = next(change for change in trial.changes if change.path == "protected.py")
    assert change.before == change.after
    assert change.executable and not change.base_executable
    summary = harness.store.summary(trial.run_id)
    assert summary is not None and not summary.can_apply
    before = snapshot(harness.project)
    with pytest.raises(DomainFailure, match="protected"):
        harness.store.apply(trial.run_id)
    assert snapshot(harness.project) == before
