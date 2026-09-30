from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
from collections.abc import Callable
from itertools import count
from pathlib import Path
from typing import Any

import pytest

from cuanta.adapters.engines.codex import CodexEngine
from cuanta.adapters.instinct.heuristic import HeuristicInstinct
from cuanta.adapters.storage.capsule_store import FileCapsuleStore
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.adapters.system.sandbox import LocalSandbox
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.cross_engine import CrossEnginePipeline
from cuanta.application.engine_run import EngineLauncher
from cuanta.application.instinct import DecisionMaker
from cuanta.application.mandate import MandateService
from cuanta.application.mandate_flow import MandateFlow, MandateOptions
from cuanta.application.progress import RecordingSink
from cuanta.application.sandbox import SandboxRunner, TrialRecorder
from cuanta.application.timing import PhaseRecorder
from cuanta.application.trials import TrialStore, parse_trial
from cuanta.domain.detection import Stack
from cuanta.domain.engine import EngineEvent, EngineOutcome, EngineRequest, RunResult
from cuanta.domain.errors import DomainFailure, EnvironmentFailure
from cuanta.domain.handoff import Workflow
from cuanta.domain.ledger import Run
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import Message
from cuanta.domain.outcomes import ACCEPTED, REJECTED
from cuanta.domain.progress import Note
from cuanta.domain.sandbox import (
    GIT_CEILING_ENV,
    NODE_MODULES_DENIED,
    NODE_MODULES_NOTE,
    SANDBOX_MODE,
    STATE_ROOT_ENV,
    SandboxLaunch,
    sandbox_launch,
)
from cuanta.domain.shells import Shell
from cuanta.domain.time_anatomy import analyze_time
from cuanta.ports.ledger import EventQuery
from cuanta.ports.sandbox import SandboxCopy
from tests.fakes import FakeRunner, FakeStream
from tests.unit.test_engine_profiles import all_roles

TEMPLATE = "```\n=== REQUEST ===\n```\n"
FEATURE = MandateRequest(
    type="feature", what="Add a sitemap route", why="SEO", tests="builds", out_of_scope="x"
)
REPORT = "## SUMMARY\nDone.\n\n## COMMIT PROPOSAL\n`feat(seo): add sitemap route`\n"


class EditingEngine:
    def __init__(self, edits: Callable[[Path], None], name: str = "claude") -> None:
        self._edits = edits
        self._name = name
        self.requests: list[EngineRequest] = []

    @property
    def name(self) -> str:
        return self._name

    def available(self) -> bool:
        return True

    def version(self) -> str:
        return "x"

    def missing_flags(self) -> tuple[str, ...]:
        return ()

    def cancel(self) -> None:
        return None

    def command(self, request: EngineRequest) -> list[str]:
        return [self._name]

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        self.requests.append(request)
        self._edits(Path(request.cwd))
        result = RunResult(True, "success", 0.12, 3, "s", (), REPORT)
        on_event(result)
        return EngineOutcome(0, result, 0)


def _keys(sink: RecordingSink) -> list[str]:
    return [
        event.message.key
        for event in sink.events
        if isinstance(event, Note) and event.message is not None
    ]


def _write(root: Path, relative: str, data: bytes) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "shop"
    _write(project, "src/app.ts", b"export const a = 1\r\n")
    _write(project, "src/old.ts", b"gone\n")
    _write(project, "public/logo.png", b"\x89PNG\x00old")
    _write(project, "docs/MANDATE_TEMPLATE.md", TEMPLATE.encode())
    _write(project, "node_modules/lib/index.js", b"module.exports = 1\n")
    _write(project, ".git/HEAD", b"ref: refs/heads/master\n")
    return project


def _edits(root: Path) -> None:
    _write(root, "src/app.ts", b"export const a = 2\r\n")
    _write(root, "src/sitemap.ts", b"export default []\n")
    (root / "src" / "old.ts").unlink()
    _write(root, "public/logo.png", b"\x89PNG\x00new")
    _write(root, "node_modules/.cache/x", b"cache")


class Harness:
    def __init__(self, tmp_path: Path, engine: EditingEngine) -> None:
        self.project = _project(tmp_path)
        self.ledger = MemoryLedger()
        self.clock = FixedClock()
        self.storage = LocalWorkspace(self.project)
        self.sandbox = LocalSandbox(parents=(tmp_path / "temp",))
        self.engine = engine
        self.ids = count(1)
        self.recorder = TrialRecorder(self.sandbox, self.storage, self.clock.now_iso)
        self.runner = SandboxRunner(self.sandbox, self.recorder, self.ledger, self.project)
        self.store = TrialStore(self.storage, self.ledger, self.clock.now_iso)

    def launcher(self, engine: object) -> EngineLauncher:
        return EngineLauncher(
            self.engine,
            self.ledger,
            self.clock,
            lambda: f"RUN{next(self.ids)}",
            lambda size: b"\x01" * size,
            "shop",
            4318,
            None,
        )

    def flow(self, copy: SandboxCopy, launch: SandboxLaunch) -> MandateFlow:
        decisions = DecisionMaker(HeuristicInstinct(), self.ledger, self.clock.now_iso)
        service = MandateService(
            self.storage,
            self.ledger,
            decisions,
            self.clock.now_iso,
            scanned=LocalWorkspace(copy.root),
        )
        return MandateFlow(
            service,
            lambda _: self.engine,
            self.launcher,
            Stack,
            lambda _: ({}, None),
            str(copy.root),
            self.engine.name,
            0.0,
            new_run_id=lambda: f"RUN{next(self.ids)}",
            sandbox=launch,
        )

    def run(self, keep: bool = False, request: MandateRequest = FEATURE) -> RecordingSink:
        sink = RecordingSink()
        result = self.runner.run_mandate(
            self.flow,
            request,
            0,
            MandateOptions(simple=True, keep_copy=keep),
            sink,
            payload=lambda report: {"run_id": report.run.id},
        )
        self.result = result
        return sink


def test_learning_runs_after_saved_trial_and_before_sandbox_removal(tmp_path: Path) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))
    learned: list[str] = []

    def learn(copy: SandboxCopy, run_id: str) -> None:
        assert copy.root.is_dir()
        trial = harness.store.load(run_id)
        assert trial is not None and trial.paths
        assert harness.storage.read_text(f".cuanta/trials/{run_id}/report.md") == REPORT
        learned.append(run_id)

    harness.runner = SandboxRunner(
        harness.sandbox, harness.recorder, harness.ledger, harness.project, after_record=learn
    )
    harness.run()
    assert harness.result.trial is not None
    assert learned == [harness.result.trial.run_id]
    assert not Path(harness.result.copy_root).exists()


def test_cleanup_is_deferred_until_result_is_shown_and_after_images_survive(tmp_path: Path) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))
    phases: list[str] = []

    def cleanup(copy: SandboxCopy, run_id: str) -> Callable[[], None]:
        assert harness.store.load(run_id) is not None
        phases.append("captured")

        def displayed() -> None:
            phases.append("shown")
            assert harness.sandbox.remove(copy)

        return displayed

    harness.runner = SandboxRunner(
        harness.sandbox, harness.recorder, harness.ledger, harness.project, cleanup=cleanup
    )
    harness.run()
    result = harness.result
    assert result.trial is not None and result.trial.changes
    saved = harness.storage.read_bytes(f".cuanta/trials/{result.trial.run_id}/files/src/app.ts")
    assert saved == b"export const a = 2\r\n"
    assert phases == ["captured"] and Path(result.copy_root).exists()
    result.shown()
    assert phases == ["captured", "shown"] and not Path(result.copy_root).exists()
    assert (
        harness.storage.read_bytes(f".cuanta/trials/{result.trial.run_id}/files/src/app.ts")
        == saved
    )


def test_kept_copy_never_schedules_cleanup(tmp_path: Path) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))

    def forbidden(copy: SandboxCopy, run_id: str) -> Callable[[], None]:
        raise AssertionError(f"unexpected cleanup: {copy.root} {run_id}")

    harness.runner = SandboxRunner(
        harness.sandbox, harness.recorder, harness.ledger, harness.project, cleanup=forbidden
    )
    harness.run(keep=True)
    assert harness.result.after_shown is None
    assert Path(harness.result.copy_root).is_dir()


def test_sandbox_timing_contains_copy_capture_guards_and_removal(tmp_path: Path) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))
    harness.runner = SandboxRunner(
        harness.sandbox,
        harness.recorder,
        harness.ledger,
        harness.project,
        timing=PhaseRecorder(harness.clock, harness.ledger),
    )
    harness.run()
    trial = harness.result.trial
    assert trial is not None
    run = harness.ledger.get_run(trial.run_id)
    assert run is not None
    report = analyze_time(run, harness.ledger.events(EventQuery(run_id=run.id)))
    phases = {row.phase: row for row in report.phases}
    assert phases["sandbox_copy"].seconds == 0.5
    assert phases["after_image"].seconds == 0.5
    assert phases["copy_removal"].seconds == 0.5
    assert phases["snapshots_guards"].seconds is not None
    assert report.wall_seconds is not None and report.wall_seconds > 2


def test_a_sandbox_run_edits_only_the_copy_and_stores_a_trial(tmp_path: Path) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))
    harness.run()
    project = harness.project
    result = harness.result
    assert result.trial is not None and result.removed
    assert (project / "src" / "app.ts").read_bytes() == b"export const a = 1\r\n"
    assert (project / "src" / "old.ts").is_file()
    assert not (project / "src" / "sitemap.ts").exists()
    assert not (project / "node_modules" / ".cache").exists()
    trial = result.trial
    assert {change.path: change.kind.value for change in trial.changes} == {
        "public/logo.png": "modified",
        "src/app.ts": "modified",
        "src/old.ts": "deleted",
        "src/sitemap.ts": "added",
    }
    assert trial.subject == "feat(seo): add sitemap route"
    folder = project / ".cuanta" / "trials" / trial.run_id
    patch = (folder / "change.patch").read_bytes()
    assert b"-export const a = 1\r\n+export const a = 2\r\n" in patch
    assert b"GIT binary patch" in patch
    assert (folder / "files" / "src" / "sitemap.ts").read_bytes() == b"export default []\n"
    assert (folder / "base" / "src" / "old.ts").read_bytes() == b"gone\n"
    assert (folder / "paths.nul").read_bytes().count(b"\0") == 4
    assert (folder / "commit.txt").read_text(encoding="utf-8") == "feat(seo): add sitemap route\n"
    assert (folder / "report.md").read_text(encoding="utf-8").startswith("## SUMMARY")
    metrics = json.loads((folder / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["run_id"] == trial.run_id
    assert metrics["trial"]["by_kind"] == {"added": 1, "modified": 2, "deleted": 1}
    assert metrics["sandbox"]["linked"] == ["node_modules"]
    run = harness.ledger.get_run(trial.run_id)
    assert run is not None and run.mode == SANDBOX_MODE


def test_unreadable_after_images_preserve_run_metrics_without_an_applicable_trial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cuanta.cli.commands.mandate import guard_tripped, sandbox_payload

    harness = Harness(tmp_path, EditingEngine(_edits))

    def unreadable(copy: SandboxCopy) -> dict[str, str]:
        raise PermissionError("engine-created file is not readable")

    monkeypatch.setattr(harness.sandbox, "manifest", unreadable)
    harness.run()
    result = harness.result
    assert result.record_error == "PermissionError" and result.trial is None
    assert not result.removed and Path(result.copy_root).is_dir()
    assert result.report is not None and result.report.run.cost_usd == 0.12
    assert guard_tripped(result)
    assert sandbox_payload(result)["record_error"] == "PermissionError"
    assert harness.store.load(result.report.run.id) is None


def test_the_engine_gets_the_copy_state_root_ceiling_and_node_modules_rules(
    tmp_path: Path,
) -> None:
    engine = EditingEngine(_edits)
    harness = Harness(tmp_path, engine)
    sink = harness.run(keep=True)
    request = engine.requests[0]
    assert Path(request.cwd) == Path(harness.result.copy_root)
    assert request.env[STATE_ROOT_ENV] == str(harness.project.resolve())
    assert request.env[GIT_CEILING_ENV] == str(Path(request.cwd).parent)
    assert set(NODE_MODULES_DENIED) <= set(request.disallowed_tools)
    assert "Bash(git *)" in request.disallowed_tools
    assert request.temporary_copy
    assert NODE_MODULES_NOTE in request.prompt
    assert not harness.result.removed
    assert Path(harness.result.copy_root).is_dir()
    keys = _keys(sink)
    assert "sandbox.copied" in keys and "sandbox.kept" in keys and "sandbox.trial" in keys


def test_codex_in_a_sandbox_copy_skips_the_git_check_without_node_modules_denies(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(streams={"codex": FakeStream([])})
    codex = CodexEngine(runner)
    harness = Harness(tmp_path, EditingEngine(_edits))
    copy = harness.sandbox.create(harness.project)
    launch = sandbox_launch(str(harness.project), str(copy.slot), linked=True)
    flow = harness.flow(copy, launch)
    flow._engines = lambda _: codex
    prepared = flow.prepare(FEATURE, 0, MandateOptions(engine="codex", simple=True))
    command = codex.command(prepared.launcher.request(prepared.spec, "R", "t", None))
    assert "--skip-git-repo-check" in command
    assert prepared.spec.mode == SANDBOX_MODE
    assert not set(NODE_MODULES_DENIED) & set(prepared.spec.disallowed_tools)
    assert NODE_MODULES_NOTE in prepared.spec.prompt
    assert dict(prepared.spec.env)[STATE_ROOT_ENV] == str(harness.project)
    assert harness.sandbox.remove(copy)


def test_apply_writes_after_images_and_records_acceptance_once(tmp_path: Path) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))
    harness.run()
    trial = harness.result.trial
    assert trial is not None
    summary = harness.store.summary(trial.run_id)
    assert summary is not None and summary.pending and summary.drift == ()
    harness.store.apply(trial.run_id)
    project = harness.project
    assert (project / "src" / "app.ts").read_bytes() == b"export const a = 2\r\n"
    assert (project / "src" / "sitemap.ts").read_bytes() == b"export default []\n"
    assert not (project / "src" / "old.ts").exists()
    assert (project / "public" / "logo.png").read_bytes() == b"\x89PNG\x00new"
    run = harness.ledger.get_run(trial.run_id)
    assert run is not None and run.outcome == ACCEPTED and run.outcome_at
    applied = harness.store.summary(trial.run_id)
    assert applied is not None and applied.applied_at and not applied.can_apply
    with pytest.raises(DomainFailure, match="already applied"):
        harness.store.apply(trial.run_id)
    with pytest.raises(DomainFailure, match="already applied"):
        harness.store.discard(trial.run_id)


def test_an_accepted_run_can_still_be_applied_later(tmp_path: Path) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))
    harness.run()
    trial = harness.result.trial
    assert trial is not None
    harness.ledger.set_run_outcome(trial.run_id, ACCEPTED, "2026-09-26T09:00:00")
    summary = harness.store.summary(trial.run_id)
    assert summary is not None and summary.can_apply and not summary.pending
    handoff = harness.store.handoff(trial.run_id, Workflow.TRUNK, Shell.BASH)
    assert handoff is not None
    assert f"cuanta runs apply {trial.run_id} --yes" in handoff.commands
    with pytest.raises(DomainFailure, match="already accepted"):
        harness.store.discard(trial.run_id)
    harness.store.apply(trial.run_id)
    assert (harness.project / "src" / "sitemap.ts").is_file()
    run = harness.ledger.get_run(trial.run_id)
    assert run is not None and run.outcome_at == "2026-09-26T09:00:00"


def test_a_case_only_rename_applies(tmp_path: Path) -> None:
    def rename(root: Path) -> None:
        (root / "src" / "app.ts").rename(root / "src" / "App.ts")
        _write(root, "src/App.ts", b"export const App = 1\r\n")

    harness = Harness(tmp_path, EditingEngine(rename))
    harness.run()
    trial = harness.result.trial
    assert trial is not None
    kinds = {change.path: change.kind.value for change in trial.changes}
    assert kinds == {"src/App.ts": "added", "src/app.ts": "deleted"}
    harness.store.apply(trial.run_id)
    names = sorted(path.name for path in (harness.project / "src").iterdir())
    assert "App.ts" in names and "app.ts" not in names
    assert (harness.project / "src" / "App.ts").read_bytes() == b"export const App = 1\r\n"


@pytest.mark.parametrize(
    ("relative", "data"),
    [("src/app.ts", b"user edit\n"), ("src/sitemap.ts", b"user file\n"), ("src/old.ts", None)],
)
def test_apply_refuses_drift_and_leaves_the_project_untouched(
    tmp_path: Path, relative: str, data: bytes | None
) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))
    harness.run()
    trial = harness.result.trial
    assert trial is not None
    target = harness.project / relative
    if data is None:
        target.unlink()
    else:
        _write(harness.project, relative, data)
    snapshot = {
        path: (harness.project / path).read_bytes() for path in ("src/app.ts", "public/logo.png")
    }
    with pytest.raises(DomainFailure, match="changed since the copy") as caught:
        harness.store.apply(trial.run_id)
    assert relative in caught.value.message
    assert {path: (harness.project / path).read_bytes() for path in snapshot} == snapshot
    run = harness.ledger.get_run(trial.run_id)
    assert run is not None and run.outcome == ""


def test_apply_refuses_damaged_images_and_restores_after_a_failed_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))
    harness.run()
    trial = harness.result.trial
    assert trial is not None
    image = harness.project / ".cuanta" / "trials" / trial.run_id / "files" / "src" / "app.ts"
    original = image.read_bytes()
    image.write_bytes(b"tampered")
    with pytest.raises(DomainFailure, match="missing or damaged"):
        harness.store.apply(trial.run_id)
    image.write_bytes(original)
    calls = count()
    real = LocalWorkspace.write_bytes

    def flaky(
        self: LocalWorkspace, relative: str, content: bytes, executable: bool = False
    ) -> None:
        if next(calls) == 1:
            raise OSError("disk full")
        real(self, relative, content, executable)

    monkeypatch.setattr(LocalWorkspace, "write_bytes", flaky)
    with pytest.raises(OSError, match="disk full"):
        harness.store.apply(trial.run_id)
    monkeypatch.setattr(LocalWorkspace, "write_bytes", real)
    assert (harness.project / "src" / "app.ts").read_bytes() == b"export const a = 1\r\n"
    assert (harness.project / "src" / "old.ts").read_bytes() == b"gone\n"
    assert not (harness.project / "src" / "sitemap.ts").exists()


def test_apply_verifies_the_result_and_rolls_back_a_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))
    harness.run()
    trial = harness.result.trial
    assert trial is not None
    real = LocalWorkspace.write_bytes

    def skewed(
        self: LocalWorkspace, relative: str, content: bytes, executable: bool | None = None
    ) -> None:
        extra = b"!" if relative == "src/sitemap.ts" else b""
        real(self, relative, content + extra, executable)

    monkeypatch.setattr(LocalWorkspace, "write_bytes", skewed)
    with pytest.raises(EnvironmentFailure, match="different from the run"):
        harness.store.apply(trial.run_id)
    monkeypatch.setattr(LocalWorkspace, "write_bytes", real)
    assert (harness.project / "src" / "app.ts").read_bytes() == b"export const a = 1\r\n"
    assert not (harness.project / "src" / "sitemap.ts").exists()


def test_discard_records_rejection_and_keeps_the_trial(tmp_path: Path) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))
    harness.run()
    trial = harness.result.trial
    assert trial is not None
    harness.store.discard(trial.run_id)
    run = harness.ledger.get_run(trial.run_id)
    assert run is not None and run.outcome == REJECTED
    assert harness.store.load(trial.run_id) is not None
    with pytest.raises(DomainFailure, match="was discarded"):
        harness.store.apply(trial.run_id)


def test_discard_needs_a_sandbox_run(tmp_path: Path) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))
    harness.ledger.add_run(Run(id="PLAIN", kind="mandate"))
    harness.ledger.add_run(Run(id="COPY", kind="mandate", mode=SANDBOX_MODE))
    with pytest.raises(DomainFailure, match="no isolated-copy changes"):
        harness.store.discard("PLAIN")
    assert harness.store.discard("COPY") is None
    run = harness.ledger.get_run("COPY")
    assert run is not None and run.outcome == REJECTED


def test_investigations_record_breaches_and_never_apply(tmp_path: Path) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))
    request = MandateRequest(
        type="investigation", what="audit", why="x", tests="t", out_of_scope="x"
    )
    sink = harness.run(request=request)
    trial = harness.result.trial
    assert trial is not None and trial.read_only_breach and trial.subject == ""
    assert "sandbox.read_only_breach" in _keys(sink)
    with pytest.raises(DomainFailure, match="read-only"):
        harness.store.apply(trial.run_id)
    assert harness.store.handoff(trial.run_id, Workflow.BRANCHES, Shell.BASH) is None


def test_a_run_without_changes_has_nothing_to_apply(tmp_path: Path) -> None:
    harness = Harness(tmp_path, EditingEngine(lambda root: None))
    sink = harness.run()
    trial = harness.result.trial
    assert trial is not None and trial.changes == ()
    keys = _keys(sink)
    assert "sandbox.no_changes" in keys
    with pytest.raises(DomainFailure, match="changed no files"):
        harness.store.apply(trial.run_id)


def test_project_edits_during_the_run_leave_the_base_missing(tmp_path: Path) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))
    project = harness.project

    def concurrent(root: Path) -> None:
        _edits(root)
        _write(project, "src/app.ts", b"user changed it meanwhile\n")

    harness.engine = EditingEngine(concurrent)
    sink = harness.run()
    trial = harness.result.trial
    assert trial is not None and trial.base_missing == ("src/app.ts",)
    patch = (project / ".cuanta" / "trials" / trial.run_id / "change.patch").read_bytes()
    assert b"src/app.ts" not in patch
    keys = _keys(sink)
    assert "sandbox.base_missing" in keys
    with pytest.raises(DomainFailure, match="changed in the project while the run worked"):
        harness.store.apply(trial.run_id)


def test_a_write_through_a_node_modules_hard_link_is_flagged(tmp_path: Path) -> None:
    def tamper(root: Path) -> None:
        with open(root / "node_modules" / "lib" / "index.js", "ab") as handle:
            handle.write(b"// patched\n")

    harness = Harness(tmp_path, EditingEngine(tamper))
    sink = harness.run()
    trial = harness.result.trial
    assert trial is not None
    assert trial.dependencies_changed == ("node_modules/lib/index.js",)
    assert "sandbox.dependencies_changed" in _keys(sink)
    assert not trial.applicable
    with pytest.raises(DomainFailure, match="node_modules") as caught:
        harness.store.apply(trial.run_id)
    assert "npm ci" in caught.value.hint


def test_edits_to_gitignored_files_are_reported(tmp_path: Path) -> None:
    def edit_ignored(root: Path) -> None:
        _write(root, ".env.local", b"SECRET=2\n")
        _write(root, "src/app.ts", b"changed\n")

    harness = Harness(tmp_path, EditingEngine(edit_ignored))
    _write(harness.project, ".gitignore", b".env*\n")
    _write(harness.project, ".env.local", b"SECRET=1\n")
    sink = harness.run()
    trial = harness.result.trial
    assert trial is not None
    assert trial.ignored_changes == (".env.local",)
    assert "sandbox.ignored_changes" in _keys(sink)
    assert ".env.local" not in trial.paths


def test_a_failed_recording_keeps_the_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))

    def broken(*_: object, **__: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(TrialRecorder, "record", broken)
    sink = RecordingSink()
    result = harness.runner.run_mandate(harness.flow, FEATURE, 0, MandateOptions(simple=True), sink)
    assert result.record_error == "OSError" and result.trial is None and not result.removed
    assert result.report is not None and result.report.run.end_reason == "error_sandbox_record"
    assert "sandbox.kept" in _keys(sink)
    kept = next(tmp_path.glob("temp/cuanta-sandbox/*/shop"))
    assert (kept / "src" / "sitemap.ts").is_file()


def test_a_run_that_never_launched_leaves_no_copy(tmp_path: Path) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))
    request = MandateRequest(type="feature", what="", why="", out_of_scope="")
    with pytest.raises(DomainFailure):
        harness.runner.run_mandate(
            harness.flow, request, 0, MandateOptions(simple=True), RecordingSink()
        )
    assert not any(tmp_path.glob("temp/cuanta-sandbox/*/shop"))


def test_handoff_follows_the_workflow_branch_and_pending_state(tmp_path: Path) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))
    harness.run()
    trial = harness.result.trial
    assert trial is not None
    branches = harness.store.handoff(trial.run_id, Workflow.BRANCHES, Shell.CMD)
    assert branches is not None
    assert branches.branch == "feat/add-sitemap-route-run1"
    assert branches.current_branch == "master"
    assert branches.commands[0] == f'cd /d "{harness.project}"'
    assert f"cuanta runs apply {trial.run_id} --yes" in branches.commands
    harness.store.apply(trial.run_id)
    trunk = harness.store.handoff(trial.run_id, Workflow.TRUNK, Shell.BASH)
    assert trunk is not None and trunk.branch == ""
    assert not any("runs apply" in line or "switch" in line for line in trunk.commands)
    assert any(line.startswith("git --literal-pathspecs commit -F") for line in trunk.commands)
    assert trunk.chained == " && ".join(trunk.commands)


def test_rejected_runs_have_no_handoff(tmp_path: Path) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))
    harness.run()
    trial = harness.result.trial
    assert trial is not None
    harness.store.discard(trial.run_id)
    assert harness.store.handoff(trial.run_id, Workflow.BRANCHES, Shell.BASH) is None


def test_handoff_reads_worktree_heads_and_hides_git_without_a_repository(tmp_path: Path) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))
    harness.run()
    trial = harness.result.trial
    assert trial is not None
    project = harness.project
    (project / ".git" / "HEAD").unlink()
    (project / ".git").rmdir()
    plain = harness.store.handoff(trial.run_id, Workflow.BRANCHES, Shell.BASH)
    assert plain is not None
    assert not any(line.startswith("git ") for line in plain.commands)
    _write(project, "repo/.git/worktrees/w/HEAD", b"ref: refs/heads/feature/x\n")
    _write(project, ".git", b"gitdir: repo/.git/worktrees/w\n")
    assert harness.store.current_branch() == "feature/x"


def test_parse_trial_rejects_malformed_documents() -> None:
    assert parse_trial({}) is None
    assert parse_trial({"run_id": "R", "changes": "no"}) is None
    parsed = parse_trial(
        {
            "run_id": "R",
            "changes": [
                {"path": "a", "kind": "added", "after": "1", "added": 2},
                {"path": "b", "kind": "weird"},
                "junk",
                {"kind": "added"},
            ],
            "kept": True,
            "dependencies_changed_count": True,
        }
    )
    assert parsed is not None
    assert [change.path for change in parsed.changes] == ["a"]
    assert parsed.kept and parsed.dependencies_changed_count == 0


def test_cross_engine_roles_run_in_the_copy_and_leave_one_trial(tmp_path: Path) -> None:
    runner = FakeRunner(streams={"codex": FakeStream([])})
    codex = CodexEngine(runner)
    harness = Harness(tmp_path, EditingEngine(_edits))
    engine_launcher = EngineLauncher(
        codex,
        harness.ledger,
        harness.clock,
        lambda: f"STEP{next(harness.ids)}",
        lambda size: b"\x01" * size,
        "shop",
        4318,
        None,
    )

    def pipeline(
        copy: SandboxCopy, launch: SandboxLaunch, checkpoint: Callable[[], Message | None]
    ) -> CrossEnginePipeline:
        return CrossEnginePipeline(
            lambda _: engine_launcher,
            tuple,
            FileCapsuleStore(tmp_path / "caps"),
            str(copy.root),
            0.0,
            max_turns=1,
            sandbox=launch,
            checkpoint=checkpoint,
        )

    result = harness.runner.run_cross(pipeline, FEATURE, all_roles(), RecordingSink(), False)
    assert result.cross is not None and result.cross.ok
    assert all("--skip-git-repo-check" in command for command in runner.calls)
    assert all(Path(str(cwd)) == Path(result.copy_root) for cwd in runner.cwds)
    assert result.trial is not None and result.trial.run_id == result.cross.steps[0].run_id
    steps = [harness.ledger.get_run(step.run_id) for step in result.cross.steps]
    assert all(run is not None and run.mode == SANDBOX_MODE for run in steps)
    assert result.removed
    assert all(NODE_MODULES_NOTE in (stdin or "") for stdin in runner.stdins)


class TamperingEngine(EditingEngine):
    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        with open(Path(request.cwd) / "node_modules" / "lib" / "index.js", "ab") as handle:
            handle.write(b"// role edit\n")
        return super().run(request, on_event)


def _cross(harness: Harness, tmp_path: Path, engine: EditingEngine) -> CrossRunner:
    launcher = EngineLauncher(
        engine,
        harness.ledger,
        harness.clock,
        lambda: f"STEP{next(harness.ids)}",
        lambda size: b"\x01" * size,
        "shop",
        4318,
        None,
    )

    def pipeline(
        copy: SandboxCopy, launch: SandboxLaunch, checkpoint: Callable[[], Message | None]
    ) -> CrossEnginePipeline:
        return CrossEnginePipeline(
            lambda _: launcher,
            tuple,
            FileCapsuleStore(tmp_path / "caps"),
            str(copy.root),
            0.0,
            max_turns=1,
            sandbox=launch,
            checkpoint=checkpoint,
        )

    return pipeline


CrossRunner = Callable[
    [SandboxCopy, SandboxLaunch, Callable[[], Message | None]], CrossEnginePipeline
]


def test_the_node_modules_check_stops_a_cross_engine_run_after_the_first_role(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))
    engine = TamperingEngine(lambda root: None)
    sink = RecordingSink()
    result = harness.runner.run_cross(
        _cross(harness, tmp_path, engine), FEATURE, all_roles("claude"), sink, False
    )
    assert result.cross is not None and not result.cross.ok
    assert len(result.cross.steps) == 1
    assert result.cross.stopped is not None
    assert result.cross.stopped.key == "sandbox.dependencies_changed"
    assert result.trial is not None and result.trial.guard_tripped


def test_an_interrupted_cross_run_records_what_it_did_and_keeps_nothing_hidden(
    tmp_path: Path,
) -> None:
    class Interrupting(EditingEngine):
        def run(
            self, request: EngineRequest, on_event: Callable[[EngineEvent], None]
        ) -> EngineOutcome:
            if len(self.requests) == 1:
                raise KeyboardInterrupt
            return super().run(request, on_event)

    harness = Harness(tmp_path, EditingEngine(_edits))
    engine = Interrupting(_edits)
    sink = RecordingSink()
    with pytest.raises(KeyboardInterrupt):
        harness.runner.run_cross(
            _cross(harness, tmp_path, engine), FEATURE, all_roles("claude"), sink, False
        )
    trials = list((harness.project / ".cuanta" / "trials").iterdir())
    assert len(trials) == 1
    assert (trials[0] / "report.md").is_file()
    assert "sandbox.trial" in _keys(sink)


def _git(root: Path, *args: str) -> None:
    import os
    import subprocess

    subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args],
        cwd=root,
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_handoff_warns_about_uncommitted_edits_and_skips_untracked_deletions(
    tmp_path: Path,
) -> None:
    def edits(root: Path) -> None:
        _write(root, "src/app.ts", b"agent edit\n")
        (root / "notes.md").unlink()
        _write(root, "src/new.ts", b"new\n")

    harness = Harness(tmp_path, EditingEngine(edits))
    project = harness.project
    (project / ".git" / "HEAD").unlink()
    (project / ".git").rmdir()
    _git(project, "init", "-q")
    _git(project, "add", "src/app.ts", "src/old.ts")
    _write(project, "src/app.ts", b"user work in progress\n")
    _write(project, "notes.md", b"untracked notes\n")
    harness.run()
    trial = harness.result.trial
    assert trial is not None
    assert {"src/app.ts", "notes.md", "src/new.ts"} <= set(trial.paths)
    handoff = harness.store.handoff(trial.run_id, Workflow.TRUNK, Shell.BASH)
    assert handoff is not None
    assert handoff.uncommitted == ("src/app.ts",)
    folder = project / ".cuanta" / "trials" / trial.run_id
    listed = (folder / "paths.nul").read_bytes().split(b"\0")
    assert b"notes.md" not in listed
    assert b"src/new.ts" in listed and b"src/app.ts" in listed


def test_an_interrupt_inside_the_first_cross_role_still_records_and_checks(
    tmp_path: Path,
) -> None:
    class FirstRoleInterrupt(EditingEngine):
        def run(
            self, request: EngineRequest, on_event: Callable[[EngineEvent], None]
        ) -> EngineOutcome:
            root = Path(request.cwd)
            _write(root, "src/app.ts", b"half done\n")
            with open(root / "node_modules" / "lib" / "index.js", "ab") as handle:
                handle.write(b"// role edit\n")
            raise KeyboardInterrupt

    harness = Harness(tmp_path, EditingEngine(_edits))
    sink = RecordingSink()
    with pytest.raises(KeyboardInterrupt):
        harness.runner.run_cross(
            _cross(harness, tmp_path, FirstRoleInterrupt(lambda root: None)),
            FEATURE,
            all_roles("claude"),
            sink,
            False,
        )
    trials = list((harness.project / ".cuanta" / "trials").iterdir())
    assert len(trials) == 1
    stored = harness.store.load(trials[0].name)
    assert stored is not None and stored.guard_tripped
    assert "src/app.ts" in stored.paths
    assert "sandbox.dependencies_changed" in _keys(sink)


def test_a_preview_of_a_sandbox_run_shows_the_isolated_launch(tmp_path: Path) -> None:
    runner = FakeRunner(streams={"codex": FakeStream([])})
    codex = CodexEngine(runner)
    harness = Harness(tmp_path, EditingEngine(_edits))
    _write(harness.project, "package.json", b"{}")
    decisions = DecisionMaker(HeuristicInstinct(), harness.ledger, harness.clock.now_iso)
    service = MandateService(harness.storage, harness.ledger, decisions, harness.clock.now_iso)
    flow = MandateFlow(
        service,
        lambda _: codex,
        harness.launcher,
        lambda: Stack(manifests=("package.json",)),
        lambda _: ({}, None),
        str(harness.project),
        "codex",
        0.0,
    )
    options = MandateOptions(engine="codex", simple=True, sandbox=True)
    prepared = flow.prepare(FEATURE, 0, options, preview=True)
    assert "--skip-git-repo-check" in prepared.composed.command
    assert NODE_MODULES_NOTE in prepared.composed.prompt
    plain = flow.prepare(FEATURE, 0, MandateOptions(engine="codex", simple=True), preview=True)
    assert "--skip-git-repo-check" not in plain.composed.command


def test_a_kept_copy_is_marked_so_the_sweep_leaves_it(tmp_path: Path) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))
    harness.run(keep=True)
    slot = Path(harness.result.copy_root).parent
    marker = json.loads((slot / "sandbox.json").read_text(encoding="utf-8"))
    assert marker["kept"] is True


def test_links_and_dependencies_outside_the_project_are_announced(tmp_path: Path) -> None:
    outside = tmp_path / "shared"
    _write(outside, "lib.ts", b"x\n")
    harness = Harness(tmp_path, EditingEngine(lambda root: None))
    _write(tmp_path, "node_modules/hoisted/index.js", b"x\n")
    link = harness.project / "shared-link"
    if sys.platform == "win32":
        winapi: Any = importlib.import_module("_winapi")
        winapi.CreateJunction(str(outside), str(link))
    else:
        os.symlink(outside, link, target_is_directory=True)
    (harness.project / ".git" / "HEAD").unlink()
    (harness.project / ".git").rmdir()
    sink = harness.run()
    keys = _keys(sink)
    assert "sandbox.skipped_links" in keys
    assert "sandbox.outside_dependencies" in keys


def test_editable_python_installs_point_at_the_copy(tmp_path: Path) -> None:
    engine = EditingEngine(lambda root: None)
    harness = Harness(tmp_path, engine)
    source = harness.project / "src"
    site = "Lib/site-packages" if sys.platform == "win32" else "lib/python3.12/site-packages"
    _write(harness.project, f".venv/{site}/_editable_impl_shop.pth", f"{source}\n".encode())
    harness.run()
    request = engine.requests[0]
    expected = str(Path(request.cwd) / "src")
    assert request.env["PYTHONPATH"].split(os.pathsep)[0] == expected


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_the_feature_patch_applies_with_git_to_the_project_as_it_was(tmp_path: Path) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))
    harness.run()
    trial = harness.result.trial
    assert trial is not None
    check = tmp_path / "check"
    for relative in ("src/app.ts", "src/old.ts", "public/logo.png"):
        _write(check, relative, (harness.project / relative).read_bytes())
    _git(check, "init", "-q")
    patch = harness.project / ".cuanta" / "trials" / trial.run_id / "change.patch"
    _git(check, "apply", "--check", str(patch))
    _git(check, "apply", str(patch))
    folder = harness.project / ".cuanta" / "trials" / trial.run_id / "files"
    for change in trial.changes:
        target = check / change.path
        if change.after is None:
            assert not target.exists()
        else:
            assert target.read_bytes() == (folder / change.path).read_bytes()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_handoff_flags_untracked_edits_but_not_crlf_checkouts(tmp_path: Path) -> None:
    def edits(root: Path) -> None:
        _write(root, "src/app.ts", b"agent edit\r\n")
        _write(root, "notes.md", b"agent notes\n")

    harness = Harness(tmp_path, EditingEngine(edits))
    project = harness.project
    (project / ".git" / "HEAD").unlink()
    (project / ".git").rmdir()
    _write(project, "src/app.ts", b"export const a = 1\n")
    _git(project, "init", "-q")
    _git(project, "add", "src/app.ts")
    _write(project, "src/app.ts", b"export const a = 1\r\n")
    _write(project, "notes.md", b"my own untracked notes\n")
    harness.run()
    trial = harness.result.trial
    assert trial is not None
    handoff = harness.store.handoff(trial.run_id, Workflow.TRUNK, Shell.BASH)
    assert handoff is not None
    assert handoff.uncommitted == ()
    assert handoff.untracked == ("notes.md",)


def test_a_run_that_changes_cuanta_settings_in_the_project_cannot_be_applied(
    tmp_path: Path,
) -> None:
    def tamper(root: Path) -> None:
        _edits(root)
        origin = Path(harness.engine.requests[-1].env[STATE_ROOT_ENV])
        _write(origin, ".cuanta/config.toml", b"[test]\ncommand = 'evil'\n")

    harness = Harness(tmp_path, EditingEngine(tamper))
    sink = harness.run()
    trial = harness.result.trial
    assert trial is not None
    assert trial.state_changed == (".cuanta/config.toml",)
    assert trial.guard_tripped and not trial.applicable
    assert "sandbox.state_changed" in _keys(sink)
    with pytest.raises(DomainFailure, match="cuanta's own files"):
        harness.store.apply(trial.run_id)
    stored = harness.store.load(trial.run_id)
    assert stored is not None and stored.state_changed_count == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_a_mode_only_change_is_recorded_and_applied(tmp_path: Path) -> None:
    def chmod(root: Path) -> None:
        os.chmod(root / "src" / "app.ts", 0o755)

    harness = Harness(tmp_path, EditingEngine(chmod))
    harness.run()
    trial = harness.result.trial
    assert trial is not None
    (change,) = trial.changes
    assert change.path == "src/app.ts" and change.executable and not change.base_executable
    patch = (harness.project / ".cuanta" / "trials" / trial.run_id / "change.patch").read_text(
        encoding="utf-8"
    )
    assert "old mode 100644\nnew mode 100755\n" in patch
    harness.store.apply(trial.run_id)
    assert os.access(harness.project / "src" / "app.ts", os.X_OK)


def test_results_other_sandbox_runs_store_meanwhile_do_not_block_the_run(
    tmp_path: Path,
) -> None:
    def concurrent(root: Path) -> None:
        _edits(root)
        origin = Path(harness.engine.requests[-1].env[STATE_ROOT_ENV])
        harness.ledger.add_run(Run(id="OTHER", kind="mandate", started_at="t", mode=SANDBOX_MODE))
        _write(origin, ".cuanta/trials/OTHER/trial.json", b"{}")
        _write(origin, ".cuanta/trials/FORGED/trial.json", b"{}")

    harness = Harness(tmp_path, EditingEngine(concurrent))
    harness.run()
    trial = harness.result.trial
    assert trial is not None
    assert trial.state_changed == (".cuanta/trials/FORGED/trial.json",)


def test_a_retried_record_ignores_the_run_own_partial_trial(tmp_path: Path) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))
    copy = harness.sandbox.create(harness.project)
    _write(harness.project, ".cuanta/trials/RUN9/trial.json", b"partial")
    try:
        assert harness.runner.checkpoint(copy, "RUN9") is None
        assert harness.runner.checkpoint(copy) is not None
    finally:
        assert harness.sandbox.remove(copy)


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_a_rolled_back_apply_restores_the_executable_bit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def chmod(root: Path) -> None:
        _write(root, "src/app.ts", b"export const a = 3\n")
        os.chmod(root / "src" / "app.ts", 0o755)

    harness = Harness(tmp_path, EditingEngine(chmod))
    harness.run()
    trial = harness.result.trial
    assert trial is not None
    real = LocalWorkspace.write_bytes
    writes: list[str] = []

    def skewed(
        self: LocalWorkspace, relative: str, content: bytes, executable: bool | None = None
    ) -> None:
        writes.append(relative)
        real(self, relative, content + (b"!" if len(writes) == 1 else b""), executable)

    monkeypatch.setattr(LocalWorkspace, "write_bytes", skewed)
    with pytest.raises(EnvironmentFailure, match="different from the run"):
        harness.store.apply(trial.run_id)
    monkeypatch.setattr(LocalWorkspace, "write_bytes", real)
    target = harness.project / "src" / "app.ts"
    assert target.read_bytes() == b"export const a = 1\r\n"
    assert not os.access(target, os.X_OK)


def test_apply_restores_the_project_when_a_reject_lands_meanwhile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, EditingEngine(_edits))
    harness.run()
    trial = harness.result.trial
    assert trial is not None
    real = harness.ledger.set_run_outcome

    def raced(run_id: str, outcome: str, at: str, reason: str = "") -> bool:
        real(run_id, REJECTED, at, "someone else")
        return False

    monkeypatch.setattr(harness.ledger, "set_run_outcome", raced)
    with pytest.raises(DomainFailure, match="discarded while it was being applied"):
        harness.store.apply(trial.run_id)
    assert (harness.project / "src" / "app.ts").read_bytes() == b"export const a = 1\r\n"
    assert not (harness.project / "src" / "sitemap.ts").exists()
    assert harness.store.applied_at(trial.run_id) == ""


def test_a_sandbox_run_stores_the_estimate_it_ran_with(tmp_path: Path) -> None:
    from cuanta.domain.estimates import RunEstimate

    harness = Harness(tmp_path, EditingEngine(_edits))
    sink = RecordingSink()
    options = MandateOptions(
        simple=True, budget_usd=0.75, estimate=RunEstimate("history", 0.3, 0.6, 4)
    )
    result = harness.runner.run_mandate(harness.flow, FEATURE, 0, options, sink)
    assert result.report is not None
    stored = harness.ledger.get_run(result.report.run.id)
    assert stored is not None
    assert (stored.estimate_low, stored.estimate_high, stored.estimate_samples) == (0.3, 0.6, 4)
    assert (stored.estimate_source, stored.cap_usd, stored.mode) == ("history", 0.75, "sandbox")
