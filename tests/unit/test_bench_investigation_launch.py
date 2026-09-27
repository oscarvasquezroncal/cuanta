from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.application.engine_run import DEFAULT_DENIED, EngineLauncher, Launch, LaunchSpec
from cuanta.bootstrap import Container
from cuanta.domain.bench import BenchTask
from cuanta.domain.config import Config, layer_from_table
from cuanta.domain.engine import EngineEvent, EngineOutcome, RunResult
from cuanta.domain.index_tools import INDEX_TOOLS
from cuanta.domain.ledger import Run
from cuanta.domain.mandate import MandateRequest
from cuanta.ports.system import Completed
from tests.fakes import FakeRunner


def test_pack_off_keeps_index_tools_safety_and_explicit_pack(tmp_path: Path) -> None:
    (tmp_path / "cart.py").write_text("TOTAL = 1\n", encoding="utf-8")
    runner = FakeRunner(binaries={"claude": "/bin/claude"})
    container = Container(tmp_path, Config(pack_enabled=False, index_tools=True), runner=runner)
    ledger = MemoryLedger()
    flow = container.mandate_flow(ledger)
    cross = container.cross_engine(ledger, 1.0, 0)
    assert flow._context_pack is None
    assert cross._context_pack is None
    plan = container.change_plan(MandateRequest(type="investigation", what="Audit cart.py"))
    assert plan.read_only
    engine = container.engine("claude")
    assert engine is not None
    request = container.launcher(engine, ledger, telemetry=False).request(
        LaunchSpec("bench", "request", str(tmp_path), (), read_only=True), "RUN", "TRACE", None
    )
    assert set(INDEX_TOOLS) <= set(request.allowed_tools)
    pack = container.context_pack(MandateRequest(type="investigation", what="Audit cart.py"))
    assert pack.tokens > 0
    assert Config().pack_enabled is True
    assert layer_from_table({"runs": {"pack_enabled": False}})["pack_enabled"] is False
    container.close()


@pytest.mark.parametrize(
    ("depth", "turns"), [("quick", 20), ("normal", 40), ("deep", 80), ("", 40)]
)
def test_investigation_baseline_is_read_only_single_and_uses_delivered_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, depth: str, turns: int
) -> None:
    runner = FakeRunner(binaries={"claude": "/bin/claude"})
    runner.responses["claude --version"] = Completed(0, "9.9.9 (Claude Code)", "")
    container = Container(tmp_path, Config(index_enabled=False), runner=runner)
    captured: list[LaunchSpec] = []

    def launch(
        _self: EngineLauncher,
        spec: LaunchSpec,
        _on_event: Callable[[EngineEvent], None],
        _before: Callable[[str], None] | None = None,
    ) -> Launch:
        captured.append(spec)
        request = _self.request(spec, "BASE", "TRACE", None)
        assert not {"Agent", "Task"} & set(request.allowed_tools)
        assert not {"Agent", "Task"} & set(request.tools or ())
        assert {"Agent", "Task"} <= set(request.disallowed_tools)
        assert set(DEFAULT_DENIED) <= set(request.disallowed_tools)
        command = _self._engine.command(request)
        assert command[command.index("--effort") + 1] == spec.effort
        result = RunResult(True, "success", 0.1, 2, "session", text="Answer cart.py:1")
        return Launch(Run("BASE", "bench"), EngineOutcome(0, result, 1))

    monkeypatch.setattr(EngineLauncher, "launch", launch)
    task = BenchTask(
        "audit",
        ("investigation",),
        "fixture",
        MandateRequest(type="investigation", what="Audit cart.py"),
        "Audit",
        {},
    )
    run, subtype, answer = container._bench_baseline(
        task, str(tmp_path), 0.5, "claude-sonnet-5", "lean", depth, MemoryLedger()
    )
    assert run.id == "BASE"
    assert subtype == "success"
    assert answer == "Answer cart.py:1"
    spec = captured[0]
    assert spec.read_only
    assert spec.temporary_copy
    assert spec.shape == "single"
    assert spec.task_type == "investigation"
    assert spec.depth == (depth or "normal")
    assert spec.effort == {"quick": "low", "normal": "medium", "deep": "high", "": "medium"}[depth]
    assert spec.max_turns == turns
    assert spec.max_budget_usd == 0.5
    container.close()


def test_legacy_code_baseline_retains_git_denial_and_omitted_depth_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner(binaries={"claude": "/bin/claude"})
    container = Container(tmp_path, Config(index_enabled=False), runner=runner)
    captured: list[LaunchSpec] = []

    def launch(
        self: EngineLauncher,
        spec: LaunchSpec,
        on_event: Callable[[EngineEvent], None],
        before: Callable[[str], None] | None = None,
    ) -> Launch:
        captured.append(spec)
        request = self.request(spec, "BASE", "TRACE", None)
        assert set(DEFAULT_DENIED) <= set(request.disallowed_tools)
        return Launch(Run("BASE", "bench"), EngineOutcome(0, None, 0))

    monkeypatch.setattr(EngineLauncher, "launch", launch)
    task = BenchTask("fix", (), "fixture", MandateRequest(type="bug", what="Fix"), "Fix", {})
    container._bench_baseline(task, str(tmp_path), 0.5, "sonnet", "lean", "", MemoryLedger())
    assert captured[0].disallowed_tools == DEFAULT_DENIED
    assert captured[0].effort == ""
    assert captured[0].max_turns == 0
    assert not captured[0].read_only
    container.close()
