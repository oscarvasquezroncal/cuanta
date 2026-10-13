from __future__ import annotations

from dataclasses import replace

import pytest

from cuanta.domain.bench import BenchTask, Condition, ProofRecord, RunMetrics
from cuanta.domain.bench_proof import (
    ARMS,
    Comparison,
    Verdict,
    arm_caps,
    arm_named,
    arm_settings,
    chosen_arms,
    defaults_args,
    defaults_parity,
    plan_proof,
    targets,
)
from cuanta.domain.errors import DomainFailure
from cuanta.domain.mandate import MandateRequest


def measured(
    arm: str,
    cost: float | None,
    tokens: int = 1_000,
    wall_s: float = 100.0,
    accepted: bool = True,
) -> RunMetrics:
    return RunMetrics(
        "calc-add",
        Condition.BASELINE if arm == "claude-alone" else Condition.CUANTA,
        1,
        f"run-{arm}",
        accepted,
        False,
        tokens,
        0,
        0,
        0,
        cost,
        wall_s,
        0,
        0,
        proof=ProofRecord(Comparison.DEFAULTS.value, arm, 2.0),
    )


def test_the_defaults_comparison_runs_claude_alone_and_both_cuanta_defaults() -> None:
    arms = {arm.name: arm for arm in ARMS[Comparison.DEFAULTS]}
    assert list(arms) == ["claude-alone", "cuanta-default", "cuanta-team"]
    alone = arms["claude-alone"]
    assert alone.condition is Condition.BASELINE
    assert (alone.model, alone.effort, alone.defaults) == ("claude-opus-5-5", "high", False)
    assert arms["cuanta-default"].defaults and not arms["cuanta-default"].shape
    assert arms["cuanta-team"].defaults and arms["cuanta-team"].shape == "pipeline"
    assert arm_settings(alone) == "claude -p, model claude-opus-5-5, effort high"
    assert arm_settings(arms["cuanta-default"]) == "cuanta run with its defaults"
    assert arm_settings(arms["cuanta-team"]) == "cuanta run with its defaults, shape pipeline"
    assert arm_caps(["cuanta-team=2"], Comparison.DEFAULTS) == {"cuanta-team": 2.0}
    assert arm_named("cuanta-team") is arms["cuanta-team"]


def test_a_defaults_arm_runs_the_installed_cli_on_a_mandate_file() -> None:
    team = arm_named("cuanta-team")
    single = arm_named("cuanta-default")
    assert team is not None and single is not None
    assert defaults_args(single, "/scratch/mandate.md", 2.0, "/scratch/repo") == (
        "--plain",
        "--yes",
        "--project",
        "/scratch/repo",
        "run",
        "/scratch/mandate.md",
        "--max-budget-usd",
        "2.00",
    )
    assert defaults_args(team, "m.md", 1.5, "r")[-2:] == ("--shape", "pipeline")


def test_defaults_parity_is_met_within_ten_percent_and_twenty_percent_of_time() -> None:
    met = defaults_parity(
        [
            measured("claude-alone", 1.00, 1_000, 100.0),
            measured("cuanta-default", 1.08, 1_090, 119.0),
            measured("cuanta-team", 1.60, 3_000, 300.0),
        ]
    )
    assert met.comparison is Comparison.DEFAULTS and met.verdict is Verdict.MET
    assert met.measured == pytest.approx(0.08)
    assert "cost +8%" in met.summary and "time +19%" in met.summary
    assert "cuanta-team $1.60" in met.summary
    pricier = defaults_parity([measured("claude-alone", 1.00), measured("cuanta-default", 1.20)])
    assert pricier.verdict is Verdict.MISSED
    slower = defaults_parity(
        [measured("claude-alone", 1.00), measured("cuanta-default", 0.90, wall_s=130.0)]
    )
    assert slower.verdict is Verdict.MISSED
    failed = defaults_parity(
        [measured("claude-alone", 1.00), measured("cuanta-default", 0.50, accepted=False)]
    )
    assert failed.verdict is Verdict.MISSED and "accepted 0/1" in failed.summary


def test_defaults_parity_is_na_without_both_arms_measured() -> None:
    assert defaults_parity([measured("cuanta-default", 1.0)]).verdict is Verdict.UNKNOWN
    unpriced = defaults_parity([measured("claude-alone", None), measured("cuanta-default", 1.0)])
    assert unpriced.verdict is Verdict.UNKNOWN
    run = measured("claude-alone", 1.0)
    proofless = replace(run, proof=None)
    assert targets([proofless]) == ()
    assert [result.comparison for result in targets([run])] == [Comparison.DEFAULTS]


def test_a_comparison_can_run_only_the_named_arms() -> None:
    task = BenchTask("ref", ("reference",), "repo", MandateRequest(type="bug", what="w"), "p", {})
    only = chosen_arms(["cuanta-team"], Comparison.DEFAULTS)
    planned = plan_proof([task], Comparison.DEFAULTS, 1, 1, {"cuanta-team": 8.0}, 2.0, only=only)
    assert [(run.arm, run.cap_usd) for run in planned] == [("cuanta-team", 8.0)]
    assert len(plan_proof([task], Comparison.DEFAULTS, 1, 1, {}, 2.0)) == 3
    with pytest.raises(DomainFailure, match="--arm scout is not an arm of defaults"):
        chosen_arms(["scout"], Comparison.DEFAULTS)
    with pytest.raises(DomainFailure, match="runs its arms as a pair"):
        chosen_arms(["queue-first"], Comparison.WARM_QUEUE)
    assert chosen_arms([], Comparison.DEFAULTS) == frozenset()
