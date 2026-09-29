from __future__ import annotations

import io
import json
import shlex
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine
from cuanta.adapters.storage.capsule_store import FileCapsuleStore
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.application.cross_engine import CrossEnginePipeline, CrossReport
from cuanta.application.engine_run import EngineLauncher, LaunchSpec
from cuanta.application.routing import RoutePlan
from cuanta.bootstrap import Container
from cuanta.cli import hooks
from cuanta.domain.config import Config, layer_from_table
from cuanta.domain.engine import EngineEvent, EngineOutcome, EngineRequest, ModelUsage, RunResult
from cuanta.domain.governor_report import BEST_EFFORT, HOOKS, BlockedCalls, blocked_calls
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import msg
from cuanta.domain.models import ModelEntry, Tier
from cuanta.domain.pricing import Price, PriceTable
from cuanta.domain.progress import ProgressEvent
from cuanta.domain.read_discipline import (
    READ_LINE_LIMIT,
    decide_read_discipline,
    discipline_prompt,
)
from cuanta.domain.routing import ROLES, Role, RoleRoute, RoutingPolicy
from tests.fakes import FakeRunner

REQUEST = MandateRequest(type="feature", what="add canonical", why="seo", out_of_scope="secrets")
PRICES = PriceTable(
    {"gpt-6-sol": Price(2.0, 10.0, 2.5, 0.2), "sonnet": Price(3.0, 15.0, 3.75, 0.3)}
)


class Sink:
    def publish(self, event: ProgressEvent) -> None:
        return None


def test_pipelines_get_read_discipline_by_default_and_single_launches_keep_it_off() -> None:
    config = Config()
    assert config.pipeline_read_discipline and not config.read_discipline
    assert config.read_max_lines == READ_LINE_LIMIT == 400
    assert layer_from_table({"runs": {"read_max_lines": 250}}) == {"read_max_lines": 250}
    assert layer_from_table({"runs": {"read_max_lines": 0}}) == {}
    assert layer_from_table({"runs": {"pipeline_read_discipline": False}}) == {
        "pipeline_read_discipline": False
    }


def test_the_line_limit_is_configurable_and_points_to_the_page_tool() -> None:
    assert decide_read_discipline("Read", {}, 300, 900).permission == ""
    blocked = decide_read_discipline("Read", {}, 300, 900, line_limit=250)
    assert blocked.permission == "deny" and "page tool when you have it" in blocked.reason
    assert "offset and limit" in blocked.reason
    assert blocked.avoided_tokens == 225
    assert "250 lines" in discipline_prompt(250) and "best effort" in discipline_prompt()
    assert "Cuanta page when you have it" in discipline_prompt()


def test_the_hook_reads_the_optional_line_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert hooks.line_limit([]) == READ_LINE_LIMIT
    assert hooks.line_limit(["250"]) == 250
    assert hooks.line_limit(["0"]) is None and hooks.line_limit(["x"]) is None
    assert hooks.line_limit(["1", "2"]) is None
    assert hooks.line_limit(["\u00b2"]) is None and hooks.line_limit(["\u0663"]) is None
    source = tmp_path / "mid.ts"
    source.write_text("line\n" * 300, encoding="utf-8")
    payload = {"cwd": str(tmp_path), "tool_name": "Read", "tool_input": {"file_path": str(source)}}
    assert hooks.hook_output(payload, tmp_path, "pre") == {}
    denied = hooks.hook_output(payload, tmp_path, "pre", 250)
    assert json.dumps(denied).count('"deny"') == 1
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CUANTA_RUN_ID", raising=False)
    monkeypatch.setattr("sys.argv", ["hooks", "pre", "250"])
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert hooks.main() == 0
    assert '"deny"' in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", ["hooks", "pre", "nope"])
    assert hooks.main() == 2


def _settings(container: Container, spec: LaunchSpec) -> dict[str, object]:
    engine = ClaudeCodeEngine(container.runner)
    launcher = container.launcher(engine, container.ledger(), telemetry=False)
    request = launcher.request(spec, "RUN", "trace", None)
    if not request.settings_file:
        return {}
    loaded = json.loads(Path(request.settings_file).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _commands(settings: dict[str, object]) -> list[str]:
    found = settings.get("hooks")
    assert isinstance(found, dict)
    return [found[event][0]["hooks"][0]["command"] for event in ("PreToolUse", "PostToolUse")]


def test_a_pipeline_role_gets_the_hooks_even_in_a_full_session(tmp_path: Path) -> None:
    container = Container(tmp_path, Config(run_session="full"), runner=FakeRunner())
    try:
        role = LaunchSpec("cross", "p", str(tmp_path), ("Read",), read_discipline=True)
        commands = _commands(_settings(container, role))
        interpreter = Path(sys.executable).as_posix()
        assert [shlex.split(command) for command in commands] == [
            [interpreter, "-m", "cuanta.cli.hooks", "pre"],
            [interpreter, "-m", "cuanta.cli.hooks", "post"],
        ]
        single = LaunchSpec("mandate", "p", str(tmp_path), ("Read",), session="lean")
        assert "hooks" not in _settings(container, single)
        plain = LaunchSpec("cross", "p", str(tmp_path), ("Read",))
        assert _settings(container, plain) == {}
    finally:
        container.close()


def test_a_configured_line_limit_reaches_the_hook_command(tmp_path: Path) -> None:
    config = Config(run_session="full", read_max_lines=250)
    container = Container(tmp_path, config, runner=FakeRunner())
    try:
        role = LaunchSpec("cross", "p", str(tmp_path), ("Read",), read_discipline=True)
        commands = _commands(_settings(container, role))
        assert all(shlex.split(command)[-1] == "250" for command in commands)
    finally:
        container.close()


class RoleEngine:
    def __init__(self, name: str, model: str) -> None:
        self._name = name
        self._model = model
        self.requests: list[EngineRequest] = []

    @property
    def name(self) -> str:
        return self._name

    def available(self) -> bool:
        return True

    def version(self) -> str:
        return "1"

    def missing_flags(self) -> tuple[str, ...]:
        return ()

    def cancel(self) -> None:
        return None

    def command(self, request: EngineRequest) -> list[str]:
        return [self._name]

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        self.requests.append(request)
        usage = ModelUsage(self._model, input_tokens=1_000, output_tokens=100)
        cost = 0.01 if self._name == "claude" else None
        result = RunResult(True, "success", cost, 1, "s", (usage,), '{"status": "done"}')
        on_event(result)
        return EngineOutcome(0, result, 0)


def two_roles() -> RoutePlan:
    models = {
        Role.ANALYST: ModelEntry("claude", "sonnet", "sonnet", "anthropic"),
        Role.SENIOR: ModelEntry("codex", "gpt-6-sol", "sol", "openai"),
    }
    routes = tuple(
        RoleRoute(
            role,
            Tier.STANDARD,
            Tier.STANDARD if role in models else None,
            models.get(role),
            msg("route.policy", tier="standard"),
        )
        for role in ROLES
    )
    return RoutePlan(RoutingPolicy(), None, None, (), routes, "heuristic")


def run_roles(
    tmp_path: Path, discipline: Callable[[str], bool] | None
) -> tuple[CrossReport, RoleEngine, RoleEngine]:
    engines = {"claude": RoleEngine("claude", "sonnet"), "codex": RoleEngine("codex", "gpt-6-sol")}
    counter = iter(range(100))
    ledger = MemoryLedger()
    launchers = {
        name: EngineLauncher(
            engine,
            ledger,
            FixedClock(),
            lambda: f"RUN{next(counter)}",
            lambda size: b"\x01" * size,
            "shop",
            4318,
            None,
            prices=PRICES,
        )
        for name, engine in engines.items()
    }
    pipeline = CrossEnginePipeline(
        launchers.get,
        tuple,
        FileCapsuleStore(tmp_path / "capsules"),
        str(tmp_path),
        1.0,
        read_discipline=discipline,
        read_max_lines=300,
    )
    report = pipeline.run(REQUEST, two_roles(), Sink())
    return report, engines["claude"], engines["codex"]


def test_claude_roles_are_enforced_and_codex_roles_get_the_prompt_as_best_effort(
    tmp_path: Path,
) -> None:
    report, claude, codex = run_roles(tmp_path / "on", lambda engine: True)
    legacy, old_claude, old_codex = run_roles(tmp_path / "off", None)
    rule = discipline_prompt(300)
    assert rule in codex.requests[0].prompt and rule not in claude.requests[0].prompt
    assert rule not in old_codex.requests[0].prompt
    assert codex.requests[0].prompt.replace(f"\n{rule}", "") == old_codex.requests[0].prompt
    assert claude.requests[0].prompt == old_claude.requests[0].prompt
    assert [(step.role, step.read_discipline) for step in report.steps] == [
        (Role.ANALYST, HOOKS),
        (Role.SENIOR, BEST_EFFORT),
    ]
    assert [step.read_discipline for step in legacy.steps] == ["", ""]


def test_blocked_calls_count_each_hook_decision_once_with_the_avoided_tokens() -> None:
    def hook(tool: str, use: str, permission: str, tokens: int = 0) -> LedgerEvent:
        return LedgerEvent(
            run_id="R1",
            source="cuanta_hook",
            kind="read_discipline",
            tool_name=tool,
            tool_use_id=use,
            query_source=permission,
            raw=json.dumps({"avoided_tokens_estimate": tokens}),
        )

    events = [
        hook("Read", "t1", "deny", 2_000),
        hook("Read", "t1", "deny", 2_000),
        hook("Read", "t2", "deny", 500),
        hook("Grep", "t3", "deny"),
        hook("Bash", "t4", "allow"),
        LedgerEvent(run_id="R1", source="claude_otel", kind="read_discipline", tool_name="Read"),
        LedgerEvent(run_id="R1", source="cuanta_hook", kind="tool_result", tool_name="Read"),
    ]
    assert blocked_calls(events) == BlockedCalls(reads=2, searches=1, tests=1, tokens=2_500)
    assert blocked_calls([]).total == 0
