from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.application.mandate_flow import MandateOptions, forecast_capped
from cuanta.application.steering import SESSION_FINISH, session_steering
from cuanta.domain.config import layer_from_table, merge
from cuanta.domain.governor import ReactionKind, RolePlan, RoleProgress, Trigger, decide
from cuanta.domain.limits import (
    LimitSettings,
    RunLimits,
    forecast_limits,
    limit_settings,
    limits_message,
    limits_payload,
)
from cuanta.domain.messages import english
from cuanta.domain.routing import Provider, Role
from cuanta.tui.i18n import Catalog
from tests.fakes import FakeRunner
from tests.unit.test_engine_profiles import REQUEST
from tests.unit.test_engine_profiles import flow as profile_flow
from tests.unit.test_forecast import team
from tests.unit.test_forecast_launch import forecaster
from tests.unit.test_steering import Leg, Recorder, TurnEngine, launcher_for, setup, team_spec

FORECAST = LimitSettings()


def test_without_a_spend_cap_the_forecast_p90_sets_a_soft_and_a_hard_cap() -> None:
    capped = forecast_limits(RunLimits(max_turns=12), False, 1.5, FORECAST)
    assert capped == RunLimits(3.0, 12, 0.0, soft_usd=1.5)
    assert forecast_limits(RunLimits(), False, None, FORECAST) == RunLimits()
    assert forecast_limits(RunLimits(), False, 0.0, FORECAST) == RunLimits()


def test_an_explicit_or_configured_cap_wins_over_the_forecast() -> None:
    assert forecast_limits(RunLimits(5.0), False, 1.5, FORECAST) == RunLimits(5.0)
    assert forecast_limits(RunLimits(), True, 1.5, FORECAST) == RunLimits()


@pytest.mark.parametrize(
    ("soft", "hard", "expected"),
    [
        (0.0, 2.0, RunLimits(3.0)),
        (1.0, 0.0, RunLimits(soft_usd=1.5)),
        (0.0, 0.0, RunLimits()),
        (3.0, 2.0, RunLimits(3.0, soft_usd=3.0)),
    ],
)
def test_zero_turns_either_forecast_cap_off_and_the_soft_cap_never_passes_the_hard_one(
    soft: float, hard: float, expected: RunLimits
) -> None:
    settings = LimitSettings(soft_p90=soft, hard_p90=hard)
    assert forecast_limits(RunLimits(), False, 1.5, settings) == expected


def test_the_forecast_factors_are_read_from_the_limits_table() -> None:
    assert (merge([]).soft_p90, merge([]).hard_p90) == (1.0, 2.0)
    layer = layer_from_table({"limits": {"soft_p90": 0, "hard_p90": 3}})
    assert layer == {"soft_p90": 0.0, "hard_p90": 3.0}
    assert layer_from_table({"limits": {"soft_p90": -1}}) == {}
    settings = limit_settings(merge([layer]))
    assert (settings.soft_p90, settings.hard_p90) == (0.0, 3.0)


def test_the_card_names_both_caps_in_both_languages() -> None:
    limits = RunLimits(3.0, soft_usd=1.5)
    assert english(limits_message(limits)) == (
        "limits: spend cap $3.00, closing turn at $1.50 (forecast P90)"
    )
    assert Catalog("es").message(limits_message(limits)) == (
        "límites: tope de gasto $3.00, turno de cierre a $1.50 (P90 previsto)"
    )
    assert english(limits_message(RunLimits(soft_usd=1.5))) == (
        "limits: closing turn at $1.50 (forecast P90)"
    )
    assert limits_payload(limits) == {
        "budget_usd": 3.0,
        "soft_usd": 1.5,
        "max_turns": None,
        "wall_min": None,
    }


def test_a_prepared_launch_without_a_cap_takes_both_caps_from_its_forecast(
    tmp_path: Path,
) -> None:
    engine = ClaudeCodeEngine(FakeRunner())
    flow = profile_flow(tmp_path, engine)
    prepared = flow.prepare(REQUEST, 0, MandateOptions())
    planned = forecaster(MemoryLedger()).plan("bug", team(), Provider.CLAUDE, "normal", "single", 0)
    p90 = planned.envelope.p90_usd
    assert p90 is not None and p90 > 0
    capped = forecast_capped(replace(prepared, forecast=planned), FORECAST, MandateOptions())
    assert capped.limits.soft_usd == round(p90, 2)
    assert capped.limits.budget_usd == round(2 * p90, 2)
    assert capped.spec.max_budget_usd == capped.limits.budget_usd
    command = engine.command(capped.launcher.request(capped.spec, "R", "trace", None))
    assert command[command.index("--max-budget-usd") + 1] == f"{capped.limits.budget_usd:.2f}"
    explicit = forecast_capped(
        replace(prepared, forecast=planned), FORECAST, MandateOptions(budget_usd=0.0)
    )
    assert explicit.limits == prepared.limits and explicit.spec.max_budget_usd == 0.0
    off = forecast_capped(
        replace(prepared, forecast=planned), FORECAST, MandateOptions(limits="off")
    )
    assert off.limits == prepared.limits


def test_the_soft_cap_sends_the_finish_turn_before_the_hard_cap() -> None:
    plan = RolePlan(
        Role.ORCHESTRATOR,
        Provider.CLAUDE,
        "claude-opus-5-5",
        3.0,
        20,
        hard_cap_usd=3.0,
        soft_usd=1.5,
    )
    assert decide(plan, RoleProgress(requests=2, spent_usd=1.2), set()) is None
    found = decide(plan, RoleProgress(requests=3, spent_usd=1.51), set())
    assert found is not None
    assert (found.kind, found.trigger) == (ReactionKind.FINISH_NOW, Trigger.SOFT_CAP)
    assert decide(plan, RoleProgress(requests=3, spent_usd=1.51), {ReactionKind.FINISH_NOW}) is None


def test_a_single_session_with_a_soft_cap_gets_one_finish_at_the_soft_cap(tmp_path: Path) -> None:
    engine = TurnEngine([Leg((250_000,) * 14, cost=1.0)])
    launcher = launcher_for(engine, MemoryLedger())
    recorder = Recorder()
    spec = team_spec(tmp_path, agents_file="", cap=3.0)
    assert session_steering(setup(), launcher, spec, None, recorder) is None
    steering = session_steering(setup(), launcher, spec, None, recorder, soft_usd=0.5)
    assert steering is not None
    launcher.launch(replace(spec, steer=True), steering)
    assert [turn[2] for turn in engine.turns] == [SESSION_FINISH]
    assert [taken.reaction.trigger for taken in steering.taken] == [Trigger.SOFT_CAP]
    assert recorder.texts()[0].startswith(
        "session: the governor sent the finish turn (the run reached its forecast P90) at $0.5"
    )
    uncapped = team_spec(tmp_path, agents_file="", cap=0.0)
    assert session_steering(setup(), launcher, uncapped, None, recorder, soft_usd=0.5) is not None
