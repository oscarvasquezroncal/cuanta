from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from cuanta.application.cross_engine import CompletionState, CrossReport, CrossStep
from cuanta.application.estimate import Estimate, estimate
from cuanta.application.forecast import PlannedForecast
from cuanta.application.mandate_flow import MandateOptions
from cuanta.application.routing import RoutePlan
from cuanta.domain.change_plan import ChangePlan
from cuanta.domain.config import Config
from cuanta.domain.errors import NotAvailable
from cuanta.domain.ledger import Run
from cuanta.domain.mandate import MandateRequest, Shape
from cuanta.domain.messages import english, msg
from cuanta.domain.pricing import PriceTable
from cuanta.domain.routing import Role, RoutingPolicy
from cuanta.domain.scout import ShapeChoice
from cuanta.tui.services import ContainerServices, cross_report

if TYPE_CHECKING:
    from cuanta.bootstrap import Container


def test_a_cross_run_reaches_the_pipeline_screen_as_a_mandate_report() -> None:
    steps = (
        CrossStep(Role.ANALYST, "claude", "sonnet", "R1", True, 0.2, "found it"),
        CrossStep(Role.SENIOR, "codex", "gpt-6-sol", "R2", True, 0.5, "edited"),
    )
    report = CrossReport(
        steps,
        False,
        0.7,
        msg("cross.role_budget", role="tester"),
        changed_files=("src/a.ts",),
        state=CompletionState.PARTIAL,
    )
    root = Run("R1", "cross", engine="claude", cost_usd=0.2, task_type="feature")
    converted = cross_report(root, report)
    assert converted.run.id == "R1" and converted.run.cost_usd == 0.7
    assert not converted.ok
    assert converted.changed_files == ("src/a.ts",)
    assert converted.tests == "partial"
    assert converted.handoffs == ("found it", "edited")
    assert converted.text == "tester has no remaining reserved budget"
    assert converted.task_type == "feature"
    with pytest.raises(NotAvailable):
        cross_report(None, report)


def test_the_team_step_reuses_the_change_plan_compiled_for_the_same_request(
    tmp_path: Path,
) -> None:
    compiled: list[MandateRequest] = []

    def compile_plan(request: MandateRequest) -> ChangePlan:
        compiled.append(request)
        return ChangePlan(verify=(f"check {len(compiled)}",))

    container = cast("Container", SimpleNamespace(change_plan=compile_plan))
    services = ContainerServices(tmp_path)
    first = MandateRequest("bug", "fix add", "wrong sum")
    other = MandateRequest("bug", "fix sub", "wrong difference")
    assert services._compiled_plan(container, first).verify == ("check 1",)
    assert services._compiled_plan(container, first).verify == ("check 1",)
    assert services._compiled_plan(container, other).verify == ("check 2",)
    assert compiled == [first, other]


@pytest.mark.parametrize(
    ("engine", "profile"), [("codex", "balanced"), ("claude", "balanced"), ("claude", "fast")]
)
def test_a_failed_team_forecast_leaves_the_team_step_with_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: str, profile: str
) -> None:
    team = RoutePlan(RoutingPolicy(engines=(engine,)), None, None, (), (), "heuristic")
    reserves: list[bool] = []

    def plan_route(*args: object, **kwargs: object) -> tuple[RoutePlan, None]:
        return team, None

    def team_estimate(
        plan: RoutePlan,
        task_type: str,
        depth: str,
        cap: float,
        shape: str = "pipeline",
        repair: bool = True,
        docs_off: bool = False,
    ) -> Estimate:
        reserves.append(repair)
        return estimate(
            plan, (), PriceTable({}), task_type, depth, cap, shape, None, repair, docs_off
        )

    def team_forecast(*args: object, **kwargs: object) -> PlannedForecast:
        if profile == "fast":
            assert args[4] == "single"
            assert kwargs["implementation_profile"] == "fast"
            assert kwargs["variant"] == "low"
            assert kwargs["model"] == "claude-sonnet-5"
            assert kwargs["native"] is False
        raise ValueError("the code index is being written")

    container = SimpleNamespace(
        config=Config(engine=engine, implementation_profile=profile, implementation_variant="low"),
        plan_route=plan_route,
        team_estimate=team_estimate,
        change_plan=lambda request: ChangePlan(),
        team_forecast=team_forecast,
        shaped_options=lambda request, options: (options, ShapeChoice(Shape.PIPELINE)),
        docs_choice=lambda request, options: None,
        shape_plan=lambda plan, shape, docs: plan,
        close=lambda: None,
    )
    services = ContainerServices(tmp_path)
    monkeypatch.setattr(services, "_container", lambda: cast("Container", container))
    request = MandateRequest("bug", "fix add", "wrong sum")
    plan, found = services.team_plan(
        request, MandateOptions(engine=engine, model="claude-sonnet-5")
    )
    assert plan is team
    assert found.forecast is None
    assert found.forecast_error is not None
    assert english(found.forecast_error) == (
        "Forecast unavailable, the launch goes ahead without one: the code index is being written"
    )
    assert reserves == [False]
