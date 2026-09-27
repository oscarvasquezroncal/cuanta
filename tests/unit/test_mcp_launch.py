from __future__ import annotations

import json
import sys
from pathlib import Path

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.application.engine_run import LaunchSpec
from cuanta.application.route_apply import RouteOptions
from cuanta.bootstrap import Container
from cuanta.domain.agents import AgentsPlan, guarded_agents, indexed_agents
from cuanta.domain.change_plan import ChangePlan
from cuanta.domain.config import Config, layer_from_table
from cuanta.domain.index_tools import INDEX_CONTRACT, INDEX_TOOLS
from cuanta.domain.ledger import Run
from cuanta.domain.routing import Role
from cuanta.ports.ledger import EventQuery
from tests.fakes import FakeRunner
from tests.unit.test_route_apply import AGENT, REQUEST, routing


def test_owned_mcp_profile_has_only_cuanta_with_an_absolute_runtime(tmp_path: Path) -> None:
    runner = FakeRunner()
    container = Container(tmp_path, Config(index_tools=True), runner=runner)
    engine = container.engine("claude")
    assert engine is not None
    launcher = container.launcher(engine, MemoryLedger(), telemetry=False)
    request = launcher.request(
        LaunchSpec("mandate", "request", str(tmp_path), (), read_only=True), "RUN", "TRACE", None
    )
    config = json.loads(Path(request.mcp_config).read_text(encoding="utf-8"))
    assert set(config["mcpServers"]) == {"cuanta"}
    server = config["mcpServers"]["cuanta"]
    assert server["command"] == sys.executable
    assert server["args"] == ["-m", "cuanta", "--project", str(tmp_path), "mcp", "serve"]
    assert all(tool in request.allowed_tools for tool in INDEX_TOOLS)
    assert "Bash" not in request.allowed_tools and "Write" not in request.allowed_tools
    assert request.tools is not None and all(tool not in request.tools for tool in INDEX_TOOLS)
    assert INDEX_CONTRACT in request.append_system_prompt
    assert request.setting_sources == ()
    settings = json.loads(Path(request.settings_file).read_text(encoding="utf-8"))
    assert settings["disableAllHooks"] is True
    assert "Write" in settings["permissions"]["deny"]
    assert not runner.calls


def test_disabled_mcp_and_explicit_full_unprotected_profile_keep_prior_behavior(
    tmp_path: Path,
) -> None:
    for enabled, session in ((False, "lean"), (True, "full")):
        container = Container(tmp_path, Config(index_tools=enabled), runner=FakeRunner())
        engine = container.engine("claude")
        assert engine is not None
        launcher = container.launcher(engine, MemoryLedger(), telemetry=False)
        request = launcher.request(
            LaunchSpec("mandate", "request", str(tmp_path), (), session=session),
            "RUN",
            "TRACE",
            None,
        )
        assert not set(request.allowed_tools) & set(INDEX_TOOLS)
        assert INDEX_CONTRACT not in request.append_system_prompt
        if request.mcp_config:
            assert json.loads(Path(request.mcp_config).read_text(encoding="utf-8")) == {
                "mcpServers": {}
            }
        else:
            assert session == "full"


def test_generated_roles_keep_original_body_and_protection_with_index_tools() -> None:
    original = AgentsPlan(
        {
            "analyst": {
                "prompt": "Original static role",
                "tools": ["Read", "Bash", "Write"],
                "hooks": {},
            }
        },
        {"analyst": Role.ANALYST},
    )
    protected = guarded_agents(original, ChangePlan(guard=("private/**",)))
    indexed = indexed_agents(protected)
    fields = indexed.agents["analyst"]
    tools = fields["tools"]
    denied = fields["disallowedTools"]
    assert isinstance(tools, list) and isinstance(denied, list)
    assert str(fields["prompt"]).startswith("Original static role\n\n" + INDEX_CONTRACT)
    assert set(tools) == {"Read", *INDEX_TOOLS}
    assert "Write" in denied and "Bash" in denied
    assert "hooks" not in fields and "mcpServers" not in fields
    assert original.agents["analyst"]["tools"] == ["Read", "Bash", "Write"]


def test_config_index_tools_is_explicit_before_runtime_probe() -> None:
    assert layer_from_table({"runs": {"index_tools": True}}) == {"index_tools": True}


def test_generated_team_uses_index_only_when_the_launch_loads_it(tmp_path: Path) -> None:
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "architecture-analyst.md").write_text(
        AGENT.format(name="architecture-analyst"), encoding="utf-8"
    )
    service = routing(tmp_path, {})
    service._index_tools = True
    service._default_session = "full"
    applied = service.apply(REQUEST, RouteOptions(mode="fixed"), "claude")
    assert service.protect(applied, ChangePlan()) is applied
    assert service.protect(applied, ChangePlan(), "full") is applied
    for session, plan in (("lean", ChangePlan()), ("full", ChangePlan(read_only=True))):
        protected = service.protect(applied, plan, session)
        assert protected.agents is not None
        analyst = protected.agents.agents["architecture-analyst"]
        assert INDEX_CONTRACT in str(analyst["prompt"])
        tools = analyst["tools"]
        assert isinstance(tools, list)
        assert set(INDEX_TOOLS) <= set(tools)


def test_mcp_call_logging_is_metadata_only_and_requires_existing_run(tmp_path: Path) -> None:
    container = Container.for_project(tmp_path)
    container.record_mcp_call(
        "missing", "page", {}, {"text": "secret source payload"}, 0.01, "call:1"
    )
    assert not (tmp_path / ".cuanta/ledger.db").exists()
    ledger = container.shared_ledger()
    ledger.add_run(Run("RUN", "mandate"))
    result = {"content": [{"type": "text", "text": "secret source payload"}]}
    container.record_mcp_call(
        "RUN", "page", {"path": "src/cart.py", "query": "private prompt"}, result, 0.025, "call:1"
    )
    container.record_mcp_call(
        "RUN",
        "initialize",
        {},
        {"protocol_version": "2025-11-25", "client_name": "test", "client_version": "1"},
        0,
        "init",
    )
    events = ledger.events(EventQuery(run_id="RUN"))
    assert len(events) == 2
    call = next(item for item in events if item.kind == "index_call")
    assert call.source == "cuanta_mcp" and call.agent == "uncertain"
    assert call.file_path == "src/cart.py" and call.duration_ms == 25
    assert call.tool_result_bytes > 0 and call.tool_use_id == "call:1"
    assert "secret" not in call.raw and "private" not in call.raw
    assert json.loads(call.raw)["returned_tokens_estimate"] > 0
    assert (
        next(item for item in events if item.kind == "index_handshake").raw.find("2025-11-25") > 0
    )
    container.close()


def test_source_escape_is_not_logged_as_a_local_file(tmp_path: Path) -> None:
    container = Container.for_project(tmp_path)
    ledger = container.shared_ledger()
    ledger.add_run(Run("RUN", "mandate"))
    container.record_mcp_call("RUN", "page", {"path": "../outside.py"}, {"isError": True}, 0, "bad")
    (event,) = ledger.events(EventQuery(run_id="RUN"))
    assert event.file_path == "" and event.success is False
    assert "outside" not in event.raw
    container.record_mcp_call("RUN", "page", {}, {"error": {"code": -32603}}, 0, "rpc")
    assert ledger.events(EventQuery(run_id="RUN"))[-1].success is False
    container.close()
