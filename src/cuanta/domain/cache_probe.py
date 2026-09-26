from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from cuanta.domain.errors import DomainFailure
from cuanta.domain.messages import Message, msg
from cuanta.domain.models import Access
from cuanta.domain.pricing import Price

DEFAULT_GAPS_S = (60, 360)
LONG_GAP_S = 3660
DEFAULT_BUDGET_USD = 0.10
PER_RUN_USD = 0.05
DEFAULT_TOOLS = ("Read", "Glob")
REFERENCE_MAX_GAP_S = 300
READ_COVERAGE = 0.9
ASSUMED_PREFIX_TOKENS = 30_000
ONE_HOUR_WRITE_FACTOR = 2.0
_GAP = re.compile(r"([1-9]\d*)([smh]?)\Z")


class Warmth(StrEnum):
    WARM = "warm"
    COLD = "cold"
    UNKNOWN = "unknown"


class Verdict(StrEnum):
    MEASURED = "measured"
    CONSISTENT = "consistent"
    LOWER_BOUND = "lower_bound"
    UNCACHEABLE = "uncacheable"
    UNSTABLE = "unstable"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class CacheReading:
    index: int
    gap_s: float
    run_id: str
    ok: bool
    has_usage: bool
    cache_read: int
    cache_write: int
    write_5m: int
    write_1h: int
    fresh_input: int
    cost_usd: float | None
    engine_version: str
    api_key_source: str
    local_date: str

    @property
    def prefix_tokens(self) -> int:
        return self.cache_read + self.cache_write + self.fresh_input


@dataclass(frozen=True, slots=True)
class CacheTtlResult:
    verdict: Verdict
    ttl_s: int
    lower_s: int
    upper_s: int | None
    declared_s: int | None
    seed: CacheReading
    readings: tuple[CacheReading, ...]
    warmth: tuple[Warmth, ...]
    skipped_gaps_s: tuple[int, ...]
    spent_usd: float | None
    auth: Access
    api_key_source: str
    engine_version: str
    model: str
    measured_on: str
    tools: tuple[str, ...]

    @property
    def saveable(self) -> bool:
        return self.verdict in {Verdict.MEASURED, Verdict.CONSISTENT, Verdict.LOWER_BOUND}

    @property
    def message(self) -> Message:
        if self.verdict is Verdict.MEASURED:
            return msg(
                "cache_probe.verdict.measured",
                ttl=self.ttl_s,
                lower=self.lower_s,
                upper=self.upper_s,
            )
        if self.verdict is Verdict.CONSISTENT:
            return msg("cache_probe.verdict.consistent", ttl=self.ttl_s, lower=self.lower_s)
        if self.verdict is Verdict.LOWER_BOUND:
            return msg("cache_probe.verdict.lower_bound", lower=self.lower_s)
        return msg(f"cache_probe.verdict.{self.verdict.value}")


@dataclass(frozen=True, slots=True)
class CacheProbePlan:
    gaps_s: tuple[int, ...]
    budget_usd: float
    per_run_usd: float
    worst_run_usd: float | None
    worst_case_usd: float
    affordable_gaps_s: tuple[int, ...]


def parse_gaps(text: str) -> tuple[int, ...]:
    parts = [part.strip().lower() for part in text.split(",")]
    if not parts or any(not part for part in parts):
        raise DomainFailure("give one or more positive cache-probe gaps")
    values: set[int] = set()
    for part in parts:
        found = _GAP.fullmatch(part)
        if found is None:
            raise DomainFailure(f"invalid cache-probe gap: {part}", "use seconds, m or h")
        number = int(found.group(1))
        values.add(number * {"": 1, "s": 1, "m": 60, "h": 3600}[found.group(2)])
    gaps = tuple(sorted(values))
    if gaps[0] > REFERENCE_MAX_GAP_S:
        raise DomainFailure("the first cache-probe gap must be at most 300 seconds")
    return gaps


def probe_prompt() -> str:
    return "Reply with the single word OK."


def access_of(api_key_source: str) -> Access:
    if not api_key_source:
        return Access.UNKNOWN
    return Access.SUBSCRIPTION if api_key_source == "none" else Access.API


def declared_ttl(seed: CacheReading) -> int | None:
    if seed.write_1h > 0:
        return 3600
    if seed.write_5m > 0:
        return 300
    return None


def classify(seed: CacheReading, reference: CacheReading, reading: CacheReading) -> Warmth:
    if not reading.ok or not reading.has_usage:
        return Warmth.UNKNOWN
    if reading.engine_version != seed.engine_version or reading.local_date != seed.local_date:
        return Warmth.UNKNOWN
    if reading.api_key_source != seed.api_key_source:
        return Warmth.UNKNOWN
    if reference.cache_read == 0:
        return Warmth.COLD
    return (
        Warmth.WARM if reading.cache_read >= READ_COVERAGE * reference.cache_read else Warmth.COLD
    )


def decide(
    seed: CacheReading,
    readings: tuple[CacheReading, ...],
    skipped_gaps_s: tuple[int, ...],
    spent_usd: float | None,
    model: str,
    measured_on: str,
    tools: tuple[str, ...],
) -> CacheTtlResult:
    declared = declared_ttl(seed)
    warmth = tuple(Warmth.UNKNOWN for _ in readings)
    lower = 0
    upper: int | None = None
    verdict = Verdict.INCONCLUSIVE
    ttl = 0
    if seed.ok and seed.has_usage:
        if seed.cache_read + seed.cache_write == 0:
            verdict = Verdict.UNCACHEABLE
        elif readings and readings[0].ok and readings[0].has_usage:
            reference = readings[0]
            warmth = tuple(classify(seed, reference, item) for item in readings)
            if reference.cache_read == 0 and warmth[0] is not Warmth.UNKNOWN:
                verdict = Verdict.UNSTABLE
            else:
                valid = [
                    (round(item.gap_s), state) for item, state in zip(readings, warmth, strict=True)
                ]
                warm_gaps = [gap for gap, state in valid if state is Warmth.WARM]
                cold_gaps = [gap for gap, state in valid if state is Warmth.COLD]
                lower = max(warm_gaps, default=0)
                upper = min(cold_gaps) if cold_gaps else None
                if lower and (upper is None or lower < upper):
                    if upper is not None:
                        verdict = Verdict.MEASURED
                        ttl = lower
                        if declared is not None and lower <= declared < upper:
                            ttl = declared
                    else:
                        verdict = Verdict.LOWER_BOUND
                        ttl = lower
    return CacheTtlResult(
        verdict=verdict,
        ttl_s=ttl,
        lower_s=lower,
        upper_s=upper,
        declared_s=declared,
        seed=seed,
        readings=readings,
        warmth=warmth,
        skipped_gaps_s=skipped_gaps_s,
        spent_usd=spent_usd,
        auth=access_of(seed.api_key_source),
        api_key_source=seed.api_key_source,
        engine_version=seed.engine_version,
        model=model,
        measured_on=measured_on,
        tools=tools,
    )


def worst_run_cost(price: Price | None, prefix_tokens: int) -> float | None:
    if price is None:
        return None
    return (prefix_tokens * price.input * ONE_HOUR_WRITE_FACTOR + 16 * price.output) / 1_000_000


def plan_ceiling(
    price: Price | None, gaps: tuple[int, ...], budget_usd: float, per_run_usd: float
) -> CacheProbePlan:
    worst = worst_run_cost(price, ASSUMED_PREFIX_TOKENS)
    affordable = max(0, int((budget_usd + 1e-9) / per_run_usd) - 1)
    return CacheProbePlan(
        gaps_s=gaps,
        budget_usd=budget_usd,
        per_run_usd=per_run_usd,
        worst_run_usd=worst,
        worst_case_usd=per_run_usd * (len(gaps) + 1),
        affordable_gaps_s=gaps[:affordable],
    )
