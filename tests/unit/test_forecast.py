from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.application.forecast import (
    JEV_CAP_USD,
    Forecaster,
    JevEnvelope,
    PlannedForecast,
    PlanSizes,
    cheaper_model,
    envelope_asks,
    envelope_json,
    envelope_roles,
    jev_price,
    measured_prefixes,
    plan_sizes,
    publish_forecast,
    role_samples,
    single_route,
    track_record,
    unfactored_roles,
)
from cuanta.application.routing import RoutePlan
from cuanta.domain.cache import CacheClock, PrefixState
from cuanta.domain.change_plan import ChangePlan, EditTarget
from cuanta.domain.code_index import IndexedFile
from cuanta.domain.envelope import ENVELOPE_SOURCE, JEV_SOURCE, RoleSample, Verdict, role_factor
from cuanta.domain.errors import EnvironmentFailure
from cuanta.domain.instinct import Answer, Ask, Choice, Context, Noul, Primitive, Receipt, Score
from cuanta.domain.ledger import Forecast, LedgerEvent, Run
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import Message, english, msg
from cuanta.domain.models import ModelEntry, Tier
from cuanta.domain.pricing import Price, PriceTable
from cuanta.domain.progress import Note, ProgressEvent, Status
from cuanta.domain.routing import ROLES, Provider, Role, RoleRoute, RoutingPolicy
from cuanta.tui.i18n import Catalog

REQUEST = MandateRequest("bug", "fix add", "add(2, 3) == -1", out_of_scope="tests")
SONNET = Price(3.0, 15.0, 3.75, 0.3)
OPUS = Price(5.0, 25.0, 6.25, 0.5)
HAIKU = Price(1.0, 5.0, 1.25, 0.1)
JEV = Price(0.042, 0.0, 0.0, 0.0)
NOW = datetime(2026, 9, 28, 10, 0, tzinfo=UTC)
COLD_CLOCK = CacheClock(0, NOW)
PLAN = ChangePlan(edit=(EditTarget("src/calc.py", 0.9),), read=("src/missing.py",))
PRICES = PriceTable(
    {
        "claude-sonnet-5": SONNET,
        "claude-opus-5-5": OPUS,
        "claude-haiku-4-5": HAIKU,
        "gpt-6-sol": SONNET,
        "gpt-6-luna": HAIKU,
    }
)
MODELS = {
    Role.ORCHESTRATOR: ("claude-sonnet-5", Tier.STANDARD),
    Role.ANALYST: ("claude-sonnet-5", Tier.STANDARD),
    Role.SENIOR: ("claude-opus-5-5", Tier.PREMIUM),
    Role.TESTER: ("claude-sonnet-5", Tier.STANDARD),
    Role.DOCS: ("claude-haiku-4-5", Tier.ECONOMY),
}
CATALOG = (
    ModelEntry("claude", "claude-haiku-4-5", "Haiku", "anthropic", tier=Tier.ECONOMY),
    ModelEntry("claude", "claude-sonnet-5", "Sonnet", "anthropic", tier=Tier.STANDARD),
    ModelEntry("claude", "claude-opus-5-5", "Opus", "anthropic", tier=Tier.PREMIUM),
)


def team(engine: str = "claude", risk: float | None = None) -> RoutePlan:
    routes = tuple(
        RoleRoute(
            role,
            MODELS[role][1],
            MODELS[role][1],
            ModelEntry(engine, MODELS[role][0], "m", "p", tier=MODELS[role][1]),
            msg("route.policy", tier=MODELS[role][1].value),
        )
        for role in ROLES
    )
    return RoutePlan(RoutingPolicy(engines=(engine,)), None, risk, (), routes, "heuristic")


def sizes(plan: ChangePlan) -> PlanSizes:
    return plan_sizes(plan, (IndexedFile("src/calc.py", "h", "python", 8_000),))


def forecaster(
    ledger: MemoryLedger,
    clock: CacheClock = COLD_CLOCK,
    advisor: JevEnvelope | None = None,
    shapes: Mapping[str, str] | None = None,
) -> Forecaster:
    return Forecaster(
        ledger,
        PRICES,
        lambda: CATALOG,
        sizes,
        lambda engine: clock,
        lambda: "2026-09-28T10:00:00Z",
        advisor,
        (lambda runs: shapes) if shapes is not None else None,
    )


def planned(ledger: MemoryLedger, cap: float = 5.0, **kwargs: object) -> PlannedForecast:
    risk = kwargs.get("risk")
    return forecaster(ledger).plan(
        "bug",
        team(risk=risk if isinstance(risk, float) else None),
        Provider.CLAUDE,
        "normal",
        "pipeline",
        cap,
        PLAN,
    )


class Recorder:
    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    def publish(self, event: ProgressEvent) -> None:
        self.events.append(event)


class FakeJev:
    def __init__(
        self,
        risk: float = 1.5,
        exploration: float = 1.2,
        confidence: float = 0.8,
        tier: str = "standard",
        fail: bool = False,
        usable: bool = True,
    ) -> None:
        self.risk = risk
        self.exploration = exploration
        self.confidence = confidence
        self.tier = tier
        self.fail = fail
        self.usable = usable
        self.contexts: list[dict[str, object]] = []
        self.asks: list[tuple[Ask, ...]] = []

    @property
    def name(self) -> str:
        return "jev"

    @property
    def remote(self) -> bool:
        return True

    def available(self) -> tuple[bool, Message]:
        return self.usable, msg("instinct.key_missing", env="TYPESAFE_API_KEY")

    def choose(
        self, question: str, options: Sequence[str], context: Context
    ) -> tuple[Choice, Receipt]:
        raise AssertionError("the envelope asks in one batch")

    def score(
        self, question: str, low: float, high: float, context: Context
    ) -> tuple[Score, Receipt]:
        raise AssertionError("the envelope asks in one batch")

    def noul(self, question: str, context: Context) -> tuple[Noul, Receipt]:
        raise AssertionError("the envelope asks in one batch")

    def ask_many(self, asks: Sequence[Ask], context: Context) -> tuple[dict[str, Answer], Receipt]:
        self.contexts.append(dict(context))
        self.asks.append(tuple(asks))
        if self.fail:
            raise EnvironmentFailure("jev unreachable: boom")
        answers: dict[str, Answer] = {}
        for ask in asks:
            if ask.primitive is Primitive.SCORE:
                value = self.risk if ask.key == "risk" else self.exploration
                answers[ask.key] = Score(value, self.confidence)
            else:
                answers[ask.key] = Choice(self.tier, self.confidence)
        return answers, Receipt("jev", 12, 0.0004)


def jev(
    ledger: MemoryLedger,
    backend: FakeJev,
    consented: bool = True,
    price: Price | None = JEV,
    cap: float = JEV_CAP_USD,
) -> JevEnvelope:
    return JevEnvelope(
        backend, ledger, lambda: "2026-09-28T10:00:00Z", price, consented, lambda _: 7, cap
    )


def test_plan_sizes_read_the_file_sizes_and_leave_unknown_files_at_zero() -> None:
    plan = ChangePlan(edit=(EditTarget("src/a.py", 0.9),), read=("src/b.py", "src/gone.py"))
    files = (
        IndexedFile("src/a.py", "h", "python", 4_000),
        IndexedFile("src/b.py", "h", "python", 402),
    )
    assert plan_sizes(plan, files) == PlanSizes((1_000,), (100, 0))


def prompt_event(run_id: str, length: int) -> LedgerEvent:
    raw = json.dumps({"attributes": {"prompt_length": length}})
    return LedgerEvent(run_id=run_id, kind="user_prompt", ts="2026-09-28T10:00:00Z", raw=raw)


def request_event(
    run_id: str,
    written: int,
    read: int = 0,
    model: str = "",
    ts: str = "2026-09-28T10:00:01Z",
) -> LedgerEvent:
    return LedgerEvent(
        run_id=run_id,
        kind="api_request",
        model=model,
        ts=ts,
        input_tokens=1_000,
        cache_read_tokens=read,
        cache_write_tokens=written,
    )


def test_measured_prefixes_take_the_median_fixed_tokens_per_model_role_and_shape() -> None:
    ledger = MemoryLedger()
    runs = (
        Run("R1", "cross", "claude", "claude-sonnet-5", scope="analyst"),
        Run("R2", "cross", "claude", "claude-sonnet-5", scope="analyst"),
        Run("R3", "cross", "claude", "claude-sonnet-5", scope="analyst"),
        Run("R4", "mandate", "claude", "claude-opus-5-5"),
        Run("R5", "cross", "claude", "claude-sonnet-5", scope="tester"),
        Run("R6", "cross", "codex", "gpt-6-sol", scope="analyst"),
        Run("R7", "mandate", "claude", "claude-opus-5-5-20260101"),
        Run("R8", "mandate", "claude", "claude-opus-5-5"),
    )
    for run in runs:
        ledger.add_run(run)
    ledger.add_events(
        [
            prompt_event("R1", 4_000),
            request_event("R1", 20_000),
            prompt_event("R2", 4_000),
            request_event("R2", 30_000),
            prompt_event("R3", 4_000),
            request_event("R3", 10_000),
            prompt_event("R4", 400),
            request_event("R4", 20_000),
            request_event("R5", 50_000),
            prompt_event("R6", 400),
            request_event("R6", 5_000),
            prompt_event("R7", 400),
            request_event("R7", 36_000),
            prompt_event("R8", 400),
            request_event("R8", 60_000),
        ]
    )
    shapes = {"R4": "single", "R7": "pipeline"}
    found = measured_prefixes(ledger, runs, "claude", shapes)
    assert found == {
        ("claude-sonnet-5", Role.ANALYST, "pipeline"): 20_000,
        ("claude-opus-5-5", Role.ORCHESTRATOR, "single"): 20_900,
        ("claude-opus-5-5", Role.ORCHESTRATOR, "pipeline"): 36_900,
    }
    assert measured_prefixes(ledger, runs, "claude", shapes, limit=1) == {
        ("claude-sonnet-5", Role.ANALYST, "pipeline"): 20_000
    }
    assert measured_prefixes(ledger, runs, "claude", {}) == {
        ("claude-sonnet-5", Role.ANALYST, "pipeline"): 20_000
    }


def stored(run_id: str, shape: str, roles: list[dict[str, object]]) -> Forecast:
    return Forecast(
        run_id=run_id,
        created_at="2026-09-28T09:00:00Z",
        provider="claude",
        task_type="bug",
        depth="normal",
        shape=shape,
        p50_usd=1.0,
        p90_usd=1.6,
        cap_usd=2.0,
        verdict="comfortable",
        per_role=json.dumps(roles),
    )


def test_role_samples_divide_each_role_actual_by_its_plan_before_the_history_factor() -> None:
    ledger = MemoryLedger()
    for run in (
        Run("R1", "cross", "claude", status="ok", scope="analyst", cost_usd=0.15, task_type="bug"),
        Run("R2", "cross", "claude", status="ok", scope="senior", cost_usd=0.5, parent_id="R1"),
        Run("R3", "cross", "claude", status="ok", scope="senior", cost_usd=0.1, parent_id="R1"),
        Run("S1", "mandate", "claude", status="ok", cost_usd=0.3, task_type="fix"),
        Run("P1", "mandate", "claude", status="ok", cost_usd=0.9, task_type="bug"),
        Run("U1", "cross", "claude", status="ok", scope="analyst", cost_usd=None),
    ):
        ledger.add_run(run)
    for forecast in (
        stored(
            "R1",
            "pipeline",
            [
                {"role": "analyst", "p50_usd": 0.1, "factor": 1.0},
                {"role": "senior", "p50_usd": 0.4, "factor": 2.0},
                {"role": "senior", "p50_usd": 0.2, "factor": 2.0, "repair": True},
            ],
        ),
        stored("S1", "single", [{"role": "orchestrator", "p50_usd": 0.6, "factor": 1.0}]),
        stored("P1", "pipeline", [{"role": "senior", "p50_usd": 0.3, "factor": 1.0}]),
        stored("U1", "pipeline", [{"role": "analyst", "p50_usd": 0.1, "factor": 1.0}]),
    ):
        ledger.add_forecast(forecast)
    samples = role_samples(ledger.forecasts(), ledger.runs())
    ratios = {
        role: [(item.task_type, round(item.ratio, 6)) for item in found]
        for role, found in samples.items()
    }
    assert ratios == {
        Role.ANALYST: [("bug", 1.5)],
        Role.SENIOR: [("bug", 3.0)],
        Role.ORCHESTRATOR: [("bug", 0.5)],
    }


def test_a_repair_row_is_a_contingency_outside_the_writer_plan() -> None:
    plain = stored("R1", "pipeline", [{"role": "senior", "p50_usd": 0.4, "factor": 2.0}])
    repair = {"role": "senior", "p50_usd": 0.2, "factor": 2.0, "repair": True}
    contingent = stored(
        "R1", "pipeline", [{"role": "senior", "p50_usd": 0.4, "factor": 2.0}, repair]
    )
    assert unfactored_roles(plain) == unfactored_roles(contingent) == {Role.SENIOR: 0.2}
    only = stored("R2", "pipeline", [repair])
    assert unfactored_roles(only) == {}


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"role": "senior"}',
        '[{"role": "senior", "p50_usd": 0.2}]',
        '[{"role": "senior", "p50_usd": 0.2, "factor": 0}]',
        '[{"role": "boss", "p50_usd": 0.2, "factor": 1.0}]',
        '[{"role": "orchestrator", "p50_usd": 0.2, "factor": 1.0}, "x"]',
    ],
)
def test_malformed_per_role_data_is_left_out_of_the_role_history(text: str) -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("R1", "mandate", "claude", status="ok", cost_usd=0.2))
    ledger.add_run(Run("R2", "mandate", "claude", status="ok", cost_usd=0.4))
    ledger.add_forecast(replace_roles(stored("R1", "single", []), text))
    good = [{"role": "orchestrator", "p50_usd": 0.2, "factor": 1.0}]
    ledger.add_forecast(stored("R2", "single", good))
    samples = role_samples(ledger.forecasts(), ledger.runs())
    assert samples == {Role.ORCHESTRATOR: (RoleSample("bug", 2.0),)}


def test_a_constant_bias_teaches_a_role_factor_equal_to_the_bias() -> None:
    ledger = MemoryLedger()
    factors: list[float] = []
    for index in range(7):
        result = forecaster(ledger).plan(
            "bug", team(), Provider.CLAUDE, "normal", "single", 50.0, PLAN
        )
        [source] = result.inputs.roles
        factors.append(role_factor(source, "bug"))
        base = sum(
            (item.p50_usd or 0.0) / (item.factor * result.inputs.risk)
            for item in result.envelope.roles
            if not item.repair
        )
        run_id = f"B{index}"
        forecaster(ledger).record(run_id, result, REQUEST)
        ledger.add_run(Run(run_id, "mandate", "claude", status="ok", cost_usd=2.0 * base))
    assert factors == pytest.approx([1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 2.0])


def replace_roles(forecast: Forecast, text: str) -> Forecast:
    from dataclasses import replace

    return replace(forecast, per_role=text)


def test_single_route_and_cheaper_models_follow_the_provider_and_tier() -> None:
    plan = team()
    claude = single_route(plan, Provider.CLAUDE)
    assert claude is not None and claude.role is Role.ORCHESTRATOR
    codex = single_route(team("codex"), Provider.CODEX)
    assert codex is not None and codex.role is Role.SENIOR
    senior = plan.route(Role.SENIOR)
    assert senior is not None
    cheaper = cheaper_model(senior, CATALOG, PRICES)
    assert cheaper is not None and (cheaper.model, cheaper.price) == ("claude-sonnet-5", SONNET)
    docs = plan.route(Role.DOCS)
    assert docs is not None and cheaper_model(docs, CATALOG, PRICES) is None


def test_envelope_roles_carry_prices_measured_prefixes_and_history() -> None:
    history = {Role.SENIOR: (RoleSample("bug", 2.0),)}
    fixed = {
        ("claude-opus-5-5", Role.SENIOR, "pipeline"): 31_000,
        ("claude-sonnet-5", Role.ORCHESTRATOR, "single"): 18_000,
        ("claude-sonnet-5", Role.ORCHESTRATOR, "pipeline"): 36_000,
    }
    roles = envelope_roles(team(), Provider.CLAUDE, "pipeline", PRICES, CATALOG, fixed, history)
    assert [item.role for item in roles] == list(ROLES)
    senior = roles[2]
    assert (senior.model.model, senior.model.price, senior.fixed_tokens) == (
        "claude-opus-5-5",
        OPUS,
        31_000,
    )
    assert senior.history == history[Role.SENIOR]
    assert roles[1].fixed_tokens == 0
    assert roles[0].fixed_tokens == 36_000
    single = envelope_roles(team(), Provider.CLAUDE, "single", PRICES, CATALOG, fixed, {})
    assert [(item.role, item.model.model, item.fixed_tokens) for item in single] == [
        (Role.ORCHESTRATOR, "claude-sonnet-5", 18_000)
    ]


def test_a_pinned_launch_model_is_the_one_the_forecast_prices() -> None:
    ledger = MemoryLedger()

    def single(model: str = "") -> PlannedForecast:
        return forecaster(ledger).plan(
            "bug", team(), Provider.CLAUDE, "normal", "single", 50.0, PLAN, model=model
        )

    routed = single()
    pinned = single("claude-opus-5-5")
    assert [item.model for item in routed.envelope.roles] == ["claude-sonnet-5"]
    assert [item.model for item in pinned.envelope.roles] == ["claude-opus-5-5"]
    assert pinned.inputs.roles[0].model.price == OPUS
    assert pinned.envelope.p50_usd is not None and routed.envelope.p50_usd is not None
    assert pinned.envelope.p50_usd > routed.envelope.p50_usd
    assert single("claude-sonnet-5").inputs.roles == routed.inputs.roles
    unknown = single("sonnet-latest")
    assert unknown.envelope.verdict is Verdict.UNKNOWN


def test_only_separate_launches_with_checks_plan_a_repair_contingency() -> None:
    ledger = MemoryLedger()
    checked = ChangePlan(edit=PLAN.edit, read=PLAN.read, verify=("pytest -q",))

    def repairs(plan: ChangePlan | None, shape: str = "pipeline", native: bool = False) -> int:
        found = forecaster(ledger).plan(
            "bug", team(), Provider.CLAUDE, "normal", shape, 5.0, plan, native
        )
        assert found.inputs.repairable is (sum(item.repair for item in found.envelope.roles) > 0)
        return sum(item.repair for item in found.envelope.roles)

    assert repairs(checked) == 1
    assert repairs(PLAN) == 0
    assert repairs(None) == 0
    assert repairs(checked, native=True) == 0
    assert repairs(checked, shape="single") == 0


def test_native_pipelines_and_turn_rails_reach_the_envelope() -> None:
    ledger = MemoryLedger()
    native = forecaster(ledger).plan(
        "feature", team(), Provider.CLAUDE, "normal", "pipeline", 50.0, PLAN, True, "", 12
    )
    assert native.inputs.native and native.inputs.max_turns == 12
    assert native.envelope.roles[0].role is Role.ORCHESTRATOR
    assert all(item.stops.max_turns <= 12 for item in native.envelope.roles)
    separate = forecaster(ledger).plan(
        "feature", team(), Provider.CLAUDE, "normal", "pipeline", 50.0, PLAN
    )
    assert Role.ORCHESTRATOR not in {item.role for item in separate.envelope.roles}


def warm_runs(ledger: MemoryLedger, models: Sequence[str]) -> None:
    for index, model in enumerate(models):
        first, second = f"W{index}a", f"W{index}b"
        ledger.add_run(Run(first, "cross", "claude", model, scope="analyst"))
        ledger.add_run(Run(second, "cross", "claude", model, scope="analyst"))
        ledger.add_events(
            [
                request_event(first, 7_800, model=model, ts="2026-09-28T09:00:00Z"),
                request_event(second, 1_200, 7_800, model, "2026-09-28T09:30:00Z"),
            ]
        )


def test_the_forecaster_builds_inputs_from_the_plan_sizes_warmth_and_calibration() -> None:
    ledger = MemoryLedger()
    for index in range(5):
        run_id = f"C{index}"
        ledger.add_run(Run(run_id, "mandate", "claude", status="ok", cost_usd=2.0 + index))
        ledger.add_forecast(stored(run_id, "pipeline", []))
    warm_runs(ledger, ("claude-sonnet-5", "claude-opus-5-5", "claude-haiku-4-5"))
    result = forecaster(ledger, CacheClock(3_480, NOW)).plan(
        "bug", team(), Provider.CLAUDE, "normal", "pipeline", 5.0, PLAN
    )
    assert result.inputs.edit_tokens == (2_000,)
    assert result.inputs.read_tokens == (0,)
    assert result.inputs.cache_ttl_s == 3_480
    assert result.inputs.model_warmth == {
        "claude-sonnet-5": pytest.approx(0.78),
        "claude-opus-5-5": pytest.approx(0.78),
        "claude-haiku-4-5": pytest.approx(0.78),
    }
    assert sorted(result.inputs.calibration) == [2.0, 3.0, 4.0, 5.0, 6.0]
    assert result.envelope.spread == 6.0
    assert result.inputs.economy is not None
    assert result.inputs.economy.model == "claude-haiku-4-5"
    assert result.prefix is PrefixState.WARM
    assert english(result.messages[0]).endswith("warm cache (78%)")
    empty = forecaster(MemoryLedger()).plan(
        "bug", team(), Provider.CLAUDE, "normal", "pipeline", 5.0
    )
    assert empty.inputs.edit_tokens == () and empty.inputs.read_tokens == ()
    assert english(empty.messages[0]).endswith("cache unknown")


def test_record_stores_the_forecast_and_the_actual_flows_from_the_recorded_cost() -> None:
    ledger = MemoryLedger()
    result = planned(ledger)
    stored_forecast = forecaster(ledger).record("R9", result, REQUEST)
    assert stored_forecast is result
    [item] = ledger.forecasts()
    assert item.forecast.run_id == "R9"
    assert item.forecast.source == ENVELOPE_SOURCE
    assert item.forecast.p50_usd == result.envelope.p50_usd
    assert item.actual_usd is None
    ledger.add_run(Run("R9", "mandate", "claude", status="ok", cost_usd=0.42))
    assert ledger.forecasts()[0].actual_usd == 0.42
    ledger.update_run(Run("R9", "mandate", "claude", status="ok", cost_usd=None))
    assert ledger.forecasts()[0].actual_usd is None


def test_an_unpriced_forecast_is_shown_but_never_stored() -> None:
    ledger = MemoryLedger()
    unpriced = Forecaster(
        ledger,
        PriceTable(),
        lambda: (),
        sizes,
        lambda engine: COLD_CLOCK,
        lambda: "2026-09-28T10:00:00Z",
    )
    result = unpriced.plan("bug", team(), Provider.CLAUDE, "normal", "pipeline", 1.0)
    assert result.envelope.verdict is Verdict.UNKNOWN
    assert english(result.messages[0]) == "Forecast n/a: a role has no price, cache unknown"
    unpriced.record("R1", result, REQUEST)
    assert ledger.forecasts() == ()


def test_publishing_warns_on_tight_verdicts_and_the_json_names_the_suggestions() -> None:
    ledger = MemoryLedger()
    comfortable = planned(ledger, cap=50.0)
    recorder = Recorder()
    publish_forecast(recorder, comfortable)
    notes = [event for event in recorder.events if isinstance(event, Note)]
    assert [note.status for note in notes] == [Status.INFO, Status.INFO]
    assert notes[0].text.startswith("Forecast $")
    assert notes[1].text.startswith("Time forecast: P50")
    tight = planned(ledger, cap=(comfortable.envelope.p50_usd or 0.0) * 1.2)
    assert tight.envelope.verdict is Verdict.TIGHT
    recorder = Recorder()
    publish_forecast(recorder, tight)
    notes = [event for event in recorder.events if isinstance(event, Note)]
    assert all(note.status is Status.WARN for note in notes)
    assert notes[1].text.startswith("Tight:")
    assert notes[2].text.startswith("Try: ")
    payload = envelope_json(tight)
    assert payload["verdict"] == "tight"
    suggestions = payload["suggestions"]
    assert isinstance(suggestions, list) and suggestions
    assert {"kind", "key", "text", "p90_usd", "saving_usd"} <= set(suggestions[0])
    assert payload["lines"] == [note.text for note in notes]


def test_jev_is_sent_numbers_only_and_its_answer_is_weighted_by_its_track_record() -> None:
    ledger = MemoryLedger()
    base = planned(ledger, risk=1.5)
    backend = FakeJev(risk=1.5, exploration=1.2, confidence=0.8)
    request = MandateRequest(
        "bug", "fix C:/secret/path.py", "token private-marker-123", where="src/app.py"
    )
    adjusted = forecaster(ledger, advisor=jev(ledger, backend)).record("R1", base, request)
    [context] = backend.contexts
    assert all(
        isinstance(value, int | float) and not isinstance(value, bool) for value in context.values()
    )
    assert "secret" not in json.dumps(context) and "src/" not in json.dumps(context)
    assert context["risk"] == 1.5 and context["fan_in"] == 7
    assert {"edit_files", "read_files", "warmth", "margin_usd", "p50_usd"} <= set(context)
    advice = adjusted.advice
    assert advice is not None
    assert advice.accuracy == pytest.approx(0.2)
    assert advice.weight == pytest.approx(0.16)
    assert adjusted.inputs.risk == pytest.approx(1.08)
    assert adjusted.inputs.exploration == pytest.approx(1.032)
    assert adjusted.source == JEV_SOURCE
    assert adjusted.envelope.p50_usd is not None and base.envelope.p50_usd is not None
    assert adjusted.envelope.p50_usd > base.envelope.p50_usd
    assert [english(note) for note in adjusted.notes] == [
        "Jev adjusted the forecast: risk ×1.50, exploration ×1.20, weight 0.16",
        "Jev suggests the standard tier for the Senior",
        "Jev suggests the standard tier for the Docs",
    ]
    assert Catalog("es").message(adjusted.notes[1]) == (
        "Jev sugiere el nivel estándar para el senior"
    )
    [item] = ledger.forecasts()
    features = json.loads(item.forecast.features)
    assert item.forecast.source == JEV_SOURCE
    assert features["base_p50_usd"] == base.envelope.p50_usd
    assert features["jev_weight"] == pytest.approx(0.16)
    assert features["jev_tier_senior"] == 1
    assert features["jev_cost_usd"] == 0.0004
    decisions = ledger.decisions()
    assert len(decisions) == len(backend.asks[0])
    assert {decision.run_id for decision in decisions} == {"R1"}
    assert sum(decision.cost_usd or 0.0 for decision in decisions) == pytest.approx(0.0004)


def test_jev_track_record_raises_the_weight_after_better_forecasts() -> None:
    ledger = MemoryLedger()
    for index, (p50, actual) in enumerate(((1.2, 1.25), (1.1, 1.2), (1.3, 1.0))):
        run_id = f"J{index}"
        features = json.dumps({"base_p50_usd": 1.0})
        ledger.add_forecast(
            Forecast(
                run_id,
                "t",
                "claude",
                "bug",
                "normal",
                "pipeline",
                p50,
                p50 * 1.6,
                2.0,
                "comfortable",
                features=features,
                source=JEV_SOURCE,
            )
        )
        ledger.add_run(Run(run_id, "mandate", "claude", status="ok", cost_usd=actual))
    assert track_record(ledger.forecasts()) == pytest.approx(3 / 8)
    assert track_record(()) == pytest.approx(0.2)


@pytest.mark.parametrize(
    ("backend", "consented", "price", "cap", "said"),
    [
        (FakeJev(), False, JEV, JEV_CAP_USD, "run cuanta instinct use jev"),
        (FakeJev(usable=False), True, JEV, JEV_CAP_USD, "TYPESAFE_API_KEY"),
        (FakeJev(), True, None, JEV_CAP_USD, "cannot be priced"),
        (FakeJev(), True, JEV, 0.000001, "over the $0.0000 cap"),
    ],
    ids=["no-consent", "no-key", "unpriced", "over-cap"],
)
def test_jev_is_not_asked_without_consent_a_key_a_price_or_room_under_its_cap(
    backend: FakeJev, consented: bool, price: Price | None, cap: float, said: str
) -> None:
    ledger = MemoryLedger()
    base = planned(ledger)
    advisor = jev(ledger, backend, consented, price, cap)
    result = forecaster(ledger, advisor=advisor).record("R1", base, REQUEST)
    assert backend.contexts == []
    assert said in english(result.notes[0])
    assert result.envelope == base.envelope
    [item] = ledger.forecasts()
    assert item.forecast.source == ENVELOPE_SOURCE
    assert ledger.decisions() == ()


def test_a_failed_jev_call_is_reported_and_the_plain_forecast_is_kept() -> None:
    ledger = MemoryLedger()
    base = planned(ledger)
    backend = FakeJev(fail=True)
    result = forecaster(ledger, advisor=jev(ledger, backend)).record("R1", base, REQUEST)
    assert len(backend.contexts) == 1
    assert english(result.notes[0]) == (
        "Jev did not answer, the plain forecast is kept: jev unreachable: boom"
    )
    assert result.source == ENVELOPE_SOURCE
    assert ledger.forecasts()[0].forecast.p50_usd == base.envelope.p50_usd


def test_jev_calls_are_priced_before_they_run() -> None:
    asks = envelope_asks((Role.ANALYST, Role.SENIOR, Role.SENIOR))
    assert [ask.key for ask in asks] == ["risk", "exploration", "tier_analyst", "tier_senior"]
    context: Mapping[str, float] = {"edit_files": 2.0, "p50_usd": 0.4}
    cost = jev_price(asks, context, JEV)
    assert 0 < cost < JEV_CAP_USD
    assert jev_price(asks, context, Price(1.0, 2.0, None, None)) > cost


def test_jev_envelope_decisions_read_as_localized_sentences() -> None:
    from cuanta.application.instinct_view import sentence
    from cuanta.domain.ledger import Decision

    asks = envelope_asks((Role.SENIOR,))
    rows = [
        Decision("R1", "jev", ask.primitive.value, ask.question, "", answer, 0.8, 10)
        for ask, answer in zip(asks, ("1.2", "0.9", "standard"), strict=True)
    ]
    spanish = Catalog("es")
    assert [spanish.message(sentence(row)) for row in rows] == [
        "Riesgo → 1.2 (80%)",
        "Exploración → 0.9 (80%)",
        "Nivel del senior → standard (80%)",
    ]
