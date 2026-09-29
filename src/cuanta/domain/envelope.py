from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

from cuanta.domain.cache import PrefixState
from cuanta.domain.cache_probe import ONE_HOUR_WRITE_FACTOR
from cuanta.domain.costs import median, sum_costs
from cuanta.domain.depth import TOKENS_PER_READ, Depth, DepthProfile, profile
from cuanta.domain.evidence_pack import PACK_TOKENS
from cuanta.domain.ledger import Forecast
from cuanta.domain.mandate import Shape
from cuanta.domain.messages import Message, keyed, msg
from cuanta.domain.pricing import PER_MILLION, Price, base_model, dollars
from cuanta.domain.real_costs import FIX_TYPE, type_key
from cuanta.domain.role_budgets import MIN_SAMPLES
from cuanta.domain.role_handoff import handoff_budget
from cuanta.domain.routing import Provider, Role, percentile

SINGLE_SHAPE = Shape.SINGLE.value
PIPELINE_SHAPE = Shape.PIPELINE.value
SCOUT_SHAPE = Shape.SCOUT.value
ENVELOPE_SOURCE = "envelope"
JEV_SOURCE = "envelope+jev"

DEFAULT_SPREAD = 1.6
MIN_SPREAD_SAMPLES = 5
SPREAD_PERCENTILE = 0.9
TIGHT_MARGIN = 0.15
FACTOR_MIN = 0.25
FACTOR_MAX = 4.0

CLAUDE_FIXED: Mapping[Role, int] = {
    Role.ORCHESTRATOR: 19_700,
    Role.ANALYST: 19_500,
    Role.SENIOR: 47_900,
    Role.TESTER: 47_900,
    Role.DOCS: 34_800,
    Role.SCOUT: 19_500,
}
CLAUDE_MAIN_FIXED = 35_600
CLAUDE_SUBAGENT_FIXED: Mapping[Role, int] = {
    Role.ANALYST: 14_000,
    Role.SENIOR: 18_200,
    Role.TESTER: 18_400,
    Role.DOCS: 14_300,
    Role.SCOUT: 14_000,
}
CODEX_FIXED = 20_000
FIVE_MINUTE_TTL_S = 300
DISPATCH_TOKENS = 300
HANDOFF_TOKENS: Mapping[Role, int] = {Role.SENIOR: 160, Role.TESTER: 450, Role.DOCS: 660}
DEFAULT_HANDOFF = 450
SCOUT_PACK_TOKENS = PACK_TOKENS
SENIOR_READ_FILES = 2
TESTER_READ_FILES = 3
MAIN_READ_FILES = 6
DOCS_FILES = 7
DOCS_EDITS = 2
CODEX_REREAD_FILES = 2
CODEX_DOCS_FILES = 2
CODEX_OUTPUT_PER_REQUEST = 150
CLAUDE_SINGLE_REQUESTS = 5
CLAUDE_MAIN_REQUESTS = 16
CLAUDE_LAUNCH_REQUESTS: Mapping[Role, int] = {
    Role.ANALYST: 6,
    Role.SENIOR: 4,
    Role.TESTER: 5,
    Role.DOCS: 13,
    Role.SCOUT: 6,
}
CLAUDE_SUBAGENT_REQUESTS: Mapping[Role, int] = {
    Role.ANALYST: 3,
    Role.SENIOR: 8,
    Role.TESTER: 5,
    Role.DOCS: 17,
    Role.SCOUT: 3,
}
BASE_REQUESTS = 4
REQUESTS_PER_READ = 1
REQUESTS_PER_EDIT = 1
VERIFY_REQUESTS = 2
VERIFY_TOKENS = 2_000
OUTPUT_PER_REQUEST = 530
WRITE_TOKENS_PER_EDIT = 800
TEST_WRITE_TOKENS = 800
DOCS_WRITE_TOKENS = 1_500
REPAIR_WARMTH = 1.0
STOP_SLACK = 1.5
READ_SLACK = 2
LARGE_READ_SHARE = 0.5
EXPLORATION_DOMINANCE = 0.5
SHALLOWER: Mapping[Depth, Depth] = {Depth.DEEP: Depth.NORMAL, Depth.NORMAL: Depth.QUICK}
EXPLORERS = frozenset({Role.ANALYST, Role.SCOUT})


class Verdict(StrEnum):
    COMFORTABLE = "comfortable"
    TIGHT = "tight"
    INFEASIBLE = "infeasible"
    UNKNOWN = "unknown"


class SuggestionKind(StrEnum):
    DEPTH = "depth"
    TIER = "tier"
    WHERE = "where"
    SCOUT = "scout"


@dataclass(frozen=True, slots=True)
class RoleModel:
    model: str
    price: Price | None


@dataclass(frozen=True, slots=True)
class RoleSample:
    task_type: str
    ratio: float


@dataclass(frozen=True, slots=True)
class RoleInput:
    role: Role
    model: RoleModel
    fixed_tokens: int = 0
    history: tuple[RoleSample, ...] = ()
    cheaper: RoleModel | None = None


@dataclass(frozen=True, slots=True)
class EnvelopeInputs:
    task_type: str
    depth: DepthProfile
    shape: str
    provider: Provider
    roles: tuple[RoleInput, ...]
    cap_usd: float
    edit_tokens: tuple[int, ...] = ()
    read_tokens: tuple[int, ...] = ()
    warmth: float = 0.0
    calibration: tuple[float, ...] = ()
    economy: RoleModel | None = None
    risk: float = 1.0
    exploration: float = 1.0
    native: bool = False
    cache_ttl_s: int = 0
    max_turns: int = 0
    model_warmth: Mapping[str, float] = field(default_factory=dict)
    repairable: bool = False


@dataclass(frozen=True, slots=True)
class FixedPrefix:
    tokens: int
    measured: bool


@dataclass(frozen=True, slots=True)
class Buckets:
    start: int = 0
    exploration: int = 0
    writing: int = 0
    verification: int = 0
    handoff: int = 0

    @property
    def task(self) -> int:
        return self.exploration + self.writing + self.verification + self.handoff

    @property
    def total(self) -> int:
        return self.start + self.task

    def plus(self, other: Buckets) -> Buckets:
        return Buckets(
            self.start + other.start,
            self.exploration + other.exploration,
            self.writing + other.writing,
            self.verification + other.verification,
            self.handoff + other.handoff,
        )

    def payload(self) -> dict[str, int]:
        return {
            "start": self.start,
            "exploration": self.exploration,
            "writing": self.writing,
            "verification": self.verification,
            "handoff": self.handoff,
        }


@dataclass(frozen=True, slots=True)
class StopRules:
    max_turns: int
    max_reads: int
    max_output_tokens: int


@dataclass(frozen=True, slots=True)
class RoleForecast:
    role: Role
    model: str
    buckets: Buckets
    requests: int
    p50_usd: float | None
    p90_usd: float | None
    share: float
    stops: StopRules
    fixed: FixedPrefix
    factor: float
    repair: bool = False
    warmth: float = 0.0


@dataclass(frozen=True, slots=True)
class Suggestion:
    kind: SuggestionKind
    message: Message
    p90_usd: float
    saving_usd: float


@dataclass(frozen=True, slots=True)
class Envelope:
    task_type: str
    depth: str
    shape: str
    provider: Provider
    buckets: Buckets
    requests: int
    p50_usd: float | None
    p90_usd: float | None
    spread: float
    spread_samples: int
    cap_usd: float
    margin_usd: float | None
    verdict: Verdict
    warmth: float
    roles: tuple[RoleForecast, ...]
    suggestions: tuple[Suggestion, ...] = ()


@dataclass(frozen=True, slots=True)
class _Work:
    source: RoleInput
    model: RoleModel
    pack: int
    handoff_in: int
    files: tuple[int, ...]
    edits: int
    written: int
    verify_rounds: int
    warmth: float
    handoff_out: int = 0
    repair: bool = False
    main: bool = False
    dispatches: int = 0
    subagent: bool = False
    requests: int = 0


def is_fix(task_type: str) -> bool:
    return type_key(task_type) == FIX_TYPE


def repair_possible(shape: str, native: bool, verify: Sequence[str]) -> bool:
    return shape != SINGLE_SHAPE and not native and bool(verify)


def output_per_request(provider: Provider) -> int:
    return CODEX_OUTPUT_PER_REQUEST if provider is Provider.CODEX else OUTPUT_PER_REQUEST


def fixed_prefix(
    provider: Provider,
    role: Role,
    measured: int = 0,
    main: bool = False,
    subagent: bool = False,
) -> FixedPrefix:
    if subagent and provider is Provider.CLAUDE:
        return FixedPrefix(
            CLAUDE_SUBAGENT_FIXED.get(role, CLAUDE_SUBAGENT_FIXED[Role.SENIOR]), False
        )
    if measured > 0:
        return FixedPrefix(measured, True)
    if provider is Provider.CODEX:
        return FixedPrefix(CODEX_FIXED, False)
    if main:
        return FixedPrefix(CLAUDE_MAIN_FIXED, False)
    return FixedPrefix(CLAUDE_FIXED.get(role, CLAUDE_FIXED[Role.SENIOR]), False)


def claude_requests(role: Role, subagent: bool = False, explores: bool = False) -> int:
    counts = CLAUDE_SUBAGENT_REQUESTS if subagent else CLAUDE_LAUNCH_REQUESTS
    if explores:
        return counts[Role.ANALYST] + counts[Role.SENIOR]
    return counts.get(role, CLAUDE_SINGLE_REQUESTS)


def p90_spread(ratios: Sequence[float]) -> tuple[float, int]:
    valid = [value for value in ratios if math.isfinite(value) and value > 0]
    if len(valid) < MIN_SPREAD_SAMPLES:
        return DEFAULT_SPREAD, 0
    found = percentile(valid, SPREAD_PERCENTILE)
    return max(1.0, found if found is not None else DEFAULT_SPREAD), len(valid)


def verdict(p50: float | None, p90: float | None, cap: float) -> Verdict:
    if p50 is None or p90 is None:
        return Verdict.UNKNOWN
    if cap <= 0:
        return Verdict.COMFORTABLE
    if p50 > cap:
        return Verdict.INFEASIBLE
    if p90 > cap or cap - p90 < TIGHT_MARGIN * cap:
        return Verdict.TIGHT
    return Verdict.COMFORTABLE


def role_factor(source: RoleInput, task_type: str) -> float:
    wanted = type_key(task_type)
    ratios = [
        sample.ratio
        for sample in source.history
        if type_key(sample.task_type) == wanted and math.isfinite(sample.ratio) and sample.ratio > 0
    ]
    found = median(ratios) if len(ratios) >= MIN_SAMPLES else None
    return 1.0 if found is None else min(FACTOR_MAX, max(FACTOR_MIN, found))


def _size(tokens: int) -> int:
    return tokens if tokens > 0 else TOKENS_PER_READ


def _capped(files: Sequence[int], depth: DepthProfile) -> tuple[int, ...]:
    sized = tuple(_size(tokens) for tokens in files)
    return sized[: depth.read_budget] if depth.read_budget > 0 else sized


def _edit_files(inputs: EnvelopeInputs) -> tuple[int, ...]:
    return _capped(inputs.edit_tokens or (0,), inputs.depth)


def _edits(inputs: EnvelopeInputs) -> int:
    return max(1, len(inputs.edit_tokens))


def _warmth(value: float) -> float:
    return min(1.0, max(0.0, value)) if math.isfinite(value) else 0.0


def model_warmth(inputs: EnvelopeInputs, model: str) -> float:
    return _warmth(inputs.model_warmth.get(base_model(model), inputs.warmth))


def turn_limit(inputs: EnvelopeInputs) -> int:
    return inputs.max_turns if inputs.max_turns > 0 else inputs.depth.max_turns


def _single_works(inputs: EnvelopeInputs) -> tuple[_Work, ...]:
    if not inputs.roles:
        return ()
    source = inputs.roles[0]
    return (
        _Work(
            source,
            source.model,
            inputs.depth.pack_tokens,
            0,
            _capped((*inputs.edit_tokens, *inputs.read_tokens), inputs.depth),
            _edits(inputs),
            _edits(inputs) * WRITE_TOKENS_PER_EDIT,
            1,
            model_warmth(inputs, source.model.model),
            requests=CLAUDE_SINGLE_REQUESTS,
        ),
    )


def _scout_chain(inputs: EnvelopeInputs) -> tuple[RoleInput, ...]:
    scout = next((item for item in inputs.roles if item.role is Role.SCOUT), None)
    analyst = next((item for item in inputs.roles if item.role is Role.ANALYST), None)
    writers = tuple(
        item for item in inputs.roles if item.role not in {Role.ORCHESTRATOR, *EXPLORERS}
    )
    if not writers and inputs.roles:
        writers = (replace(inputs.roles[0], role=Role.SENIOR),)
    if scout is not None:
        return (scout, *writers)
    model = inputs.economy or (analyst.model if analyst is not None else None)
    if model is None:
        return _pipeline_chain(inputs)
    return (RoleInput(Role.SCOUT, model), *writers)


def _pipeline_chain(inputs: EnvelopeInputs) -> tuple[RoleInput, ...]:
    chain = tuple(item for item in inputs.roles if item.role not in {Role.ORCHESTRATOR, Role.SCOUT})
    return chain or inputs.roles[:1]


def _handoff(role: Role, previous: Role, scout: bool, depth: DepthProfile) -> int:
    if scout and previous is Role.SCOUT:
        return SCOUT_PACK_TOKENS
    return min(HANDOFF_TOKENS.get(role, DEFAULT_HANDOFF), handoff_budget(depth.depth.value))


def _explore(inputs: EnvelopeInputs) -> tuple[int, ...]:
    return _capped((*inputs.edit_tokens, *inputs.read_tokens), inputs.depth)


def _tester_files(inputs: EnvelopeInputs) -> tuple[int, ...]:
    tested = _edit_files(inputs)
    return tested if inputs.provider is Provider.CODEX else tested[:TESTER_READ_FILES]


def _docs_files(provider: Provider) -> int:
    return CODEX_DOCS_FILES if provider is Provider.CODEX else DOCS_FILES


def _senior_files(provider: Provider, edited: Sequence[int]) -> int:
    return len(edited) + CODEX_REREAD_FILES if provider is Provider.CODEX else SENIOR_READ_FILES


def _chain_work(
    inputs: EnvelopeInputs, source: RoleInput, explored: bool, scout: bool
) -> tuple[tuple[int, ...], int, int, int, bool]:
    if source.role in EXPLORERS:
        return _explore(inputs), 0, 0, 0, False
    if source.role is Role.TESTER:
        return _tester_files(inputs), 0, TEST_WRITE_TOKENS, 1, False
    if source.role is Role.DOCS:
        docs = (TOKENS_PER_READ,) * _docs_files(inputs.provider)
        return docs, DOCS_EDITS, DOCS_WRITE_TOKENS, 0, False
    edits = _edits(inputs)
    if not explored:
        return _explore(inputs), edits, edits * WRITE_TOKENS_PER_EDIT, 0, True
    edited = inputs.edit_tokens or (0,)
    pool = edited if scout else (*edited, *inputs.read_tokens)
    files = _capped(pool, inputs.depth)[: _senior_files(inputs.provider, edited)]
    return files, edits, edits * WRITE_TOKENS_PER_EDIT, 0, False


def _subagents(inputs: EnvelopeInputs) -> bool:
    return inputs.native and inputs.shape != SINGLE_SHAPE


def _chain_works(inputs: EnvelopeInputs, chain: Sequence[RoleInput], scout: bool) -> list[_Work]:
    works: list[_Work] = []
    explored = False
    subagent = _subagents(inputs)
    for index, source in enumerate(chain):
        files, edits, written, rounds, explores = _chain_work(inputs, source, explored, scout)
        explored = explored or source.role in EXPLORERS
        handoff_in = (
            _handoff(source.role, chain[index - 1].role, scout, inputs.depth) if index else 0
        )
        if works:
            works[-1] = replace(works[-1], handoff_out=handoff_in)
        works.append(
            _Work(
                source,
                source.model,
                inputs.depth.pack_tokens if index == 0 else 0,
                handoff_in,
                files,
                edits,
                written,
                rounds,
                model_warmth(inputs, source.model.model),
                subagent=subagent,
                requests=claude_requests(source.role, subagent, explores),
            )
        )
    return works


def _main_work(inputs: EnvelopeInputs, chain: Sequence[_Work]) -> _Work | None:
    source = next((item for item in inputs.roles if item.role is Role.ORCHESTRATOR), None)
    if source is None or not chain:
        return None
    return _Work(
        source,
        source.model,
        inputs.depth.pack_tokens,
        len(chain) * DEFAULT_HANDOFF,
        _explore(inputs)[:MAIN_READ_FILES],
        0,
        len(chain) * DISPATCH_TOKENS,
        0,
        model_warmth(inputs, source.model.model),
        main=True,
        dispatches=len(chain),
        requests=CLAUDE_MAIN_REQUESTS,
    )


def _repair(inputs: EnvelopeInputs, works: Sequence[_Work]) -> _Work | None:
    writer = next(
        (
            work
            for work in works
            if work.source.role is Role.SENIOR or (len(works) == 1 and work.edits > 0)
        ),
        None,
    )
    if writer is None:
        return None
    return _Work(
        writer.source,
        writer.model,
        0,
        0,
        _edit_files(inputs),
        writer.edits,
        writer.edits * WRITE_TOKENS_PER_EDIT,
        1,
        REPAIR_WARMTH,
        repair=True,
        subagent=writer.subagent,
        requests=writer.requests,
    )


def _works(inputs: EnvelopeInputs, calibrated: bool) -> tuple[_Work, ...]:
    if inputs.shape == SINGLE_SHAPE:
        works = list(_single_works(inputs))
    elif inputs.shape == SCOUT_SHAPE:
        works = _chain_works(inputs, _scout_chain(inputs), True)
    else:
        works = _chain_works(inputs, _pipeline_chain(inputs), False)
    contingency = is_fix(inputs.task_type) and inputs.repairable and not calibrated
    repair = _repair(inputs, works) if contingency else None
    main = _main_work(inputs, works) if _subagents(inputs) else None
    ordered = (main, *works, repair)
    return tuple(work for work in ordered if work is not None)


def _formula_requests(work: _Work) -> int:
    return (
        BASE_REQUESTS
        + REQUESTS_PER_READ * len(work.files)
        + REQUESTS_PER_EDIT * work.edits
        + VERIFY_REQUESTS * work.verify_rounds
        + work.dispatches
    )


def _requests(work: _Work, turns: int, provider: Provider) -> int:
    count = work.requests if provider is Provider.CLAUDE else _formula_requests(work)
    return min(count, turns) if turns > 0 else count


def _buckets(work: _Work, fixed: FixedPrefix, requests: int, provider: Provider) -> Buckets:
    return Buckets(
        start=fixed.tokens + work.pack,
        exploration=sum(work.files),
        writing=requests * output_per_request(provider) + work.written,
        verification=work.verify_rounds * VERIFY_TOKENS,
        handoff=work.handoff_in,
    )


def write_rate(price: Price, provider: Provider, ttl_s: int = 0) -> float:
    if provider is Provider.CODEX or price.cache_write is None:
        return price.input
    if ttl_s > FIVE_MINUTE_TTL_S:
        return price.input * ONE_HOUR_WRITE_FACTOR
    return price.cache_write


def _rates(price: Price, provider: Provider, ttl_s: int) -> tuple[float, float]:
    read = price.cache_read if price.cache_read is not None else price.input
    return read, write_rate(price, provider, ttl_s)


def role_usd(
    price: Price | None,
    provider: Provider,
    buckets: Buckets,
    requests: int,
    warmth: float,
    handoff_out: int = 0,
    ttl_s: int = 0,
) -> float | None:
    if price is None:
        return None
    read, write = _rates(price, provider, ttl_s)
    growth = buckets.exploration + buckets.verification
    context = buckets.start + buckets.handoff
    first = context * (warmth * read + (1.0 - warmth) * write)
    rereads = max(0, requests - 1) * (context + growth / 2) * read
    output = (buckets.writing + handoff_out) * price.output
    return (first + growth * write + rereads + output) / PER_MILLION


def _stops(
    work: _Work, buckets: Buckets, requests: int, depth: DepthProfile, turns: int
) -> StopRules:
    planned = math.ceil(requests * STOP_SLACK)
    reads = len(work.files) + READ_SLACK
    return StopRules(
        max_turns=min(planned, turns) if turns > 0 else planned,
        max_reads=min(reads, depth.read_budget) if depth.read_budget > 0 else reads,
        max_output_tokens=math.ceil((buckets.writing + work.handoff_out) * STOP_SLACK),
    )


def _role_forecast(
    inputs: EnvelopeInputs, work: _Work, spread: float
) -> tuple[RoleForecast, float | None]:
    fixed = fixed_prefix(
        inputs.provider, work.source.role, work.source.fixed_tokens, work.main, work.subagent
    )
    turns = turn_limit(inputs)
    requests = _requests(work, turns, inputs.provider)
    buckets = _buckets(work, fixed, requests, inputs.provider)
    if inputs.exploration != 1.0:
        buckets = replace(buckets, exploration=round(buckets.exploration * inputs.exploration))
    cost = role_usd(
        work.model.price,
        inputs.provider,
        buckets,
        requests,
        work.warmth,
        work.handoff_out,
        inputs.cache_ttl_s,
    )
    factor = role_factor(work.source, inputs.task_type)
    p50 = cost * factor if cost is not None else None
    if p50 is not None and inputs.risk != 1.0:
        p50 *= inputs.risk
    forecast = RoleForecast(
        role=work.source.role,
        model=work.model.model,
        buckets=buckets,
        requests=requests,
        p50_usd=p50,
        p90_usd=p50 if p50 is None or work.repair else p50 * spread,
        share=0.0,
        stops=_stops(work, buckets, requests, inputs.depth, turns),
        fixed=fixed,
        factor=factor,
        repair=work.repair,
        warmth=work.warmth,
    )
    return forecast, p50


def shown_warmth(inputs: EnvelopeInputs, roles: Sequence[RoleForecast]) -> float:
    weighted = [
        (item.warmth, item.buckets.start + item.buckets.handoff)
        for item in roles
        if not item.repair
    ]
    total = sum(weight for _, weight in weighted)
    if total <= 0:
        return _warmth(inputs.warmth)
    return _warmth(sum(value * weight for value, weight in weighted) / total)


def _forecast(inputs: EnvelopeInputs) -> Envelope:
    spread, samples = p90_spread(inputs.calibration)
    made = [_role_forecast(inputs, work, spread) for work in _works(inputs, samples > 0)]
    planned = sum_costs(cost for item, cost in made if not item.repair) if made else None
    contingency = sum_costs(cost for item, cost in made if item.repair)
    p50 = planned if contingency is not None else None
    roles = tuple(
        replace(item, share=cost / p50) if cost is not None and p50 and not item.repair else item
        for item, cost in made
    )
    p90 = p50 * spread + contingency if p50 is not None and contingency is not None else None
    buckets = Buckets()
    for item in roles:
        buckets = buckets.plus(item.buckets)
    return Envelope(
        task_type=inputs.task_type,
        depth=inputs.depth.depth.value,
        shape=inputs.shape,
        provider=inputs.provider,
        buckets=buckets,
        requests=sum(item.requests for item in roles),
        p50_usd=p50,
        p90_usd=p90,
        spread=spread,
        spread_samples=samples,
        cap_usd=inputs.cap_usd,
        margin_usd=inputs.cap_usd - p90 if p90 is not None and inputs.cap_usd > 0 else None,
        verdict=verdict(p50, p90, inputs.cap_usd),
        warmth=shown_warmth(inputs, roles),
        roles=roles,
    )


Remedy = tuple[EnvelopeInputs, str, dict[str, Message | str]]


def _shallower(inputs: EnvelopeInputs, _: Envelope) -> Remedy | None:
    target = SHALLOWER.get(inputs.depth.depth)
    if target is None:
        return None
    changed = replace(inputs, depth=profile(target, inputs.task_type))
    return changed, "envelope.suggest.depth", {"depth": keyed("depth", target.value)}


def _cheaper(inputs: EnvelopeInputs, base: Envelope) -> Remedy | None:
    costs: dict[Role, float] = {}
    for item in base.roles:
        if item.p50_usd is not None:
            costs[item.role] = costs.get(item.role, 0.0) + item.p50_usd
    options = [
        (costs.get(source.role, 0.0), index, source, source.cheaper)
        for index, source in enumerate(inputs.roles)
        if source.cheaper is not None
    ]
    if not options:
        return None
    _, index, source, cheaper = max(options, key=lambda option: (option[0], option[1]))
    swapped = replace(source, model=cheaper, cheaper=None)
    roles = (*inputs.roles[:index], swapped, *inputs.roles[index + 1 :])
    params: dict[str, Message | str] = {
        "role": keyed("role", source.role.value),
        "model": cheaper.model,
    }
    return replace(inputs, roles=roles), "envelope.suggest.tier", params


def _narrower(inputs: EnvelopeInputs, _: Envelope) -> Remedy | None:
    keep = max(1, int(inputs.depth.read_budget * LARGE_READ_SHARE))
    if len(inputs.read_tokens) <= keep:
        return None
    changed = replace(inputs, read_tokens=inputs.read_tokens[:keep])
    return changed, "envelope.suggest.where", {"files": str(keep)}


def _scout(inputs: EnvelopeInputs, base: Envelope) -> Remedy | None:
    if inputs.shape == SCOUT_SHAPE:
        return None
    if base.buckets.exploration <= EXPLORATION_DOMINANCE * base.buckets.task:
        return None
    return replace(inputs, shape=SCOUT_SHAPE), "envelope.suggest.scout", {}


REMEDIES: tuple[tuple[SuggestionKind, Callable[[EnvelopeInputs, Envelope], Remedy | None]], ...] = (
    (SuggestionKind.DEPTH, _shallower),
    (SuggestionKind.TIER, _cheaper),
    (SuggestionKind.WHERE, _narrower),
    (SuggestionKind.SCOUT, _scout),
)


def suggestions(inputs: EnvelopeInputs, base: Envelope) -> tuple[Suggestion, ...]:
    if base.verdict not in {Verdict.TIGHT, Verdict.INFEASIBLE} or base.p90_usd is None:
        return ()
    found: list[Suggestion] = []
    for kind, remedy in REMEDIES:
        applied = remedy(inputs, base)
        if applied is None:
            continue
        changed, key, params = applied
        after = _forecast(changed).p90_usd
        if after is None or after >= base.p90_usd:
            continue
        message = msg(key, **params, p90=dollars(after))
        found.append(Suggestion(kind, message, after, base.p90_usd - after))
    return tuple(sorted(found, key=lambda item: -item.saving_usd))


def envelope(inputs: EnvelopeInputs) -> Envelope:
    base = _forecast(inputs)
    return replace(base, suggestions=suggestions(inputs, base))


def envelope_features(inputs: EnvelopeInputs, result: Envelope) -> dict[str, float]:
    features: dict[str, float] = {
        "edit_files": len(inputs.edit_tokens),
        "edit_tokens": sum(max(0, tokens) for tokens in inputs.edit_tokens),
        "read_files": len(inputs.read_tokens),
        "read_tokens": sum(max(0, tokens) for tokens in inputs.read_tokens),
        "unknown_sizes": sum(
            1 for tokens in (*inputs.edit_tokens, *inputs.read_tokens) if tokens <= 0
        ),
        "warmth": result.warmth,
        "cap_usd": inputs.cap_usd,
        "read_budget": inputs.depth.read_budget,
        "max_turns": turn_limit(inputs),
        "pack_tokens": inputs.depth.pack_tokens,
        "roles": len(result.roles),
        "requests": result.requests,
        "fixed_tokens": sum(item.fixed.tokens for item in result.roles),
        "measured_fixed": sum(1 for item in result.roles if item.fixed.measured),
        "spread": result.spread,
        "spread_samples": result.spread_samples,
        "repair": sum(1 for item in result.roles if item.repair),
        "native": 1 if inputs.native else 0,
        "cache_ttl_s": inputs.cache_ttl_s,
    }
    if result.margin_usd is not None:
        features["margin_usd"] = result.margin_usd
    if inputs.risk != 1.0:
        features["risk_factor"] = inputs.risk
    if inputs.exploration != 1.0:
        features["exploration_factor"] = inputs.exploration
    return features


def _role_payload(item: RoleForecast) -> dict[str, object]:
    return {
        "role": item.role.value,
        "model": item.model,
        "repair": item.repair,
        "requests": item.requests,
        "p50_usd": item.p50_usd,
        "p90_usd": item.p90_usd,
        "share": item.share,
        "factor": item.factor,
        "warmth": item.warmth,
        "fixed_tokens": item.fixed.tokens,
        "fixed_measured": item.fixed.measured,
        "buckets": item.buckets.payload(),
        "max_turns": item.stops.max_turns,
        "max_reads": item.stops.max_reads,
        "max_output_tokens": item.stops.max_output_tokens,
    }


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def forecast_record(
    inputs: EnvelopeInputs,
    result: Envelope,
    run_id: str,
    created_at: str,
    source: str = ENVELOPE_SOURCE,
) -> Forecast | None:
    if result.p50_usd is None or result.p90_usd is None:
        return None
    return Forecast(
        run_id=run_id,
        created_at=created_at,
        provider=result.provider.value,
        task_type=result.task_type,
        depth=result.depth,
        shape=result.shape,
        p50_usd=result.p50_usd,
        p90_usd=result.p90_usd,
        cap_usd=result.cap_usd if result.cap_usd > 0 else None,
        verdict=result.verdict.value,
        buckets=_json(result.buckets.payload()),
        per_role=_json([_role_payload(item) for item in result.roles]),
        features=_json(envelope_features(inputs, result)),
        source=source,
    )


def signed_dollars(value: float) -> str:
    return f"-{dollars(-value)}" if value < 0 else dollars(value)


def warmth_message(prefix: PrefixState, warmth: float) -> Message:
    if prefix is PrefixState.WARM:
        return msg("envelope.cache_warm", share=f"{_warmth(warmth):.0%}")
    if prefix is PrefixState.COLD:
        return msg("envelope.cache_cold")
    return msg("envelope.cache_unknown")


def forecast_messages(result: Envelope, prefix: PrefixState) -> tuple[Message, ...]:
    cache = warmth_message(prefix, result.warmth)
    if result.p50_usd is None or result.p90_usd is None:
        return (msg("envelope.line_unknown", cache=cache),)
    p50, p90 = dollars(result.p50_usd), dollars(result.p90_usd)
    if result.margin_usd is None:
        return (msg("envelope.line_uncapped", p50=p50, p90=p90, cache=cache),)
    line = msg(
        "envelope.line", p50=p50, p90=p90, margin=signed_dollars(result.margin_usd), cache=cache
    )
    if result.verdict not in {Verdict.TIGHT, Verdict.INFEASIBLE}:
        return (line,)
    warning = msg(f"envelope.{result.verdict.value}", p50=p50, cap=dollars(result.cap_usd))
    if not result.suggestions:
        return (line, warning)
    return (line, warning, msg("envelope.try", suggestion=result.suggestions[0].message))


def envelope_payload(result: Envelope) -> dict[str, object]:
    return {
        "task_type": result.task_type,
        "depth": result.depth,
        "shape": result.shape,
        "provider": result.provider.value,
        "p50_usd": result.p50_usd,
        "p90_usd": result.p90_usd,
        "cap_usd": result.cap_usd if result.cap_usd > 0 else None,
        "margin_usd": result.margin_usd,
        "verdict": result.verdict.value,
        "spread": result.spread,
        "spread_samples": result.spread_samples,
        "warmth": result.warmth,
        "requests": result.requests,
        "buckets": result.buckets.payload(),
        "roles": [_role_payload(item) for item in result.roles],
        "suggestions": [
            {
                "kind": item.kind.value,
                "key": item.message.key,
                "p90_usd": item.p90_usd,
                "saving_usd": item.saving_usd,
            }
            for item in result.suggestions
        ],
    }
