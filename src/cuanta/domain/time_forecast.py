from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from cuanta.domain.costs import median
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.messages import Message, msg
from cuanta.domain.pricing import base_model
from cuanta.domain.real_costs import Attempt, type_key
from cuanta.domain.routing import percentile
from cuanta.domain.time_anatomy import analyze_time


@dataclass(frozen=True, slots=True)
class TimeForecast:
    task_type: str = ""
    model: str = ""
    variant: str = ""
    profile: str = ""
    p50_seconds: float | None = None
    p90_seconds: float | None = None
    samples: int = 0

    @property
    def message(self) -> Message:
        return msg(
            "envelope.time",
            p50=f"{self.p50_seconds:.1f}s" if self.p50_seconds is not None else "n/a",
            p90=f"{self.p90_seconds:.1f}s" if self.p90_seconds is not None else "n/a",
            samples=self.samples,
        )


def time_forecast(
    task_type: str,
    model: str,
    variant: str,
    profile: str,
    items: Sequence[Attempt],
    events: Sequence[LedgerEvent],
    metadata: Mapping[str, Mapping[str, object]],
    aliases: Mapping[str, str] | None = None,
) -> TimeForecast:
    names = aliases or {}

    def normalized(value: str) -> str:
        return ", ".join(
            sorted({base_model(names.get(part, part)) for part in value.split(", ") if part})
        )

    key = type_key(task_type), normalized(model), variant, profile
    unknown = TimeForecast(*key)
    if not all(key):
        return unknown
    by_run: dict[str, list[LedgerEvent]] = defaultdict(list)
    for event in events:
        by_run[event.run_id].append(event)
    values: list[float] = []
    for item in items:
        meta = metadata.get(item.run.id, {})
        selected = [event for run_id in item.run_ids for event in by_run[run_id]]
        models = {normalized(event.model) for event in selected if event.model}
        found_model = ", ".join(sorted(models)) or normalized(item.run.model)
        found_variant = str(meta.get("variant") or meta.get("effort") or "")
        found_profile = str(meta.get("implementation_profile") or meta.get("profile") or "")
        if (type_key(item.type), found_model, found_variant, found_profile) != key:
            continue
        seconds = analyze_time(item.run, selected).wall_seconds
        if seconds is not None and math.isfinite(seconds) and seconds >= 0:
            values.append(seconds)
    if not values:
        return unknown
    return TimeForecast(*key, median(values), percentile(values, 0.9), len(values))
