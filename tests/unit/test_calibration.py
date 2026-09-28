from __future__ import annotations

import pytest

from cuanta.domain.calibration import (
    ForecastActual,
    calibrate,
    calibration_rows,
    forecast_actuals,
    forecast_matches,
    spread_ratios,
)
from cuanta.domain.ledger import Forecast, Run


def _forecast(
    run_id: str,
    p50: float,
    p90: float,
    provider: str = "claude",
    task_type: str = "feature",
) -> Forecast:
    return Forecast(
        run_id=run_id,
        created_at="2026-09-28T00:00:00Z",
        provider=provider,
        task_type=task_type,
        depth="normal",
        shape="pipeline",
        p50_usd=p50,
        p90_usd=p90,
        cap_usd=1.5,
        verdict="comfortable",
    )


def _pair(
    run_id: str, p50: float, p90: float, actual: float | None, **group: str
) -> ForecastActual:
    return ForecastActual(_forecast(run_id, p50, p90, **group), actual)


def test_calibration_math_uses_known_actuals_only() -> None:
    items = (
        _pair("A", 1.0, 1.6, 0.8),
        _pair("B", 1.0, 1.6, 2.0),
        _pair("C", 0.5, 0.8, None),
        _pair("D", 0.5, 0.8, 0.0),
    )
    found = calibrate(items)
    assert (found.samples, found.unknown) == (3, 1)
    assert found.mae_usd == pytest.approx((0.2 + 1.0 + 0.5) / 3)
    assert found.mape == pytest.approx((0.25 + 0.5) / 2)
    assert found.p90_coverage == pytest.approx(2 / 3)
    assert found.p90_error == pytest.approx((1.0 + 0.2) / 2)
    assert items[2].known is False
    assert items[0].known is True


def test_unknown_actuals_stay_not_available() -> None:
    found = calibrate((_pair("A", 1.0, 1.6, None), _pair("B", 0.4, 0.6, None)))
    assert (found.samples, found.unknown) == (0, 2)
    assert (found.mae_usd, found.mape, found.p90_coverage, found.p90_error) == (
        None,
        None,
        None,
        None,
    )
    empty = calibrate(())
    assert (empty.samples, empty.unknown, empty.mae_usd) == (0, 0, None)


def test_calibration_groups_by_provider_and_task_type_with_aliases() -> None:
    items = (
        _pair("A", 1.0, 1.6, 1.0),
        _pair("B", 1.0, 1.6, 1.2, task_type="bug"),
        _pair("C", 1.0, 1.6, 2.0, task_type="fix"),
        _pair("D", 0.3, 0.5, 0.3, provider="codex"),
    )
    rows = calibration_rows(items)
    assert [(row.provider, row.task_type, row.samples) for row in rows] == [
        ("claude", "feature", 1),
        ("claude", "fix", 2),
        ("codex", "feature", 1),
    ]
    fixes = calibrate(items, "claude", "bug")
    assert (fixes.task_type, fixes.samples, fixes.p90_coverage) == ("fix", 2, 0.5)
    assert calibrate(items, "codex").samples == 1
    assert forecast_matches(items[1].forecast, "claude", "fix")
    assert not forecast_matches(items[3].forecast, "claude")


def test_spread_ratios_use_known_actuals_of_the_same_group() -> None:
    items = (
        _pair("A", 1.0, 1.6, 1.5),
        _pair("B", 0.5, 0.8, 0.25),
        _pair("C", 0.5, 0.8, None),
        _pair("D", 0.0, 0.0, 0.3),
        _pair("E", 1.0, 1.6, 3.0, provider="codex"),
        _pair("F", 1.0, 1.6, 3.0, task_type="bug"),
        _pair("G", float("nan"), 1.6, 3.0),
    )
    assert spread_ratios(items, "claude", "feature") == (1.5, 0.5)


def test_actuals_come_from_recorded_run_costs() -> None:
    runs = (
        Run(id="M1", kind="mandate", status="ok", cost_usd=0.4),
        Run(id="X1", kind="cross", status="ok", cost_usd=0.1),
        Run(id="X1a", kind="cross", status="ok", cost_usd=0.2, parent_id="X1"),
        Run(id="X1b", kind="cross", status="ok", cost_usd=0.3, parent_id="X1"),
        Run(id="X2", kind="cross", status="ok", cost_usd=0.1),
        Run(id="X2a", kind="cross", status="ok", cost_usd=None, parent_id="X2"),
        Run(id="R", kind="mandate", cost_usd=0.2),
        Run(id="Z", kind="mandate", status="ok", cost_usd=0.0),
        Run(id="K", kind="mandate", status="ok", cost_usd=0.0, cost_source="reported"),
    )
    names = ("M1", "X1", "X2", "R", "Z", "K", "missing")
    paired = forecast_actuals(tuple(_forecast(name, 0.5, 0.8) for name in names), runs)
    actuals = {item.forecast.run_id: item.actual_usd for item in paired}
    assert actuals["M1"] == 0.4
    assert actuals["X1"] == pytest.approx(0.6)
    assert {name: actuals[name] for name in ("X2", "R", "Z", "missing")} == dict.fromkeys(
        ("X2", "R", "Z", "missing")
    )
    assert actuals["K"] == 0.0
