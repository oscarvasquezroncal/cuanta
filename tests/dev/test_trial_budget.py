from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from dev.trial import command, request_headroom
from dev.trial_budget import TrialBudget

from cuanta.domain.depth import MAX_TURNS, parse_depth
from dev import spec as trial_spec
from tests.dev.test_scripts import definition, write_spec


def test_budget_survives_separate_invocations_and_changed_trial_names(tmp_path: Path) -> None:
    path = tmp_path / "budget.json"
    first = TrialBudget(path, "Q3", 1.0)
    reservation = first.reserve("first", 0.6)
    first.settle(reservation, 0.55, "run-a")
    first.complete(reservation)
    second = TrialBudget(path, "Q3", 1.0)
    with pytest.raises(ValueError, match="remains"):
        second.reserve("changed-spec-retry", 0.6)
    smaller = second.reserve("smaller", 0.4)
    second.settle(smaller, 0.3, "run-b")
    second.complete(smaller)
    with pytest.raises(ValueError, match="cap changed"):
        TrialBudget(path, "Q3", 2.0).reserve("larger", 0.4)


def test_interrupted_or_unknown_spend_blocks_every_further_reservation(tmp_path: Path) -> None:
    budget = TrialBudget(tmp_path / "budget.json", "Q2", 2.0)
    budget.reserve("interrupted", 0.5)
    with pytest.raises(ValueError, match="unknown spend"):
        budget.reserve("retry", 0.5)


def test_atomic_lock_is_preserved_when_another_launch_holds_it(tmp_path: Path) -> None:
    budget = TrialBudget(tmp_path / "budget.json", "Q2", 2.0)
    budget.lock.write_text("busy", encoding="utf-8")
    with pytest.raises(ValueError, match="budget lock"):
        budget.reserve("trial", 0.5)
    assert budget.lock.read_text(encoding="utf-8") == "busy"


def test_trial_preserves_profile_variant_and_leaves_native_request_headroom(tmp_path: Path) -> None:
    spec = definition(tmp_path)
    trial = replace(
        spec.trials[0], profile="fast", variant="low", pure=True, model="claude-sonnet-5"
    )
    arguments = command(spec, trial)
    assert arguments[arguments.index("--profile") + 1] == "fast"
    assert arguments[arguments.index("--variant") + 1] == "low"
    assert "--pure" in arguments
    assert float(arguments[arguments.index("--max-budget-usd") + 1]) < trial.cap
    assert arguments[arguments.index("--max-turns") + 1] == str(MAX_TURNS[parse_depth(trial.depth)])
    assert "--max-turns" not in command(
        spec, replace(trial, engine="codex", profile="", pure=False, variant="", model="")
    )
    assert request_headroom(trial) == 0.1
    assert request_headroom(replace(trial, headroom_usd=0.2)) == 0.2
    assert request_headroom(replace(trial, engine="codex")) == 0.0


def test_candidate_reservations_can_exceed_total_but_actual_spend_blocks_next(
    tmp_path: Path,
) -> None:
    path = write_spec(tmp_path)
    text = path.read_text(encoding="utf-8")
    body = text.split("[[trials]]", 1)[1]
    text += "[[trials]]" + body.replace('name = "one"', 'name = "two"')
    text += "[[trials]]" + body.replace('name = "one"', 'name = "three"')
    path.write_text(text, encoding="utf-8")
    matrix = trial_spec.load(path)
    assert sum(item.cap for item in matrix.trials) > matrix.total_cap
    budget = TrialBudget(tmp_path / "series.json", "screen", matrix.total_cap)
    for number in range(2):
        reservation = budget.reserve(str(number), 0.4)
        budget.settle(reservation, 0.4, str(number))
        budget.complete(reservation)
    with pytest.raises(ValueError, match="remains"):
        budget.reserve("next", 0.4)


@pytest.mark.parametrize(("cap", "headroom"), [(0.1, 0.099), (0.01, 0.0)])
def test_subcent_native_residual_is_rejected(tmp_path: Path, cap: float, headroom: float) -> None:
    spec = definition(tmp_path)
    with pytest.raises(ValueError, match="at least"):
        command(spec, replace(spec.trials[0], cap=cap, headroom_usd=headroom))


def test_native_cap_never_rounds_into_reserved_headroom(tmp_path: Path) -> None:
    spec = definition(tmp_path)
    arguments = command(spec, replace(spec.trials[0], cap=0.1, headroom_usd=0.085))
    assert arguments[arguments.index("--max-budget-usd") + 1] == "0.01"


def test_known_cost_keeps_reservation_active_until_cleanup(tmp_path: Path) -> None:
    path = tmp_path / "budget.json"
    first = TrialBudget(path, "Q2", 2.0)
    reservation = first.reserve("first", 0.5)
    first.settle(reservation, 0.2, "run-a")
    second = TrialBudget(path, "Q2", 2.0)
    with pytest.raises(ValueError, match="awaiting acceptance or cleanup"):
        second.reserve("second", 0.5)
    assert '"cost_usd": 0.2' in path.read_text(encoding="utf-8")
    first.complete(reservation)
    assert second.reserve("second", 0.5).trial == "second"
