from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from cuanta.domain.costs import CostSource, sum_costs
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.messages import Message, english, msg

PER_MILLION = 1_000_000
_DATE_SUFFIX = re.compile(r"-\d{8}$")
_BRACKET_SUFFIX = re.compile(r"\[[^\]]*\]$")


@dataclass(frozen=True, slots=True)
class Price:
    input: float
    output: float
    cache_write: float | None
    cache_read: float | None


@dataclass(frozen=True, slots=True)
class PriceTable:
    models: Mapping[str, Price] = field(default_factory=dict)
    verified_on: str = ""
    version: int = 0

    def lookup(self, model: str) -> Price | None:
        name = _BRACKET_SUFFIX.sub("", model.strip().lower())
        if name in self.models:
            return self.models[name]
        undated = _DATE_SUFFIX.sub("", name)
        return self.models.get(undated)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self.models))

    @property
    def label(self) -> str:
        return f"price table v{self.version}, verified {self.verified_on}"


def event_cost(event: LedgerEvent, price: Price) -> float | None:
    buckets = (
        (event.input_tokens, price.input),
        (event.output_tokens + event.reasoning_tokens, price.output),
        (event.cache_write_tokens, price.cache_write),
        (event.cache_read_tokens, price.cache_read),
    )
    total = 0.0
    for tokens, rate in buckets:
        if tokens == 0:
            continue
        if rate is None:
            return None
        total += tokens * rate
    return total / PER_MILLION


@dataclass(frozen=True, slots=True)
class CostEstimate:
    value: float | None
    source: str
    unpriced: tuple[str, ...] = ()
    message: Message | None = None
    kind: CostSource = "unknown"

    @property
    def known(self) -> bool:
        return self.value is not None


def dollars(value: float | None) -> str:
    return "cost n/a" if value is None else f"${value:.2f}"


def estimate(
    value: float | None,
    message: Message,
    unpriced: tuple[str, ...] = (),
    kind: CostSource = "unknown",
) -> CostEstimate:
    return CostEstimate(value, english(message), unpriced, message, kind)


def estimate_cost(usage: Iterable[LedgerEvent], table: PriceTable | None) -> CostEstimate:
    events = list(usage)
    if not events:
        return estimate(0.0, msg("cost.no_usage"))
    reported = sum_costs(event.cost_usd for event in events)
    if reported is not None:
        return estimate(reported, msg("cost.engine"), kind="reported")
    if any(event.cost_usd is None and event.total_tokens == 0 for event in events):
        return estimate(None, msg("cost.missing_usage"))
    if table is None:
        return estimate(None, msg("cost.no_table"))
    total = 0.0
    unpriced: list[str] = []
    for event in events:
        if event.cost_usd is not None:
            total += event.cost_usd
            continue
        price = table.lookup(event.model)
        value = event_cost(event, price) if price is not None else None
        if value is None:
            unpriced.append(event.model or "unknown model")
            continue
        total += value
    if unpriced:
        return estimate(None, msg("cost.unknown_price"), tuple(dict.fromkeys(unpriced)))
    return estimate(total, msg("cost.table", table=table.label), kind="estimated")
