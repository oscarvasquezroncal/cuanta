from __future__ import annotations

from collections.abc import Iterator

from cuanta.application.loop import FixLoop, FixStep, TestStep
from cuanta.application.progress import RecordingSink
from cuanta.application.refresh import tier_transition
from cuanta.domain.detection import VerifyTier
from cuanta.domain.loop import StopReason, loop_gate, next_stop

SANCTIONED = (
    "> VERIFY_TIER=strong: an unattended sequence is sanctioned, with human review at the diff."
)


def test_gate_refuses_below_strong() -> None:
    gate = loop_gate(VerifyTier.MODERATE, "tsc + eslint, no test runner", SANCTIONED)
    assert not gate.allowed
    assert "VERIFY_TIER=moderate" in gate.missing
    assert "no test runner" in gate.missing
    assert not loop_gate(VerifyTier.WEAK, "nothing", SANCTIONED).allowed


def test_gate_needs_loop_md_sanction() -> None:
    assert not loop_gate(VerifyTier.STRONG, "pytest + mypy", None).allowed
    refused = loop_gate(
        VerifyTier.STRONG, "pytest + mypy", "No unattended loop is sanctioned here."
    )
    assert not refused.allowed
    assert "does not sanction" in refused.missing
    assert loop_gate(VerifyTier.STRONG, "pytest + mypy", SANCTIONED).allowed


def test_next_stop_order() -> None:
    assert next_stop("green", 0, 3, 0, 0) is StopReason.GREEN
    assert next_stop("persistent_failure", 1, 3, 0, 0) is StopReason.PERSISTENT
    assert next_stop("red", 1, 3, 5.0, 5.0) is StopReason.BUDGET
    assert next_stop("red", 3, 3, 0, 0) is StopReason.MAX_ITERATIONS
    assert next_stop("red", 1, 3, 1.0, 0) is None


def _loop(statuses: list[str], fixes: list[FixStep]) -> FixLoop:
    tests: Iterator[str] = iter(statuses)
    repairs: Iterator[FixStep] = iter(fixes)
    return FixLoop(
        lambda run_id: TestStep(next(tests), 2),
        lambda run_id, number: next(repairs),
        RecordingSink(),
    )


def test_loop_stops_green_after_fix() -> None:
    report = _loop(["red", "green"], [FixStep("M1", True, 0.4)]).run("L", 3, 0)
    assert report.stop is StopReason.GREEN
    assert report.ok
    assert [item.mandate_run for item in report.iterations] == ["M1"]
    assert report.spent_usd == 0.4


def test_loop_already_green_is_a_nap() -> None:
    report = _loop(["green"], []).run("L", 3, 0)
    assert report.stop is StopReason.GREEN
    assert report.iterations == ()


def test_loop_stops_on_persistent_signature() -> None:
    report = _loop(["red", "persistent_failure"], [FixStep("M1", True, 0.1)]).run("L", 5, 0)
    assert report.stop is StopReason.PERSISTENT
    assert not report.ok


def test_loop_stops_on_max_iterations() -> None:
    fixes = [FixStep(f"M{index}", True, 0.1) for index in range(2)]
    report = _loop(["red", "red", "red"], fixes).run("L", 2, 0)
    assert report.stop is StopReason.MAX_ITERATIONS
    assert len(report.iterations) == 2


def test_loop_stops_on_budget() -> None:
    fixes = [FixStep("M1", True, 3.0), FixStep("M2", True, 3.0)]
    report = _loop(["red", "red", "red"], fixes).run("L", 9, 5.0)
    assert report.stop is StopReason.BUDGET
    assert report.spent_usd == 6.0


def test_loop_stops_on_engine_error() -> None:
    report = _loop(["red"], [FixStep("M1", False, 0.2)]).run("L", 3, 0)
    assert report.stop is StopReason.ENGINE_ERROR


def test_tier_transition() -> None:
    assert tier_transition("moderate", "strong", "jest + tsc") == (
        "verify: moderate → strong (jest + tsc)",
        True,
    )
    assert tier_transition("strong", "strong", "x") == ("verify: strong (unchanged)", False)
    assert tier_transition("", "weak", "nothing")[1] is True


def test_unknown_fix_cost_stops_before_another_paid_fix() -> None:
    report = _loop(["red", "red"], [FixStep("M1", True, None)]).run("L", 3, 1.0)
    assert report.stop is StopReason.COST_UNKNOWN
    assert report.spent_usd is None
    assert len(report.iterations) == 1


def test_unknown_fix_cost_keeps_uncapped_total_unknown() -> None:
    report = _loop(
        ["red", "red", "green"], [FixStep("M1", True, None), FixStep("M2", True, 0.2)]
    ).run("L", 3, 0)
    assert report.ok and report.spent_usd is None
    assert len(report.iterations) == 2
