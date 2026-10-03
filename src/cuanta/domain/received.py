from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from cuanta.domain.engine import EngineEvent, EngineOutcome, ModelUsage, SessionStarted, StepUsage
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.messages import msg
from cuanta.domain.pricing import CostEstimate, PriceTable, estimate, event_cost
from cuanta.domain.spectrum import USAGE_KINDS


@dataclass
class Received:
    turns: int = 0
    requests: int = 0
    session_id: str = ""
    seen: set[str] = field(default_factory=set)
    models: dict[str, ModelUsage] = field(default_factory=dict)

    def add(self, event: EngineEvent) -> None:
        if isinstance(event, SessionStarted):
            self.session_id = self.session_id or event.session_id
            return
        if not isinstance(event, StepUsage) or event.usage.total <= 0:
            return
        if event.message_id:
            if event.message_id in self.seen:
                return
            self.seen.add(event.message_id)
        self.requests += 1
        if not event.parent_tool_use_id:
            self.turns += 1
        usage = event.usage
        before = self.models.get(usage.model, ModelUsage(usage.model))
        self.models[usage.model] = ModelUsage(
            usage.model,
            before.input_tokens + usage.input_tokens,
            before.output_tokens + usage.output_tokens,
            before.cache_read_tokens + usage.cache_read_tokens,
            before.cache_write_tokens + usage.cache_write_tokens,
            before.reasoning_tokens + usage.reasoning_tokens,
        )

    @property
    def usage(self) -> tuple[ModelUsage, ...]:
        return tuple(self.models.values())

    @property
    def main_model(self) -> str:
        if not self.models:
            return ""
        return max(self.models.values(), key=lambda usage: usage.total).model


def ended_early(outcome: EngineOutcome | None) -> bool:
    return outcome is None or outcome.result is None or outcome.result.partial


def received_cost(
    reported: float | None,
    recorded: Sequence[LedgerEvent],
    streamed: Sequence[LedgerEvent],
    table: PriceTable | None,
) -> CostEstimate:
    usage = [event for event in recorded if event.kind in USAGE_KINDS] or list(streamed)
    total = 0.0
    priced = 0
    reported_rows = 0
    unpriced: list[str] = []
    for event in usage:
        if event.cost_usd is not None:
            total += event.cost_usd
            priced += 1
            reported_rows += 1
            continue
        if not event.total_tokens:
            continue
        price = table.lookup(event.model) if table is not None else None
        value = event_cost(event, price) if price is not None else None
        if value is None:
            unpriced.append(event.model or "unknown model")
            continue
        total += value
        priced += 1
    missing = tuple(dict.fromkeys(unpriced))
    if reported is not None and (not priced or reported >= total):
        return estimate(reported, msg("cost.engine"), missing, kind="reported")
    if not priced:
        return estimate(None, msg("cost.missing_usage"), missing)
    if reported_rows == priced:
        return estimate(total, msg("cost.engine"), missing, kind="reported")
    label = table.label if table is not None else ""
    return estimate(total, msg("cost.table", table=label), missing, kind="estimated")
