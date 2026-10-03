from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from cuanta.domain.ledger import Forecast, Run
from cuanta.domain.real_costs import attempts, type_key


@dataclass(frozen=True, slots=True)
class ForecastActual:
    forecast: Forecast
    actual_usd: float | None

    @property
    def known(self) -> bool:
        return self.actual_usd is not None


@dataclass(frozen=True, slots=True)
class Calibration:
    provider: str
    task_type: str
    samples: int
    unknown: int
    mae_usd: float | None
    mape: float | None
    p90_coverage: float | None
    p90_error: float | None


def _finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) and value >= 0 else None


def forecast_actuals(
    forecasts: Sequence[Forecast], runs: Iterable[Run]
) -> tuple[ForecastActual, ...]:
    costs = {item.run.id: None if item.partial else _finite(item.cost) for item in attempts(runs)}
    return tuple(ForecastActual(forecast, costs.get(forecast.run_id)) for forecast in forecasts)


def same_type(left: str, right: str) -> bool:
    return type_key(left) == type_key(right)


def forecast_matches(forecast: Forecast, provider: str = "", task_type: str = "") -> bool:
    return (not provider or forecast.provider == provider) and (
        not task_type or same_type(forecast.task_type, task_type)
    )


def _usable(item: ForecastActual) -> bool:
    forecast = item.forecast
    return (
        item.actual_usd is not None
        and math.isfinite(forecast.p50_usd)
        and math.isfinite(forecast.p90_usd)
        and forecast.p50_usd >= 0
        and forecast.p90_usd >= 0
    )


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _calibration(provider: str, task_type: str, group: Sequence[ForecastActual]) -> Calibration:
    pairs = [
        (item.forecast.p50_usd, item.forecast.p90_usd, item.actual_usd)
        for item in group
        if _usable(item) and item.actual_usd is not None
    ]
    positive = [(p50, p90, actual) for p50, p90, actual in pairs if actual > 0]
    return Calibration(
        provider=provider,
        task_type=task_type,
        samples=len(pairs),
        unknown=len(group) - len(pairs),
        mae_usd=_mean([abs(p50 - actual) for p50, _, actual in pairs]),
        mape=_mean([abs(p50 - actual) / actual for p50, _, actual in positive]),
        p90_coverage=_mean([1.0 if actual <= p90 else 0.0 for _, p90, actual in pairs]),
        p90_error=_mean([abs(p90 - actual) / actual for _, p90, actual in positive]),
    )


def calibrate(
    items: Sequence[ForecastActual], provider: str = "", task_type: str = ""
) -> Calibration:
    group = [item for item in items if forecast_matches(item.forecast, provider, task_type)]
    return _calibration(provider, type_key(task_type) if task_type else "", group)


def calibration_rows(items: Sequence[ForecastActual]) -> tuple[Calibration, ...]:
    groups: dict[tuple[str, str], list[ForecastActual]] = {}
    for item in items:
        key = (item.forecast.provider, type_key(item.forecast.task_type))
        groups.setdefault(key, []).append(item)
    return tuple(
        _calibration(provider, task_type, groups[provider, task_type])
        for provider, task_type in sorted(groups)
    )


def spread_ratios(
    items: Sequence[ForecastActual], provider: str, task_type: str
) -> tuple[float, ...]:
    return tuple(
        item.actual_usd / item.forecast.p50_usd
        for item in items
        if forecast_matches(item.forecast, provider, task_type)
        and _usable(item)
        and item.actual_usd is not None
        and item.forecast.p50_usd > 0
    )
