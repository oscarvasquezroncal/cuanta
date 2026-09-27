from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from cuanta.domain.errors import DomainFailure, EnvironmentFailure
from cuanta.domain.gitindex import IndexEntry, blob_id, parse_index
from cuanta.domain.handoff import (
    INVESTIGATION,
    PATHS_FILE,
    Handoff,
    Workflow,
    branch_from_head,
    change_type,
    commit_subject,
    gitdir_from_file,
    handoff,
)
from cuanta.domain.outcomes import ACCEPTED, CROSS_KIND, REJECTED
from cuanta.domain.sandbox import (
    SANDBOX_MODE,
    ChangeKind,
    FileChange,
    drifted,
    renamed_away,
    trial_folder,
    unsafe_path,
)
from cuanta.domain.shells import Shell
from cuanta.ports.ledger import Ledger
from cuanta.ports.workspace import Workspace

TRIAL_FILE = "trial.json"
PATCH_FILE = "change.patch"
METRICS_FILE = "metrics.json"
REPORT_FILE = "report.md"
APPLIED_FILE = "applied.json"
FILES_DIR = "files"
BASE_DIR = "base"
GUARD_LIMIT = 20
SHOWN_PATHS = 5
NPM_HINT = "run npm ci in the project to restore node_modules, then run the mandate again"
STATE_HINT = (
    "review .cuanta/config.toml and discard pending isolated-copy results you did not expect, "
    "then run the mandate again"
)


@dataclass(frozen=True, slots=True)
class TrialChange:
    path: str
    kind: ChangeKind
    before: str | None
    after: str | None
    added: int = 0
    removed: int = 0
    binary: bool = False
    base_size: int = -1
    base_mtime_ns: int = -1
    executable: bool = False
    base_executable: bool = False

    @property
    def mode(self) -> bool | None:
        return self.executable if self.executable != self.base_executable else None

    def change(self) -> FileChange:
        return FileChange(self.path, self.kind, self.before, self.after)


@dataclass(frozen=True, slots=True)
class Trial:
    run_id: str
    task_type: str
    what: str
    engine: str
    subject: str
    changes: tuple[TrialChange, ...]
    copy_root: str
    kept: bool
    created_at: str = ""
    linked: tuple[str, ...] = ()
    dependencies_changed: tuple[str, ...] = ()
    dependencies_changed_count: int = 0
    base_missing: tuple[str, ...] = ()
    ignored_changes: tuple[str, ...] = ()
    ignored_changes_count: int = 0
    state_changed: tuple[str, ...] = ()
    state_changed_count: int = 0
    guard_violations: tuple[str, ...] = ()

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(change.path for change in self.changes)

    @property
    def added(self) -> int:
        return sum(change.added for change in self.changes)

    @property
    def removed(self) -> int:
        return sum(change.removed for change in self.changes)

    @property
    def read_only_breach(self) -> bool:
        return self.task_type == INVESTIGATION and bool(self.changes)

    @property
    def guard_tripped(self) -> bool:
        return (
            self.dependencies_changed_count > 0
            or self.state_changed_count > 0
            or bool(self.guard_violations)
        )

    @property
    def applicable(self) -> bool:
        return (
            bool(self.changes)
            and self.task_type != INVESTIGATION
            and not self.base_missing
            and not self.guard_tripped
        )


@dataclass(frozen=True, slots=True)
class TrialSummary:
    trial: Trial
    outcome: str
    outcome_at: str
    drift: tuple[str, ...]
    applied_at: str = ""

    @property
    def pending(self) -> bool:
        return not self.outcome

    @property
    def can_apply(self) -> bool:
        return (
            self.trial.applicable
            and not self.applied_at
            and self.outcome != REJECTED
            and not self.drift
        )


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def trial_payload(trial: Trial) -> dict[str, object]:
    return {
        "run_id": trial.run_id,
        "task_type": trial.task_type,
        "what": trial.what,
        "engine": trial.engine,
        "subject": trial.subject,
        "created_at": trial.created_at,
        "copy_root": trial.copy_root,
        "kept": trial.kept,
        "linked": list(trial.linked),
        "dependencies_changed": list(trial.dependencies_changed),
        "dependencies_changed_count": trial.dependencies_changed_count,
        "base_missing": list(trial.base_missing),
        "ignored_changes": list(trial.ignored_changes),
        "ignored_changes_count": trial.ignored_changes_count,
        "state_changed": list(trial.state_changed),
        "state_changed_count": trial.state_changed_count,
        "guard_violations": list(trial.guard_violations),
        "changes": [
            {
                "path": change.path,
                "kind": change.kind.value,
                "before": change.before,
                "after": change.after,
                "added": change.added,
                "removed": change.removed,
                "binary": change.binary,
                "base_size": change.base_size,
                "base_mtime_ns": change.base_mtime_ns,
                "executable": change.executable,
                "base_executable": change.base_executable,
            }
            for change in trial.changes
        ],
    }


def _text(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    return value if isinstance(value, str) else ""


def _texts(data: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = data.get(key)
    return tuple(str(item) for item in value) if isinstance(value, list) else ()


def _sha(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _number(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _stat(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else -1


def _change(item: object) -> TrialChange | None:
    if not isinstance(item, dict):
        return None
    try:
        kind = ChangeKind(str(item.get("kind")))
    except ValueError:
        return None
    path = item.get("path")
    if not isinstance(path, str):
        return None
    return TrialChange(
        path,
        kind,
        _sha(item.get("before")),
        _sha(item.get("after")),
        _number(item.get("added")),
        _number(item.get("removed")),
        item.get("binary") is True,
        _stat(item.get("base_size")),
        _stat(item.get("base_mtime_ns")),
        item.get("executable") is True,
        item.get("base_executable") is True,
    )


def parse_trial(data: Mapping[str, object]) -> Trial | None:
    run_id = _text(data, "run_id")
    raw = data.get("changes")
    if not run_id or not isinstance(raw, list):
        return None
    changes = tuple(change for change in (_change(item) for item in raw) if change is not None)
    return Trial(
        run_id=run_id,
        task_type=_text(data, "task_type"),
        what=_text(data, "what"),
        engine=_text(data, "engine"),
        subject=_text(data, "subject"),
        changes=changes,
        copy_root=_text(data, "copy_root"),
        kept=data.get("kept") is True,
        created_at=_text(data, "created_at"),
        linked=_texts(data, "linked"),
        dependencies_changed=_texts(data, "dependencies_changed"),
        dependencies_changed_count=_number(data.get("dependencies_changed_count")),
        base_missing=_texts(data, "base_missing"),
        ignored_changes=_texts(data, "ignored_changes"),
        ignored_changes_count=_number(data.get("ignored_changes_count")),
        state_changed=_texts(data, "state_changed"),
        state_changed_count=_number(data.get("state_changed_count")),
        guard_violations=_texts(data, "guard_violations"),
    )


class TrialStore:
    def __init__(self, storage: Workspace, ledger: Ledger, clock_iso: Callable[[], str]) -> None:
        self._storage = storage
        self._ledger = ledger
        self._clock_iso = clock_iso

    def load(self, run_id: str) -> Trial | None:
        text = self._storage.read_text(f"{trial_folder(run_id)}/{TRIAL_FILE}")
        if text is None:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parse_trial(data) if isinstance(data, dict) else None

    def _require(self, run_id: str) -> Trial:
        trial = self.load(run_id)
        if trial is None:
            raise DomainFailure(
                f"run {run_id} has no isolated-copy changes",
                "only runs launched with --sandbox can be applied",
            )
        return trial

    def applied_at(self, run_id: str) -> str:
        text = self._storage.read_text(f"{trial_folder(run_id)}/{APPLIED_FILE}")
        if text is None:
            return ""
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return "unknown"
        value = data.get("applied_at") if isinstance(data, dict) else None
        return value if isinstance(value, str) and value else "unknown"

    def drift(self, trial: Trial) -> tuple[str, ...]:
        current = {change.path: self._storage.sha256(change.path) for change in trial.changes}
        return drifted((change.change() for change in trial.changes), current)

    def summary(self, run_id: str) -> TrialSummary | None:
        trial = self.load(run_id)
        if trial is None:
            return None
        run = self._ledger.get_run(run_id)
        outcome = run.outcome if run is not None else ""
        at = run.outcome_at if run is not None else ""
        applied = self.applied_at(run_id)
        settled = bool(applied) or outcome == REJECTED
        return TrialSummary(trial, outcome, at, () if settled else self.drift(trial), applied)

    def apply(self, run_id: str) -> Trial:
        trial = self._require(run_id)
        if self.applied_at(run_id):
            raise DomainFailure(f"run {run_id} was already applied", "nothing to do")
        run = self._ledger.get_run(run_id)
        if run is not None and run.outcome == REJECTED:
            raise DomainFailure(f"run {run_id} was discarded", "run the mandate again")
        self._check(trial)
        images = self._images(trial)
        originals = {change.path: self._storage.read_bytes(change.path) for change in trial.changes}
        try:
            for change in trial.changes:
                if change.kind is ChangeKind.DELETED:
                    self._storage.remove(change.path)
            modes = {change.path: change.mode for change in trial.changes}
            for path, data in images.items():
                self._storage.write_bytes(path, data, modes.get(path))
        except BaseException:
            self._restore(originals, trial)
            raise
        moved = renamed_away(change.change() for change in trial.changes)
        wrong = [
            change.path
            for change in trial.changes
            if change.path not in moved and self._storage.sha256(change.path) != change.after
        ]
        if wrong:
            self._restore(originals, trial)
            raise EnvironmentFailure(
                f"applying left {len(wrong)} files different from the run: {_shown(wrong)}",
                "the project was restored; check that nothing else is writing to it",
            )
        now = self._clock_iso()
        if not self._ledger.set_run_outcome(run_id, ACCEPTED, now):
            current = self._ledger.get_run(run_id)
            if current is not None and current.outcome == REJECTED:
                self._restore(originals, trial)
                raise DomainFailure(
                    f"run {run_id} was discarded while it was being applied",
                    "the project was restored",
                )
        marker = json.dumps({"applied_at": now, "files": list(trial.paths)}, indent=2)
        self._storage.write_text(f"{trial_folder(run_id)}/{APPLIED_FILE}", marker)
        self._ledger.set_routing_accepted(run_id, True)
        return trial

    def _check(self, trial: Trial) -> None:
        if trial.guard_violations:
            raise DomainFailure(
                f"protected paths changed during the run: {_shown(trial.guard_violations)}",
                "review the plan and run the mandate again",
            )
        if trial.state_changed_count:
            raise DomainFailure(
                "cuanta's own files in the project changed during the run: "
                f"{_shown(trial.state_changed)}",
                STATE_HINT,
            )
        if trial.dependencies_changed_count:
            raise DomainFailure(
                f"node_modules in the project changed during the run "
                f"({trial.dependencies_changed_count} files)",
                NPM_HINT,
            )
        if trial.task_type == INVESTIGATION:
            raise DomainFailure(
                "investigations are read-only; there is nothing to apply", "discard it instead"
            )
        if not trial.changes:
            raise DomainFailure("the run changed no files", "discard it instead")
        if trial.base_missing:
            raise DomainFailure(
                f"{len(trial.base_missing)} files changed in the project while the run worked: "
                f"{_shown(trial.base_missing)}",
                "discard this run or run it again",
            )
        unsafe = [change.path for change in trial.changes if unsafe_path(change.path)]
        if unsafe:
            raise DomainFailure(f"refusing unsafe paths: {_shown(unsafe)}", "discard this run")
        moved = self.drift(trial)
        if moved:
            raise DomainFailure(
                f"refusing to apply: {len(moved)} files changed since the copy: {_shown(moved)}",
                "review them, then discard this run or run it again",
            )

    def _images(self, trial: Trial) -> dict[str, bytes]:
        folder = trial_folder(trial.run_id)
        images: dict[str, bytes] = {}
        for change in trial.changes:
            if change.kind is ChangeKind.DELETED:
                continue
            data = self._storage.read_bytes(f"{folder}/{FILES_DIR}/{change.path}")
            if data is None or digest(data) != change.after:
                raise DomainFailure(
                    f"the stored copy of {change.path} is missing or damaged",
                    "discard this run or run it again",
                )
            images[change.path] = data
        return images

    def _restore(self, originals: Mapping[str, bytes | None], trial: Trial) -> None:
        modes = {
            change.path: change.base_executable
            for change in trial.changes
            if change.mode is not None
        }
        for path, data in originals.items():
            with suppress(OSError):
                if data is None:
                    self._storage.remove(path)
                else:
                    self._storage.write_bytes(path, data, modes.get(path))

    def discard(self, run_id: str, reason: str = "") -> Trial | None:
        trial = self.load(run_id)
        run = self._ledger.get_run(run_id)
        if run is not None and run.kind == CROSS_KIND and run.parent_id:
            raise DomainFailure(
                f"run {run_id} is a role of the cross-engine run {run.parent_id}",
                f"discard the pipeline: cuanta runs discard {run.parent_id}",
            )
        if trial is None and (run is None or run.mode != SANDBOX_MODE):
            self._require(run_id)
        if self.applied_at(run_id):
            raise DomainFailure(
                f"run {run_id} was already applied", "undo it with git if you do not want it"
            )
        if run is not None and run.outcome:
            raise DomainFailure(f"run {run_id} is already {run.outcome}", "nothing to do")
        if not self._ledger.set_run_outcome(run_id, REJECTED, self._clock_iso(), reason):
            raise DomainFailure(f"run {run_id} was decided meanwhile", "check cuanta runs show")
        self._ledger.set_routing_accepted(run_id, False)
        return trial

    def git_present(self) -> bool:
        return self._storage.exists(".git")

    def _gitdir(self) -> str:
        if self._storage.is_dir(".git"):
            return ".git"
        gitdir = gitdir_from_file(self._storage.read_text(".git") or "")
        return Path(gitdir).as_posix() if gitdir else ""

    def current_branch(self) -> str:
        gitdir = self._gitdir()
        if not gitdir:
            return ""
        return branch_from_head(self._storage.read_text(f"{gitdir}/HEAD") or "")

    def index(self) -> dict[str, IndexEntry] | None:
        gitdir = self._gitdir()
        data = self._storage.read_bytes(f"{gitdir}/index") if gitdir else None
        return parse_index(data) if data is not None else None

    def _clean(self, trial: Trial, change: TrialChange, entry: IndexEntry) -> bool:
        if entry.matches(change.base_size, change.base_mtime_ns):
            return True
        base = self._storage.read_bytes(f"{trial_folder(trial.run_id)}/{BASE_DIR}/{change.path}")
        if base is None:
            return False
        return entry.sha in {blob_id(base), blob_id(base.replace(b"\r\n", b"\n"))}

    def uncommitted(
        self, trial: Trial, index: Mapping[str, IndexEntry] | None
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if index is None:
            return (), ()
        edited: list[str] = []
        untracked: list[str] = []
        for change in trial.changes:
            entry = index.get(change.path)
            if entry is None:
                if change.kind is ChangeKind.MODIFIED:
                    untracked.append(change.path)
            elif change.kind is ChangeKind.ADDED or not self._clean(trial, change, entry):
                edited.append(change.path)
        return tuple(edited), tuple(untracked)

    def pathspec(self, trial: Trial, index: Mapping[str, IndexEntry] | None) -> tuple[str, ...]:
        return tuple(
            change.path
            for change in trial.changes
            if index is None or change.kind is not ChangeKind.DELETED or change.path in index
        )

    def handoff(self, run_id: str, workflow: Workflow, shell: Shell) -> Handoff | None:
        summary = self.summary(run_id)
        if summary is None or not summary.trial.applicable or summary.outcome == REJECTED:
            return None
        trial = summary.trial
        index = self.index()
        paths = self.pathspec(trial, index)
        names = "".join(f"{path}\0" for path in paths).encode("utf-8", "surrogateescape")
        target = f"{trial_folder(run_id)}/{PATHS_FILE}"
        if self._storage.read_bytes(target) != names:
            self._storage.write_bytes(target, names)
        edited, untracked = self.uncommitted(trial, index)
        kind = change_type(trial.task_type, trial.paths)
        return handoff(
            workflow,
            kind,
            trial.subject or commit_subject(kind, trial.what, ""),
            run_id,
            str(self._storage.root),
            shell,
            self.current_branch(),
            not summary.applied_at,
            self.git_present() and bool(paths),
            edited,
            untracked,
        )


def _shown(paths: tuple[str, ...] | list[str]) -> str:
    listed = list(paths)
    shown = ", ".join(listed[:SHOWN_PATHS])
    return f"{shown}, …" if len(listed) > SHOWN_PATHS else shown
