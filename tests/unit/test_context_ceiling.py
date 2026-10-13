from __future__ import annotations

from pathlib import Path

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.application.engine_run import LaunchSpec
from cuanta.bootstrap import Container
from cuanta.domain.agents import AgentDefinition, AgentRoute, build_agents
from cuanta.domain.config import Config
from cuanta.domain.context_ceiling import (
    COMPACT_ENV,
    compact_supported,
    context_ceiling,
    context_message,
    max_context,
)
from cuanta.domain.governor import (
    ReactionKind,
    RolePlan,
    RoleProgress,
    Trigger,
    decide,
)
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.messages import english
from cuanta.domain.routing import Provider, Role
from cuanta.ports.system import Completed
from cuanta.tui.i18n import Catalog
from tests.cli.conftest import CLAUDE_HELP
from tests.fakes import FakeRunner


def test_ceilings_follow_the_model_and_the_installed_version() -> None:
    assert context_ceiling("claude-haiku-5-5") == 100_000
    assert context_ceiling("claude-sonnet-5-5") == context_ceiling("claude-opus-5-5") == 120_000
    assert compact_supported("2.1.296 (Claude Code)") and not compact_supported("2.1.295")
    assert not compact_supported("")


def test_each_agent_carries_its_compact_window_only_when_supported() -> None:
    definitions = (
        AgentDefinition("architecture-analyst", {"description": "a"}, "prompt"),
        AgentDefinition("python-senior", {"description": "s"}, "prompt"),
    )
    routes = {
        Role.ANALYST: AgentRoute("claude-haiku-5-5", "medium"),
        Role.SENIOR: AgentRoute("claude-sonnet-5-5", "high"),
    }
    compacted = build_agents(definitions, routes, compact=True).agents
    assert compacted["architecture-analyst"]["autoCompactWindow"] == 100_000
    assert compacted["python-senior"]["autoCompactWindow"] == 120_000
    plain = build_agents(definitions, routes).agents
    assert "autoCompactWindow" not in plain["architecture-analyst"]


def launcher_env(tmp_path: Path, version: str, model: str) -> dict[str, str]:
    runner = FakeRunner(
        binaries={"claude": "/bin/claude"},
        responses={
            "claude --version": Completed(0, version + "\n", ""),
            "claude --help": Completed(0, CLAUDE_HELP, ""),
        },
    )
    container = Container(tmp_path, Config(), runner=runner, home=tmp_path / "h")
    try:
        engine = container.engine("claude")
        assert engine is not None
        launcher = container.launcher(engine, MemoryLedger(), telemetry=False)
        spec = LaunchSpec("mandate", "request", str(tmp_path), (), model=model)
        return launcher.request(spec, "RUN", "TRACE", None).env
    finally:
        container.close()


def test_the_main_session_gets_its_window_through_the_environment(tmp_path: Path) -> None:
    assert launcher_env(tmp_path, "2.1.296", "claude-sonnet-5-5")[COMPACT_ENV] == "120000"
    assert launcher_env(tmp_path / "h", "2.1.296", "claude-haiku-5-5")[COMPACT_ENV] == "100000"
    assert COMPACT_ENV not in launcher_env(tmp_path / "o", "2.1.280", "claude-sonnet-5")


def test_older_versions_rotate_a_role_at_its_ceiling() -> None:
    plan = RolePlan(
        Role.SENIOR,
        Provider.CLAUDE,
        "claude-sonnet-5",
        5.0,
        30,
        rotatable=True,
        context_ceiling=120_000,
    )
    found = decide(plan, RoleProgress(requests=8, spent_usd=0.5, context_tokens=121_000), set())
    assert found is not None
    assert (found.kind, found.trigger) == (ReactionKind.ROTATE, Trigger.CONTEXT_CEILING)
    below = decide(plan, RoleProgress(requests=8, spent_usd=0.5, context_tokens=90_000), set())
    assert below is None or below.trigger is not Trigger.CONTEXT_CEILING


def test_the_report_names_the_largest_context_of_each_role() -> None:
    events = (
        LedgerEvent(
            kind="api_request",
            agent="analyst",
            model="claude-haiku-5-5",
            input_tokens=5,
            cache_read_tokens=40_000,
            cache_write_tokens=1_000,
        ),
        LedgerEvent(
            kind="api_request",
            agent="analyst",
            model="claude-haiku-5-5",
            input_tokens=5,
            cache_read_tokens=70_000,
        ),
        LedgerEvent(kind="api_request", model="claude-sonnet-5-5", cache_read_tokens=20_000),
        LedgerEvent(kind="tool_result", agent="analyst", cache_read_tokens=900_000),
    )
    assert max_context(events) == {
        "analyst": (70_005, "claude-haiku-5-5"),
        "main": (20_000, "claude-sonnet-5-5"),
    }
    shown = context_message("analyst", 70_005, 100_000)
    assert english(shown) == "context max analyst: 70,005 tokens of a 100,000 ceiling"
    over = context_message("main", 130_000, 120_000)
    assert english(over).startswith("context over its ceiling: main reached 130,000")
    assert "techo" in Catalog("es").message(shown)
