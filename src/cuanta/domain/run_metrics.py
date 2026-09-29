from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from cuanta.domain.agents import role_of
from cuanta.domain.anatomy import Phase, analyze_anatomy
from cuanta.domain.governor import ROTATED, ReactionKind
from cuanta.domain.governor_report import BlockedCalls, GovernorEntry, GovernorSummary
from cuanta.domain.ledger import Forecast, LedgerEvent, Run
from cuanta.domain.outcomes import ACCEPTED
from cuanta.domain.real_costs import Attempt
from cuanta.domain.report import AUXILIARY_SOURCES, first_request_event
from cuanta.domain.routing import Role
from cuanta.domain.run_mode import V5
from cuanta.domain.scout_report import ScoutSummary
from cuanta.domain.spectrum import FALLBACK_KINDS, USAGE_KINDS

BUCKETS = ("start", "exploration", "writing", "verification", "handoff")
FINISH_KINDS = frozenset({ReactionKind.FINISH_NOW.value, ReactionKind.CODEX_STOP.value})
SENIOR_SCOPE = Role.SENIOR.value
USD_DIGITS = 6
SHARE_DIGITS = 4


@dataclass(frozen=True, slots=True)
class BucketMetric:
    name: str
    forecast: int | None = None
    actual: int | None = None


@dataclass(frozen=True, slots=True)
class Reactions:
    count: int
    saved_usd: float | None


@dataclass(frozen=True, slots=True)
class RunMetrics:
    run_id: str = ""
    provider: str = ""
    task_type: str = ""
    started_at: str = ""
    outcome: str = ""
    p50_usd: float | None = None
    p90_usd: float | None = None
    cap_usd: float | None = None
    actual_usd: float | None = None
    estimated: bool = False
    buckets: tuple[BucketMetric, ...] = ()
    total_tokens: int | None = None
    changed_lines: int | None = None
    blocked: BlockedCalls | None = None
    finishes: Reactions | None = None
    rotations: Reactions | None = None
    first_warm_share: float | None = None
    warm_share: float | None = None
    pack_tokens: int | None = None
    senior_input_tokens: int | None = None
    mode: str = V5

    @property
    def shown(self) -> bool:
        return bool(self.run_id)

    @property
    def accepted(self) -> bool:
        return self.outcome == ACCEPTED

    @property
    def cap_used(self) -> float | None:
        if self.actual_usd is None or self.cap_usd is None or self.cap_usd <= 0:
            return None
        return self.actual_usd / self.cap_usd

    @property
    def p90_left_usd(self) -> float | None:
        if self.actual_usd is None or self.p90_usd is None:
            return None
        return self.p90_usd - self.actual_usd

    @property
    def per_accepted_usd(self) -> float | None:
        return self.actual_usd if self.accepted else None

    @property
    def tokens_per_line(self) -> float | None:
        if not self.accepted or self.total_tokens is None or not self.changed_lines:
            return None
        return self.total_tokens / self.changed_lines


def _auxiliary(event: LedgerEvent) -> bool:
    return event.query_source in AUXILIARY_SOURCES or event.agent in AUXILIARY_SOURCES


def agent_requests(events: Sequence[LedgerEvent]) -> list[LedgerEvent]:
    return [event for event in events if event.kind in USAGE_KINDS and not _auxiliary(event)]


def usage_of(events: Sequence[LedgerEvent]) -> list[LedgerEvent]:
    primary = {event.run_id for event in events if event.kind in USAGE_KINDS}
    return [
        event
        for event in events
        if event.kind in USAGE_KINDS
        or (event.kind in FALLBACK_KINDS and event.run_id not in primary)
    ]


def _input(event: LedgerEvent) -> int:
    return event.input_tokens + event.cache_read_tokens + event.cache_write_tokens


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def forecast_buckets(forecast: Forecast | None) -> dict[str, int]:
    if forecast is None:
        return {}
    try:
        data = json.loads(forecast.buckets)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {name: value for name in BUCKETS if (value := _count(data.get(name))) is not None}


def actual_buckets(resolved: Sequence[LedgerEvent]) -> dict[str, int]:
    requests = agent_requests(resolved)
    if not requests:
        return {}
    start = growth = handoff = written = 0
    for item in analyze_anatomy(resolved, requests).events:
        totals = item.totals
        added = totals.fresh_input + totals.cache_write
        written += totals.output + totals.reasoning
        if item.phase is Phase.START:
            start += added + totals.cache_read
        elif item.phase is Phase.HANDOFF:
            handoff += added
        else:
            growth += added
    return {"start": start, "exploration": growth, "writing": written, "handoff": handoff}


def bucket_metrics(
    forecast: Forecast | None, resolved: Sequence[LedgerEvent]
) -> tuple[BucketMetric, ...]:
    planned = forecast_buckets(forecast)
    observed = actual_buckets(resolved)
    return tuple(BucketMetric(name, planned.get(name), observed.get(name)) for name in BUCKETS)


def _share(read: int, total: int) -> float | None:
    return read / total if total > 0 else None


def first_request_share(events: Sequence[LedgerEvent]) -> float | None:
    by_run: dict[str, list[LedgerEvent]] = {}
    for event in events:
        by_run.setdefault(event.run_id, []).append(event)
    read = total = 0
    for group in by_run.values():
        first = first_request_event(group)
        if first is not None:
            read += first.cache_read_tokens
            total += _input(first)
    return _share(read, total)


def warm_share(usage: Sequence[LedgerEvent]) -> float | None:
    return _share(sum(event.cache_read_tokens for event in usage), sum(map(_input, usage)))


def senior_input(resolved: Sequence[LedgerEvent], runs: Sequence[Run]) -> int | None:
    seniors = {run.id for run in runs if run.scope == SENIOR_SCOPE}
    selected = [
        event
        for event in usage_of(resolved)
        if event.run_id in seniors or role_of(event.agent) is Role.SENIOR
    ]
    return sum(map(_input, selected)) if selected else None


def _reactions(entries: Sequence[GovernorEntry]) -> Reactions:
    saved = [entry.saved_usd for entry in entries if entry.saved_usd is not None]
    return Reactions(len(entries), sum(saved) if saved else None)


def governor_counts(governor: GovernorSummary) -> tuple[Reactions | None, Reactions | None]:
    if not governor.recorded and not governor.reactions and not governor.discipline:
        return None, None
    sent = [entry for entry in governor.reactions if entry.sent]
    finishes = [entry for entry in sent if entry.kind in FINISH_KINDS]
    rotations = [
        entry
        for entry in sent
        if entry.kind == ReactionKind.ROTATE.value and entry.outcome == ROTATED
    ]
    return _reactions(finishes), _reactions(rotations)


def known_blocked(governor: GovernorSummary) -> BlockedCalls | None:
    blocked = governor.blocked
    if blocked is None or not (blocked.total or governor.enforced or governor.hooks):
        return None
    return blocked


def _usd(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) and value >= 0 else None


def run_metrics(
    attempt: Attempt | None,
    roles: Sequence[Run],
    resolved: Sequence[LedgerEvent],
    forecast: Forecast | None,
    governor: GovernorSummary,
    scout: ScoutSummary,
    changed_lines: int | None,
    mode: str = V5,
) -> RunMetrics:
    if attempt is None:
        return RunMetrics()
    run = attempt.run
    usage = usage_of(resolved)
    finishes, rotations = governor_counts(governor)
    return RunMetrics(
        run_id=run.id,
        provider=attempt.mix,
        task_type=attempt.type,
        started_at=run.started_at,
        outcome=run.outcome,
        p50_usd=_usd(forecast.p50_usd) if forecast is not None else None,
        p90_usd=_usd(forecast.p90_usd) if forecast is not None else None,
        cap_usd=(
            forecast.cap_usd
            if forecast is not None and forecast.cap_usd is not None
            else run.cap_usd
        ),
        actual_usd=attempt.cost,
        estimated=attempt.estimated,
        buckets=bucket_metrics(forecast, resolved),
        total_tokens=sum(event.total_tokens for event in usage) if usage else None,
        changed_lines=changed_lines,
        blocked=known_blocked(governor),
        finishes=finishes,
        rotations=rotations,
        first_warm_share=first_request_share(resolved),
        warm_share=warm_share(usage),
        pack_tokens=scout.tokens if scout.mode else None,
        senior_input_tokens=senior_input(resolved, (run, *roles)),
        mode=mode,
    )


def _rounded(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def _reaction_payload(reactions: Reactions | None) -> Mapping[str, object]:
    if reactions is None:
        return {"count": None, "saved_usd": None}
    return {"count": reactions.count, "saved_usd": _rounded(reactions.saved_usd, USD_DIGITS)}


def metrics_payload(metrics: RunMetrics) -> dict[str, object]:
    blocked = metrics.blocked
    per_line = metrics.tokens_per_line
    return {
        "run_id": metrics.run_id,
        "provider": metrics.provider,
        "task_type": metrics.task_type,
        "mode": metrics.mode,
        "started_at": metrics.started_at,
        "outcome": metrics.outcome or None,
        "forecast": {
            "p50_usd": metrics.p50_usd,
            "p90_usd": metrics.p90_usd,
            "cap_usd": metrics.cap_usd,
            "buckets": {
                item.name: {"forecast_tokens": item.forecast, "actual_tokens": item.actual}
                for item in metrics.buckets
            },
        },
        "actual_usd": metrics.actual_usd,
        "actual_estimated": metrics.estimated and metrics.actual_usd is not None,
        "cap_used": _rounded(metrics.cap_used, SHARE_DIGITS),
        "p90_minus_actual_usd": _rounded(metrics.p90_left_usd, USD_DIGITS),
        "cost_per_accepted_usd": metrics.per_accepted_usd,
        "total_tokens": metrics.total_tokens,
        "changed_lines": metrics.changed_lines,
        "tokens_per_accepted_line": None if per_line is None else round(per_line, 1),
        "blocked": {
            "reads": blocked.reads if blocked is not None else None,
            "calls": blocked.total if blocked is not None else None,
            "tokens_estimate": blocked.tokens if blocked is not None else None,
        },
        "finishes": _reaction_payload(metrics.finishes),
        "rotations": _reaction_payload(metrics.rotations),
        "warm_share": {
            "first_requests": _rounded(metrics.first_warm_share, SHARE_DIGITS),
            "overall": _rounded(metrics.warm_share, SHARE_DIGITS),
        },
        "scout_pack_tokens": metrics.pack_tokens,
        "senior_input_tokens": metrics.senior_input_tokens,
    }
