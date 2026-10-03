from __future__ import annotations

from pathlib import Path

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.application.route_apply import RouteOptions
from cuanta.domain.errors import DomainFailure
from cuanta.domain.messages import english
from cuanta.domain.models import Tier
from cuanta.domain.routing import Role
from tests.unit.test_route_apply import AGENT, REQUEST, routing

MAIN = "the main session runs on the chosen model claude-opus-5-5"


def forge_agents(root: Path) -> None:
    agents = root / ".claude" / "agents"
    agents.mkdir(parents=True)
    for name in ("architecture-analyst", "python-senior", "tester", "docs-updater"):
        (agents / f"{name}.md").write_text(AGENT.format(name=name), encoding="utf-8")


def test_the_main_session_model_takes_the_orchestrator_route_and_nothing_else(
    tmp_path: Path,
) -> None:
    forge_agents(tmp_path)
    subject = routing(tmp_path, {})
    applied = subject.apply(REQUEST, RouteOptions(mode="fixed"), "claude")
    assert applied.orchestrator == "claude-sonnet-5"
    chosen = subject.main_model(applied, "claude-opus-5-5")
    route = chosen.plan.route(Role.ORCHESTRATOR)
    assert route is not None and route.model is not None
    assert (route.model.id, route.tier) == ("opus", Tier.PREMIUM)
    assert english(route.reason) == MAIN
    assert chosen.orchestrator == "claude-opus-5-5"
    assert chosen.agents_file == applied.agents_file
    rest = [item for item in chosen.plan.routes if item.role is not Role.ORCHESTRATOR]
    assert rest == [item for item in applied.plan.routes if item.role is not Role.ORCHESTRATOR]
    assert subject.main_model(applied, "sonnet") is applied
    assert subject.main_model(applied, "") is applied


def test_an_orchestrator_pin_on_another_model_is_refused_and_the_same_one_is_kept(
    tmp_path: Path,
) -> None:
    forge_agents(tmp_path)
    subject = routing(tmp_path, {})
    pins = RouteOptions(mode="fixed", role_models=(("orchestrator", "sonnet"),))
    pinned = subject.apply(REQUEST, pins, "claude")
    with pytest.raises(DomainFailure) as caught:
        subject.main_model(pinned, "claude-opus-5-5")
    assert caught.value.message == (
        "the orchestrator is pinned to sonnet, but the main session runs on claude-opus-5-5"
    )
    assert caught.value.hint == "pin the orchestrator to the main session model, or drop the pin"
    assert subject.main_model(pinned, "claude-sonnet-5") is pinned


def test_a_forced_main_session_model_never_teaches_the_router_its_orchestrator_tier(
    tmp_path: Path,
) -> None:
    forge_agents(tmp_path)
    ledger = MemoryLedger()
    subject = routing(tmp_path, {}, ledger)
    for index in range(5):
        applied = subject.apply(REQUEST, RouteOptions(), "claude")
        forced = subject.main_model(applied, "claude-opus-5-5")
        assert forced.orchestrator == "claude-opus-5-5"
        run_id = f"01RUN{index:021d}"
        subject.record(run_id, REQUEST.type, forced)
        subject.close(run_id, "green", 0.1)
    roles = [decision.role for decision in ledger.routing_decisions()]
    assert Role.ORCHESTRATOR.value not in roles
    assert Role.ANALYST.value in roles
    later = subject.apply(REQUEST, RouteOptions(), "claude")
    route = later.plan.route(Role.ORCHESTRATOR)
    assert route is not None and route.tier is Tier.STANDARD
    assert later.orchestrator == "claude-sonnet-5"
