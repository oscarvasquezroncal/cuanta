from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from cuanta.adapters.instinct.heuristic import HeuristicInstinct
from cuanta.adapters.storage.capsule_store import FileCapsuleStore
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.adapters.testing.pytest_runner import PytestRunner
from cuanta.application.affected import AffectedGateway
from cuanta.application.engine_run import EngineLauncher
from cuanta.application.gateway import RunGateway
from cuanta.application.instinct import DecisionMaker
from cuanta.application.mandate import MandateService
from cuanta.application.mandate_flow import MandateFlow, MandateOptions
from cuanta.application.progress import RecordingSink
from cuanta.application.timing import PhaseRecorder
from cuanta.domain.config import Config, layer_from_table, merge
from cuanta.domain.detection import Stack, VerifyTier
from cuanta.domain.ledger import Snapshot
from cuanta.domain.live_run import LiveRun
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import msg
from cuanta.domain.progress import started
from cuanta.domain.verification import (
    VerificationPolicy,
    VerificationResult,
    pytest_parallel,
    timing_message,
)
from cuanta.ports.system import Completed
from cuanta.tui.i18n import Catalog
from tests.fakes import FakeRunner
from tests.unit.test_launch_cost import SilentEngine
from tests.unit.test_time_anatomy import ManualClock


def flow_for(root: Path, verify: Callable[[str], str | None]) -> MandateFlow:
    clock, ledger = ManualClock(), MemoryLedger()
    engine = SilentEngine(0.01, "sonnet", name="claude")
    service = MandateService(
        LocalWorkspace(root),
        ledger,
        DecisionMaker(HeuristicInstinct(), ledger, clock.now_iso),
        clock.now_iso,
    )
    launcher = EngineLauncher(
        engine, ledger, clock, lambda: "R", lambda size: b"x" * size, "project", 4318, None
    )
    return MandateFlow(
        service,
        lambda name: engine,
        lambda selected: launcher,
        Stack,
        lambda run_id: ({}, None),
        str(root),
        "claude",
        0,
        final_suite=verify,
        new_run_id=lambda: "R",
        timing=PhaseRecorder(clock, ledger),
    )


def test_audit_never_spawns_a_suite(tmp_path: Path) -> None:
    calls: list[str] = []

    def verify(run_id: str) -> str:
        calls.append(run_id)
        return "green"

    flow = flow_for(tmp_path, verify)
    request = MandateRequest("investigation", "find the client", "where?", out_of_scope="edits")
    report = flow.run(flow.prepare(request, 0, MandateOptions(simple=True)), RecordingSink())
    assert calls == []
    assert report.tests == "skipped"


def test_report_is_saved_before_interrupted_verification(tmp_path: Path) -> None:
    marker = tmp_path / ".cuanta/runs/R/run.json"

    def verify(run_id: str) -> str:
        assert run_id == "R"
        assert marker.is_file()
        raise KeyboardInterrupt

    flow = flow_for(tmp_path, verify)
    request = MandateRequest(
        "feature", "add a title", "clarity", tests="title exists", out_of_scope="docs"
    )
    with pytest.raises(KeyboardInterrupt):
        flow.run(flow.prepare(request, 0, MandateOptions(simple=True)), RecordingSink())
    assert marker.is_file()


@pytest.mark.parametrize(
    ("mode", "change", "baseline", "expected"),
    [
        ("auto", True, True, ("tests/test_main.py", "--ff")),
        ("auto", False, True, None),
        ("affected", True, False, None),
        ("auto", True, False, ("-n", "auto")),
        ("full", False, True, ("-n", "auto")),
        ("off", True, True, None),
    ],
)
def test_verification_selection_and_fallback(
    tmp_path: Path, mode: str, change: bool, baseline: bool, expected: tuple[str, ...] | None
) -> None:
    (tmp_path / "main.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_main.py").write_text("def test_main(): pass\n", encoding="utf-8")
    runner, ledger = FakeRunner(), MemoryLedger()
    gateway = RunGateway(
        tmp_path,
        lambda name: PytestRunner(runner),
        ledger,
        FileCapsuleStore(tmp_path / ".cuanta/capsules"),
        FixedClock(),
        lambda: "T",
        tmp_path / ".cuanta/tmp",
    )
    affected = AffectedGateway(gateway, LocalWorkspace(tmp_path), ledger, frozenset(), lambda: {})
    if baseline:
        ledger.add_snapshots(
            [Snapshot("R", "start", name, digest) for name, digest in affected.hashes().items()]
        )
    if change:
        (tmp_path / "main.py").write_text("value = 2\n", encoding="utf-8")
    manifest = '[dependency-groups]\ndev = ["pytest-xdist>=3"]\n'
    result = affected.verify(
        Stack(test_runner="pytest"),
        Config(),
        VerifyTier.STRONG,
        "R",
        VerificationPolicy(mode, 60),
        pytest_parallel(manifest, "pytest"),
    )
    if expected is None:
        assert runner.calls == []
        assert result.status == "skipped"
    else:
        assert runner.calls[0][1 : 1 + len(expected)] == expected
        assert runner.envs[0] is not None
        assert runner.envs[0]["CUANTA_VERIFY_TIMEOUT_S"] == "60"


def test_timeout_verdict_is_inconclusive(tmp_path: Path) -> None:
    runner = FakeRunner(responses={"pytest": Completed(124, "", "timeout", 60)})
    gateway = RunGateway(
        tmp_path,
        lambda name: PytestRunner(runner),
        MemoryLedger(),
        FileCapsuleStore(tmp_path / ".cuanta/capsules"),
        FixedClock(),
        lambda: "T",
        tmp_path / ".cuanta/tmp",
    )
    result = gateway.run(
        Stack(test_runner="pytest"),
        Config(),
        VerifyTier.STRONG,
        env={"CUANTA_VERIFY_TIMEOUT_S": "60"},
    )
    assert result.status.value == "inconclusive"
    assert result.outcome.failed == result.outcome.errored == 0


def test_config_and_cli_precedence(tmp_path: Path) -> None:
    config = merge([layer_from_table({"verify": {"mode": "full", "timeout_s": 0}})])
    assert config.verify_mode == "full" and config.verify_timeout_s == 0
    flow = flow_for(tmp_path, lambda run_id: "green")
    flow._verify_policy = VerificationPolicy(config.verify_mode, config.verify_timeout_s)
    request = MandateRequest("investigation", "find client", "where?", out_of_scope="edits")
    assert flow.prepare(request, 0, MandateOptions(simple=True)).verification.mode == "full"
    assert (
        flow.prepare(request, 0, MandateOptions(simple=True, verify="off")).verification.mode
        == "off"
    )
    assert pytest_parallel('[project]\ndependencies = ["pytest"]', "pytest") == ()
    assert pytest_parallel('[project]\ndependencies = ["pytest-xdist"]', "jest") == ()


def test_live_line_switches_to_verification_in_both_languages() -> None:
    live = LiveRun(0, tool="Grep")
    live.progress(started("verdict", msg("verify.running", runner="pytest")), 10)
    state = live.take(202)
    assert state is not None and state.phase == "verification"
    assert Catalog("en").live(state) == "verifying · pytest · 3:12"
    assert Catalog("es").live(state) == "verificando · pytest · 3:12"


@pytest.mark.parametrize("manifest", ['project = "invalid"', 'dependency-groups = "invalid"'])
def test_xdist_detection_ignores_malformed_dependency_tables(manifest: str) -> None:
    assert pytest_parallel(manifest, "pytest") == ()


def test_timed_out_verification_keeps_the_agent_status_and_saved_report(tmp_path: Path) -> None:
    flow = flow_for(tmp_path, lambda run_id: "green")
    result = VerificationResult(
        "inconclusive", msg("verify.timeout", time="1:00", command="pytest")
    )
    flow._verification = lambda run_id, policy, progress: result
    request = MandateRequest("investigation", "find client", "where?", out_of_scope="edits")
    ready = flow.prepare(request, 0, MandateOptions(simple=True, verify="full"))
    report = flow.run(ready, RecordingSink())
    assert report.ok and report.run.status == "ok" and report.tests == "inconclusive"
    stored = json.loads((tmp_path / ".cuanta/runs/R/run.json").read_text(encoding="utf-8"))
    assert stored["status"] == "ok"
    assert stored["verification"]["status"] == "inconclusive"
    assert report.preparation_seconds is not None and report.agent_seconds is not None


def test_the_timeout_names_the_full_command_even_for_an_affected_selection(tmp_path: Path) -> None:
    runner = FakeRunner(responses={"pytest": Completed(124, "", "timeout", 60)})
    ledger = MemoryLedger()
    (tmp_path / "main.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_main.py").write_text("def test_main(): pass\n", encoding="utf-8")
    gateway = RunGateway(
        tmp_path,
        lambda name: PytestRunner(runner),
        ledger,
        FileCapsuleStore(tmp_path / ".cuanta/capsules"),
        FixedClock(),
        lambda: "T",
        tmp_path / ".cuanta/tmp",
    )
    affected = AffectedGateway(gateway, LocalWorkspace(tmp_path), ledger, frozenset(), lambda: {})
    ledger.add_snapshots(
        [Snapshot("R", "start", path, digest) for path, digest in affected.hashes().items()]
    )
    (tmp_path / "main.py").write_text("value = 2\n", encoding="utf-8")
    result = affected.verify(
        Stack(test_runner="pytest"),
        Config(),
        VerifyTier.STRONG,
        "R",
        VerificationPolicy("auto", 60),
        ("-n", "auto"),
    )
    assert result.command.startswith("pytest tests/test_main.py")
    assert dict(result.reason.params)["command"] == "pytest -n auto"


def test_time_line_names_in_session_checks_without_claiming_they_were_skipped() -> None:
    message = timing_message(
        {"preparation_seconds": 8, "agent_seconds": 33, "implementation": {"state": "complete"}}
    )
    assert message is not None
    assert Catalog("en").message(message).endswith("Verification in agent session")
