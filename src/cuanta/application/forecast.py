from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace

from cuanta.application.instinct import DecisionMaker
from cuanta.application.routing import RoutePlan
from cuanta.domain.cache import (
    UNKNOWN_WARMTH,
    CacheClock,
    ModelStart,
    PrefixState,
    Warmth,
    model_starts,
    model_warmth,
)
from cuanta.domain.calibration import ForecastActual, spread_ratios
from cuanta.domain.change_plan import ChangePlan
from cuanta.domain.code_index import IndexedFile
from cuanta.domain.costs import median, sum_costs
from cuanta.domain.depth import parse_depth, profile
from cuanta.domain.envelope import (
    ENVELOPE_SOURCE,
    JEV_SOURCE,
    PIPELINE_SHAPE,
    SINGLE_SHAPE,
    Envelope,
    EnvelopeInputs,
    RoleInput,
    RoleModel,
    RoleSample,
    Verdict,
    envelope,
    envelope_features,
    envelope_payload,
    forecast_messages,
    forecast_record,
    repair_possible,
)
from cuanta.domain.errors import CuantaError, EnvironmentFailure
from cuanta.domain.instinct import Answer, Ask, Choice, Primitive, Score
from cuanta.domain.ledger import Forecast, Run
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import Message, english, keyed, msg
from cuanta.domain.models import TIER_ORDER, ModelEntry, Tier, parse_tier, tier_rank
from cuanta.domain.overhead import first_request_split, prompt_length
from cuanta.domain.pricing import PER_MILLION, Price, PriceTable, base_model
from cuanta.domain.progress import Status, note
from cuanta.domain.real_costs import attempts, known_cost
from cuanta.domain.routing import Provider, Role, RoleRoute, candidates, single_model
from cuanta.domain.spectrum import estimated_tokens
from cuanta.domain.time_forecast import TimeForecast, time_forecast
from cuanta.ports.instinct import Instinct
from cuanta.ports.ledger import EventQuery, Ledger
from cuanta.ports.progress import ProgressSink

CROSS_KIND = "cross"
MANDATE_KIND = "mandate"
PREFIX_SCAN = 30
JEV_CAP_USD = 0.001
JEV_PRIOR_HITS = 1
JEV_PRIOR_RUNS = 5
JEV_LOW = 0.5
JEV_HIGH = 2.0
JEV_OVERHEAD_TOKENS = 200
JEV_OUTPUT_TOKENS = 60
RISK_KEY = "risk"
EXPLORATION_KEY = "exploration"
TIER_PREFIX = "tier_"
BASE_P50 = "base_p50_usd"

ROLE_NAMES = frozenset(role.value for role in Role)

PrefixKey = tuple[str, Role, str]
Shapes = Callable[[Sequence[Run]], Mapping[str, str]]


@dataclass(frozen=True, slots=True)
class PlanSizes:
    edit: tuple[int, ...] = ()
    read: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class JevAdvice:
    risk: float
    exploration: float
    tiers: tuple[tuple[Role, Tier], ...]
    confidence: float
    accuracy: float
    cost_usd: float | None

    @property
    def weight(self) -> float:
        return self.confidence * self.accuracy

    def features(self, base_p50: float) -> dict[str, float]:
        found: dict[str, float] = {
            BASE_P50: base_p50,
            "jev_risk": self.risk,
            "jev_exploration": self.exploration,
            "jev_confidence": self.confidence,
            "jev_accuracy": self.accuracy,
            "jev_weight": self.weight,
        }
        if self.cost_usd is not None:
            found["jev_cost_usd"] = self.cost_usd
        found.update({f"jev_tier_{role.value}": tier_rank(tier) for role, tier in self.tiers})
        return found


@dataclass(frozen=True, slots=True)
class PlannedForecast:
    inputs: EnvelopeInputs
    envelope: Envelope
    prefix: PrefixState
    source: str = ENVELOPE_SOURCE
    advice: JevAdvice | None = None
    notes: tuple[Message, ...] = ()
    base_p50: float | None = None
    context: tuple[tuple[str, float], ...] = ()
    tiers: tuple[tuple[Role, Tier], ...] = ()
    time: TimeForecast = field(default_factory=TimeForecast)

    @property
    def messages(self) -> tuple[Message, ...]:
        return (*forecast_messages(self.envelope, self.prefix), self.time.message)

    @property
    def warning(self) -> bool:
        return self.envelope.verdict in {Verdict.TIGHT, Verdict.INFEASIBLE}


def plan_sizes(plan: ChangePlan, files: Iterable[IndexedFile]) -> PlanSizes:
    sizes = {file.path: estimated_tokens(file.size_bytes) for file in files}
    return PlanSizes(
        tuple(sizes.get(target.path, 0) for target in plan.edit),
        tuple(sizes.get(path, 0) for path in plan.read),
    )


def model_names(entry: ModelEntry) -> tuple[str, ...]:
    return tuple(dict.fromkeys(name for name in (entry.resolved, entry.id) if name))


def model_price(entry: ModelEntry, prices: PriceTable) -> Price | None:
    return prices.lookup(entry.resolved or entry.id) or prices.lookup(entry.id)


def run_role(run: Run) -> Role | None:
    if run.kind == MANDATE_KIND:
        return Role.ORCHESTRATOR
    if run.kind != CROSS_KIND:
        return None
    try:
        return Role(run.scope)
    except ValueError:
        return None


def slot_shape(shape: str) -> str:
    return SINGLE_SHAPE if shape == SINGLE_SHAPE else PIPELINE_SHAPE


def run_slot(run: Run, shapes: Mapping[str, str]) -> tuple[Role, str] | None:
    if run.kind == MANDATE_KIND:
        shape = shapes.get(run.id, "")
        return (Role.ORCHESTRATOR, shape) if shape in {SINGLE_SHAPE, PIPELINE_SHAPE} else None
    role = run_role(run)
    return (role, PIPELINE_SHAPE) if role is not None else None


@dataclass(frozen=True, slots=True)
class PrefixScan:
    fixed: dict[PrefixKey, int]
    starts: tuple[ModelStart, ...]


def scan_prefixes(
    ledger: Ledger,
    runs: Sequence[Run],
    engine: str,
    shapes: Mapping[str, str],
    limit: int = PREFIX_SCAN,
) -> PrefixScan:
    samples: dict[PrefixKey, list[float]] = {}
    starts: list[ModelStart] = []
    scanned = 0
    for run in runs:
        if run.engine != engine:
            continue
        scanned += 1
        if scanned > limit:
            break
        events = ledger.events(EventQuery(run_id=run.id))
        starts.extend(model_starts(events))
        slot = run_slot(run, shapes)
        if not run.model or slot is None:
            continue
        split = first_request_split(events) if prompt_length(events) > 0 else None
        if split is not None and split.fixed > 0:
            key = (base_model(run.model), *slot)
            samples.setdefault(key, []).append(float(split.fixed))
    fixed = {
        key: int(found) for key, values in samples.items() if (found := median(values)) is not None
    }
    return PrefixScan(fixed, tuple(starts))


def measured_prefixes(
    ledger: Ledger,
    runs: Sequence[Run],
    engine: str,
    shapes: Mapping[str, str],
    limit: int = PREFIX_SCAN,
) -> dict[PrefixKey, int]:
    return scan_prefixes(ledger, runs, engine, shapes, limit).fixed


def _number(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def unfactored_roles(forecast: Forecast) -> dict[Role, float]:
    try:
        entries = json.loads(forecast.per_role)
    except ValueError:
        return {}
    if not isinstance(entries, list):
        return {}
    planned: dict[Role, float] = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("role") not in ROLE_NAMES:
            return {}
        p50 = _number(entry.get("p50_usd"))
        factor = _number(entry.get("factor"))
        if p50 is None or factor is None or factor <= 0:
            return {}
        if entry.get("repair") is True:
            continue
        role = Role(entry["role"])
        planned[role] = planned.get(role, 0.0) + p50 / factor
    return planned


def _role_actuals(roles: Sequence[Run]) -> dict[Role, float | None]:
    grouped: dict[Role, list[float | None]] = {}
    for run in roles:
        role = run_role(run)
        if role is not None:
            grouped.setdefault(role, []).append(known_cost(run))
    return {role: sum_costs(costs) for role, costs in grouped.items()}


def role_samples(
    items: Sequence[ForecastActual], runs: Sequence[Run]
) -> dict[Role, tuple[RoleSample, ...]]:
    by_id = {run.id: run for run in runs}
    children: dict[str, list[Run]] = {}
    for run in runs:
        if run.kind == CROSS_KIND and run.parent_id:
            children.setdefault(run.parent_id, []).append(run)
    found: dict[Role, list[RoleSample]] = {}
    for item in items:
        root = by_id.get(item.forecast.run_id)
        if root is None or item.actual_usd is None:
            continue
        planned = unfactored_roles(item.forecast)
        if root.kind == CROSS_KIND:
            actuals = _role_actuals((root, *children.get(root.id, ())))
        elif item.forecast.shape == SINGLE_SHAPE and len(planned) == 1:
            actuals = dict.fromkeys(planned, item.actual_usd)
        else:
            continue
        for role, p50 in planned.items():
            actual = actuals.get(role)
            if actual is not None and math.isfinite(actual) and p50 > 0:
                found.setdefault(role, []).append(RoleSample(item.forecast.task_type, actual / p50))
    return {role: tuple(samples) for role, samples in found.items()}


def _priced(
    entries: Iterable[ModelEntry], engine: str, tier: Tier, prices: PriceTable, skip: str = ""
) -> RoleModel | None:
    for entry in candidates(entries, (engine,), tier):
        price = model_price(entry, prices)
        if price is not None and entry.id != skip:
            return RoleModel(entry.id, price)
    return None


def cheaper_model(
    route: RoleRoute, entries: Sequence[ModelEntry], prices: PriceTable
) -> RoleModel | None:
    if route.model is None or route.tier is None:
        return None
    lower = TIER_ORDER[: tier_rank(route.tier)]
    for tier in reversed(lower):
        found = _priced(entries, route.engine, tier, prices, route.model.id)
        if found is not None:
            return found
    return None


def economy_model(
    entries: Sequence[ModelEntry], engine: str, prices: PriceTable
) -> RoleModel | None:
    return _priced(entries, engine, Tier.ECONOMY, prices)


def single_route(plan: RoutePlan, provider: Provider) -> RoleRoute | None:
    if provider is Provider.CLAUDE:
        for role in (Role.ORCHESTRATOR, Role.ANALYST):
            route = plan.route(role)
            if route is not None and route.model is not None:
                return route
        return None
    return single_model(plan.routes)


def _measured(fixed: Mapping[PrefixKey, int], names: Iterable[str], role: Role, shape: str) -> int:
    keys = ((base_model(name), role, slot_shape(shape)) for name in names)
    return next((fixed[key] for key in keys if key in fixed), 0)


def _role_input(
    role: Role,
    route: RoleRoute,
    prices: PriceTable,
    entries: Sequence[ModelEntry],
    fixed: Mapping[PrefixKey, int],
    history: Mapping[Role, tuple[RoleSample, ...]],
    shape: str,
) -> RoleInput | None:
    entry = route.model
    if entry is None:
        return None
    measured = _measured(fixed, model_names(entry), role, shape)
    return RoleInput(
        role,
        RoleModel(entry.id, model_price(entry, prices)),
        measured,
        history.get(role, ()),
        cheaper_model(route, entries, prices),
    )


def envelope_roles(
    plan: RoutePlan,
    provider: Provider,
    shape: str,
    prices: PriceTable,
    entries: Sequence[ModelEntry],
    fixed: Mapping[PrefixKey, int],
    history: Mapping[Role, tuple[RoleSample, ...]],
) -> tuple[RoleInput, ...]:
    pairs: tuple[tuple[Role, RoleRoute], ...]
    if shape == SINGLE_SHAPE:
        chosen = single_route(plan, provider)
        pairs = ((Role.ORCHESTRATOR, chosen),) if chosen is not None else ()
    else:
        pairs = tuple((route.role, route) for route in plan.routes)
    found = (
        _role_input(role, route, prices, entries, fixed, history, shape) for role, route in pairs
    )
    return tuple(item for item in found if item is not None)


def launch_roles(
    roles: tuple[RoleInput, ...],
    model: str,
    prices: PriceTable,
    entries: Sequence[ModelEntry],
    fixed: Mapping[PrefixKey, int],
    shape: str,
) -> tuple[RoleInput, ...]:
    current = next((item for item in roles if item.role is Role.ORCHESTRATOR), None)
    if not model or (current is not None and base_model(current.model.model) == base_model(model)):
        return roles
    entry = next((item for item in entries if model in model_names(item)), None)
    chosen = (
        RoleModel(entry.id, model_price(entry, prices))
        if entry is not None
        else RoleModel(model, prices.lookup(model))
    )
    names = model_names(entry) if entry is not None else (model,)
    swapped = RoleInput(
        Role.ORCHESTRATOR,
        chosen,
        _measured(fixed, names, Role.ORCHESTRATOR, shape),
        current.history if current is not None else (),
    )
    return (swapped, *(item for item in roles if item.role is not Role.ORCHESTRATOR))


def prefix_state(result: Envelope, warm: Mapping[str, Warmth]) -> PrefixState:
    states = {
        warm.get(base_model(item.model), UNKNOWN_WARMTH).state
        for item in result.roles
        if not item.repair
    }
    if PrefixState.WARM in states:
        return PrefixState.WARM
    return PrefixState.COLD if PrefixState.COLD in states else PrefixState.UNKNOWN


class Forecaster:
    def __init__(
        self,
        ledger: Ledger,
        prices: PriceTable,
        catalog: Callable[[], Sequence[ModelEntry]],
        sizes: Callable[[ChangePlan], PlanSizes],
        cache: Callable[[str], CacheClock],
        clock_iso: Callable[[], str],
        advisor: JevEnvelope | None = None,
        shapes: Shapes | None = None,
        metadata: Callable[[str], Mapping[str, object]] | None = None,
    ) -> None:
        self._ledger = ledger
        self._prices = prices
        self._catalog = catalog
        self._sizes = sizes
        self._cache = cache
        self._clock_iso = clock_iso
        self._advisor = advisor
        self._shapes = shapes
        self._metadata = metadata

    def plan(
        self,
        task_type: str,
        plan: RoutePlan,
        provider: Provider,
        depth: str,
        shape: str,
        cap: float,
        change_plan: ChangePlan | None = None,
        native: bool = False,
        model: str = "",
        max_turns: int = 0,
        implementation_profile: str = "balanced",
        variant: str = "",
    ) -> PlannedForecast:
        engine = provider.value
        runs = self._ledger.runs()
        past = self._ledger.forecasts(engine)
        entries = tuple(self._catalog())
        sizes = self._sizes(change_plan) if change_plan is not None else PlanSizes()
        clock = self._cache(engine)
        shapes = self._shapes(runs) if self._shapes is not None else {}
        scan = scan_prefixes(self._ledger, runs, engine, shapes)
        warm = model_warmth(scan.starts, clock.ttl_s, clock.now)
        roles = envelope_roles(
            plan,
            provider,
            shape,
            self._prices,
            entries,
            scan.fixed,
            role_samples(past, runs),
        )
        inputs = EnvelopeInputs(
            task_type=task_type,
            depth=profile(parse_depth(depth), task_type),
            shape=shape,
            provider=provider,
            roles=launch_roles(roles, model, self._prices, entries, scan.fixed, shape),
            cap_usd=cap,
            edit_tokens=sizes.edit,
            read_tokens=sizes.read,
            calibration=spread_ratios(past, engine, task_type),
            economy=economy_model(entries, engine, self._prices),
            native=native,
            cache_ttl_s=clock.ttl_s,
            max_turns=max_turns,
            model_warmth={name: found.share for name, found in warm.items()},
            repairable=repair_possible(
                shape, native, change_plan.verify if change_plan is not None else ()
            ),
        )
        result = envelope(inputs)
        context = ((RISK_KEY, plan.risk),) if plan.risk is not None else ()
        tiers = tuple((route.role, route.tier) for route in plan.routes if route.tier is not None)
        items = attempts(runs, self._clock_iso())
        metadata = (
            {item.run.id: self._metadata(item.run.id) for item in items}
            if self._metadata is not None
            else {}
        )
        models = ", ".join(sorted({base_model(role.model) for role in result.roles}))
        timing = time_forecast(
            task_type,
            models,
            variant,
            implementation_profile,
            items,
            self._ledger.events(EventQuery()) if metadata else (),
            metadata,
            {name: entry.resolved or entry.id for entry in entries for name in model_names(entry)},
        )
        return PlannedForecast(
            inputs, result, prefix_state(result, warm), context=context, tiers=tiers, time=timing
        )

    def record(
        self, run_id: str, planned: PlannedForecast, request: MandateRequest
    ) -> PlannedForecast:
        final = (
            self._advisor.adjust(run_id, planned, request) if self._advisor is not None else planned
        )
        record = forecast_record(
            final.inputs, final.envelope, run_id, self._clock_iso(), final.source
        )
        if record is None:
            return final
        features = {**json.loads(record.features), "time": asdict(final.time)}
        record = replace(
            record, features=json.dumps(features, sort_keys=True, separators=(",", ":"))
        )
        if final.advice is not None and final.base_p50 is not None:
            features = {**json.loads(record.features), **final.advice.features(final.base_p50)}
            text = json.dumps(features, sort_keys=True, separators=(",", ":"))
            record = replace(record, features=text)
        self._ledger.add_forecast(record)
        return final


def _blend(value: float, weight: float) -> float:
    return 1.0 + weight * (value - 1.0)


def track_record(items: Sequence[ForecastActual]) -> float:
    hits = 0
    runs = 0
    for item in items:
        if item.forecast.source != JEV_SOURCE or item.actual_usd is None:
            continue
        base = json.loads(item.forecast.features).get(BASE_P50)
        if not isinstance(base, int | float) or isinstance(base, bool):
            continue
        runs += 1
        adjusted = abs(item.forecast.p50_usd - item.actual_usd)
        hits += 1 if adjusted < abs(float(base) - item.actual_usd) else 0
    return (hits + JEV_PRIOR_HITS) / (runs + JEV_PRIOR_RUNS)


def envelope_asks(roles: Iterable[Role]) -> tuple[Ask, ...]:
    tiers = tuple(tier.value for tier in TIER_ORDER)
    return (
        Ask(
            RISK_KEY,
            Primitive.SCORE,
            english(msg("question.envelope_risk")),
            low=JEV_LOW,
            high=JEV_HIGH,
        ),
        Ask(
            EXPLORATION_KEY,
            Primitive.SCORE,
            english(msg("question.envelope_exploration")),
            low=JEV_LOW,
            high=JEV_HIGH,
        ),
        *(
            Ask(
                f"{TIER_PREFIX}{role.value}",
                Primitive.CHOOSE,
                english(msg("question.envelope_tier", role=role.value)),
                tiers,
            )
            for role in dict.fromkeys(roles)
        ),
    )


def tier_notes(advice: JevAdvice, routed: Iterable[tuple[Role, Tier]]) -> tuple[Message, ...]:
    current = dict(routed)
    return tuple(
        msg("envelope.jev_tier", tier=keyed("tier", tier.value), role=keyed("role", role.value))
        for role, tier in advice.tiers
        if current.get(role) is not tier
    )


def jev_price(asks: Sequence[Ask], context: Mapping[str, float], price: Price) -> float:
    body = json.dumps(
        {
            "state": dict(context),
            "questions": {ask.key: [ask.question, *ask.options] for ask in asks},
        }
    )
    tokens_in = estimated_tokens(len(body.encode("utf-8"))) + JEV_OVERHEAD_TOKENS
    tokens_out = JEV_OUTPUT_TOKENS * len(asks)
    return (tokens_in * price.input + tokens_out * price.output) / PER_MILLION


def _score(answer: Answer | None) -> Score:
    if not isinstance(answer, Score):
        raise EnvironmentFailure("jev answered a score question without a score")
    return answer


def parse_advice(
    answers: Mapping[str, Answer], asks: Sequence[Ask], accuracy: float, cost: float | None
) -> JevAdvice:
    risk = _score(answers.get(RISK_KEY))
    exploration = _score(answers.get(EXPLORATION_KEY))
    tiers: list[tuple[Role, Tier]] = []
    confidences = [risk.confidence, exploration.confidence]
    for ask in asks:
        if not ask.key.startswith(TIER_PREFIX):
            continue
        answer = answers.get(ask.key)
        tier = parse_tier(answer.option) if isinstance(answer, Choice) else None
        if not isinstance(answer, Choice) or tier is None:
            raise EnvironmentFailure(f"jev answered {ask.key} without a tier")
        tiers.append((Role(ask.key.removeprefix(TIER_PREFIX)), tier))
        confidences.append(answer.probability)
    confidence = min(1.0, max(0.0, sum(confidences) / len(confidences)))
    return JevAdvice(
        min(JEV_HIGH, max(JEV_LOW, risk.value)),
        min(JEV_HIGH, max(JEV_LOW, exploration.value)),
        tuple(tiers),
        confidence,
        accuracy,
        cost,
    )


class JevEnvelope:
    def __init__(
        self,
        backend: Instinct,
        ledger: Ledger,
        clock_iso: Callable[[], str],
        price: Price | None,
        consented: bool,
        fan_in: Callable[[MandateRequest], int] | None = None,
        cap_usd: float = JEV_CAP_USD,
    ) -> None:
        self._backend = backend
        self._ledger = ledger
        self._clock_iso = clock_iso
        self._price = price
        self._consented = consented
        self._fan_in = fan_in
        self._cap = cap_usd

    def features(self, planned: PlannedForecast, request: MandateRequest) -> dict[str, float]:
        found = envelope_features(planned.inputs, planned.envelope)
        found.update(dict(planned.context))
        if planned.envelope.p50_usd is not None:
            found["p50_usd"] = planned.envelope.p50_usd
        if planned.envelope.p90_usd is not None:
            found["p90_usd"] = planned.envelope.p90_usd
        found.update({f"factor_{item.role.value}": item.factor for item in planned.envelope.roles})
        if self._fan_in is not None:
            found["fan_in"] = self._fan_in(request)
        return found

    def adjust(
        self, run_id: str, planned: PlannedForecast, request: MandateRequest
    ) -> PlannedForecast:
        if planned.envelope.p50_usd is None:
            return planned
        if not self._consented:
            return replace(planned, notes=(msg("envelope.jev_consent"),))
        usable, reason = self._backend.available()
        if not usable:
            return replace(planned, notes=(msg("envelope.jev_unavailable", reason=reason),))
        if self._price is None:
            return replace(planned, notes=(msg("envelope.jev_unpriced"),))
        context = self.features(planned, request)
        asks = envelope_asks(item.role for item in planned.envelope.roles)
        cost = jev_price(asks, context, self._price)
        if cost > self._cap:
            refused = msg("envelope.jev_refused", cost=f"${cost:.4f}", cap=f"${self._cap:.4f}")
            return replace(planned, notes=(refused,))
        maker = DecisionMaker(self._backend, self._ledger, self._clock_iso)
        accuracy = track_record(self._ledger.forecasts(planned.envelope.provider.value))
        try:
            answers, receipt = maker.ask_many(asks, context, run_id)
            advice = parse_advice(answers, asks, accuracy, receipt.cost_usd)
        except CuantaError as error:
            return replace(planned, notes=(msg("envelope.jev_failed", error=error.message),))
        inputs = replace(
            planned.inputs,
            risk=_blend(advice.risk, advice.weight),
            exploration=_blend(advice.exploration, advice.weight),
        )
        note = msg(
            "envelope.jev",
            risk=f"{advice.risk:.2f}",
            exploration=f"{advice.exploration:.2f}",
            weight=f"{advice.weight:.2f}",
        )
        return replace(
            planned,
            inputs=inputs,
            envelope=envelope(inputs),
            source=JEV_SOURCE,
            advice=advice,
            notes=(note, *tier_notes(advice, planned.tiers)),
            base_p50=planned.envelope.p50_usd,
        )


def envelope_json(planned: PlannedForecast) -> dict[str, object]:
    payload = envelope_payload(planned.envelope)
    payload["suggestions"] = [
        {
            "kind": item.kind.value,
            "key": item.message.key,
            "text": english(item.message),
            "p90_usd": item.p90_usd,
            "saving_usd": item.saving_usd,
        }
        for item in planned.envelope.suggestions
    ]
    payload["cache"] = planned.prefix.value
    payload["source"] = planned.source
    payload["time"] = asdict(planned.time)
    payload["lines"] = [english(message) for message in (*planned.notes, *planned.messages)]
    return payload


def forecast_failure(error: CuantaError | ValueError) -> Message:
    text = error.message if isinstance(error, CuantaError) else str(error)
    return msg("envelope.failed", error=text)


def publish_forecast(progress: ProgressSink, planned: PlannedForecast) -> None:
    for message in planned.notes:
        progress.publish(note(Status.INFO, message))
    status = Status.WARN if planned.warning else Status.INFO
    for message in planned.messages:
        progress.publish(note(status, message))
