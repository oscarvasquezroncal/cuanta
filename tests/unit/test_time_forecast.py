from __future__ import annotations

from dataclasses import replace

from cuanta.application.forecast import envelope_json
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.real_costs import attempts
from cuanta.domain.time_forecast import time_forecast
from tests.unit.test_real_costs import run
from tests.unit.test_time_costs import phase_event


def test_time_forecast_keeps_configuration_groups_separate() -> None:
    runs = tuple(run(str(index), model="sonnet") for index in range(6))
    metadata = {item.id: {"variant": "low", "implementation_profile": "fast"} for item in runs}
    metadata["3"]["variant"] = "high"
    metadata["4"]["implementation_profile"] = "balanced"
    events = tuple(phase_event(str(index), index * 10, "run_wall") for index in range(5))
    found = time_forecast("feature", "sonnet", "low", "fast", attempts(runs), events, metadata)
    assert found.samples == 3
    assert found.p50_seconds == 10.0
    assert found.p90_seconds == 20.0
    assert found.profile == "fast" and found.variant == "low"


def test_time_forecast_missing_wall_or_cohort_stays_unknown() -> None:
    item = run("A", model="sonnet")
    metadata = {"A": {"variant": "low", "implementation_profile": "fast"}}
    for model, variant, profile in (("opus", "low", "fast"), ("sonnet", "", "fast")):
        found = time_forecast("feature", model, variant, profile, attempts((item,)), (), metadata)
        assert found.samples == 0
        assert found.p50_seconds is None and found.p90_seconds is None
    legacy = time_forecast("feature", "sonnet", "low", "fast", attempts((item,)), (), metadata)
    assert legacy.p50_seconds is None
    assert dict(legacy.message.params)["p50"] == "n/a"


def test_time_forecast_rejects_mixed_models_and_counts_children_once() -> None:
    runs = (run("A", model="sonnet", kind="cross"), run("B", kind="cross", parent_id="A"))
    metadata = {"A": {"variant": "low", "implementation_profile": "fast"}}
    events = (
        phase_event("A", 30, "run_wall"),
        phase_event("B", 10, "run_wall"),
        LedgerEvent(run_id="A", model="sonnet"),
        LedgerEvent(run_id="B", model="opus"),
    )
    mixed = time_forecast("feature", "sonnet", "low", "fast", attempts(runs), events, metadata)
    assert mixed.samples == 0
    same = (*events[:-1], replace(events[-1], model="sonnet"))
    found = time_forecast("feature", "sonnet", "low", "fast", attempts(runs), same, metadata)
    assert found.samples == 1 and found.p50_seconds == 30


def test_time_forecast_payload_is_added_to_envelope() -> None:
    from cuanta.adapters.storage.memory_ledger import MemoryLedger
    from tests.unit.test_forecast import planned

    result = planned(MemoryLedger())
    payload = envelope_json(result)["time"]
    assert isinstance(payload, dict)
    assert payload["p50_seconds"] is None and payload["p90_seconds"] is None
    assert payload["profile"] == "balanced" and payload["samples"] == 0


def test_forecaster_loads_history_and_persists_time_before_launch() -> None:
    import json

    from cuanta.adapters.storage.memory_ledger import MemoryLedger
    from cuanta.application.forecast import Forecaster
    from cuanta.domain.mandate import MandateRequest
    from cuanta.domain.routing import Provider
    from tests.unit.test_forecast import CATALOG, COLD_CLOCK, PRICES, sizes, team

    ledger = MemoryLedger()
    ledger.add_run(run("past", model="claude-sonnet-5"))
    ledger.add_events((phase_event("past", 42.0, "run_wall"),))
    forecaster = Forecaster(
        ledger,
        PRICES,
        lambda: CATALOG,
        sizes,
        lambda _: COLD_CLOCK,
        lambda: "2026-09-28T10:00:00Z",
        metadata=lambda _: {"variant": "low", "implementation_profile": "fast"},
    )
    found = forecaster.plan(
        "feature",
        team(),
        Provider.CLAUDE,
        "normal",
        "single",
        5,
        model="claude-sonnet-5",
        implementation_profile="fast",
        variant="low",
    )
    assert found.time.p50_seconds == 42 and found.time.p90_seconds == 42
    forecaster.record("next", found, MandateRequest(type="feature"))
    saved = ledger.forecast("next")
    assert saved is not None
    assert json.loads(saved.features)["time"]["samples"] == 1


def test_model_aliases_resolve_to_the_same_time_cohort() -> None:
    found = time_forecast(
        "feature",
        "sonnet",
        "low",
        "fast",
        attempts((run("A", model="claude-sonnet-5"),)),
        (phase_event("A", 0, "run_wall"),),
        {"A": {"variant": "low", "implementation_profile": "fast"}},
        {"sonnet": "claude-sonnet-5"},
    )
    assert found.p50_seconds == 0 and found.p90_seconds == 0 and found.samples == 1
