from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from cuanta.domain.real_costs import MIX_ORDER, TYPE_ORDER, Attempt, cost_row

DEFAULT_TREND_RUNS = 20
SPARK_GLYPHS = "▁▂▃▄▅▆▇█"
ASCII_GLYPHS = "01234567"


@dataclass(frozen=True, slots=True)
class TrendRow:
    provider: str
    task_type: str
    runs: int
    accepted: int
    per_accepted_usd: float | None
    lower_bound: bool
    estimated: bool
    series: tuple[float | None, ...]


@dataclass(frozen=True, slots=True)
class CostTrend:
    limit: int = DEFAULT_TREND_RUNS
    runs: int = 0
    rows: tuple[TrendRow, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.rows


def _rank(order: Sequence[str], key: str) -> tuple[int, str]:
    return (order.index(key) if key in order else len(order), key)


def trend_row(provider: str, task_type: str, group: Sequence[Attempt]) -> TrendRow:
    row = cost_row(task_type, group)
    known = row.per_accepted is not None
    return TrendRow(
        provider,
        task_type,
        row.runs,
        row.accepted,
        row.per_accepted,
        row.lower_bound and known,
        row.estimated and known,
        tuple(cost_row(task_type, group[:end]).per_accepted for end in range(1, len(group) + 1)),
    )


def cost_trend(items: Sequence[Attempt], limit: int = DEFAULT_TREND_RUNS) -> CostTrend:
    if limit < 1:
        raise ValueError(f"a cost trend needs at least one run, not {limit}")
    groups: dict[tuple[str, str], list[Attempt]] = {}
    for item in sorted(items, key=lambda item: (item.run.started_at, item.run.id)):
        groups.setdefault((item.mix, item.type), []).append(item)
    keys = sorted(groups, key=lambda key: (_rank(MIX_ORDER, key[0]), _rank(TYPE_ORDER, key[1])))
    rows = tuple(
        trend_row(provider, kind, groups[provider, kind][-limit:]) for provider, kind in keys
    )
    return CostTrend(limit, sum(row.runs for row in rows), rows)


def spark_levels(
    series: Sequence[float | None], steps: int = len(SPARK_GLYPHS)
) -> tuple[int | None, ...]:
    known = [value for value in series if value is not None]
    if not known:
        return tuple(None for _ in series)
    low = min(known)
    span = max(known) - low
    return tuple(
        None if value is None else 0 if span <= 0 else round((value - low) / span * (steps - 1))
        for value in series
    )


def sparkline(series: Sequence[float | None], glyphs: str = SPARK_GLYPHS, blank: str = "·") -> str:
    return "".join(
        blank if level is None else glyphs[level] for level in spark_levels(series, len(glyphs))
    )


def trend_payload(trend: CostTrend) -> dict[str, object]:
    return {
        "limit": trend.limit,
        "runs": trend.runs,
        "rows": [
            {
                "provider": row.provider,
                "task_type": row.task_type,
                "runs": row.runs,
                "accepted": row.accepted,
                "cost_per_accepted_usd": row.per_accepted_usd,
                "cost_per_accepted_lower_bound": row.lower_bound,
                "cost_per_accepted_includes_estimated": row.estimated,
                "series": list(row.series),
            }
            for row in trend.rows
        ],
    }
