from __future__ import annotations

import pytest

from cuanta.adapters.system.prices import load_prices
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.pricing import Price, PriceTable, estimate_cost
from cuanta.domain.spectrum import NO_SNAPSHOTS, analyze


def _event(model: str, cost: float | None = None) -> LedgerEvent:
    return LedgerEvent(
        kind="api_request",
        model=model,
        input_tokens=1_000_000,
        output_tokens=100_000,
        cache_read_tokens=2_000_000,
        cache_write_tokens=200_000,
        cost_usd=cost,
    )


def test_price_table_is_versioned_and_dated() -> None:
    table = load_prices()
    assert table.version == 1
    assert table.verified_on == "2026-09-23"
    assert table.label == "price table v1, verified 2026-09-23"


def test_lookup_matches_exact_dated_and_bracketed_ids() -> None:
    table = load_prices()
    assert table.lookup("claude-haiku-4-5-20251001") == table.lookup("claude-haiku-4-5")
    assert table.lookup("claude-opus-5-5[1m]") is not None
    assert table.lookup("gpt-5-codex") is None
    assert table.lookup("gpt-5") is not None


def test_estimate_uses_all_four_rates() -> None:
    table = load_prices()
    estimate = estimate_cost([_event("claude-sonnet-5")], table)
    assert estimate.value is not None
    expected = 1 * 2.0 + 0.1 * 10.0 + 2 * 0.2 + 0.2 * 2.5
    assert round(estimate.value, 6) == round(expected, 6)
    assert estimate.source == table.label


def test_reported_cost_wins_and_unknown_model_is_na() -> None:
    table = load_prices()
    reported = estimate_cost([_event("anything", cost=1.25)], table)
    assert (reported.value, reported.source) == (1.25, "reported by the engine")
    unknown = estimate_cost([_event("gpt-5-codex")], table)
    assert unknown.value is None
    assert unknown.unpriced == ("gpt-5-codex",)
    assert estimate_cost([], table).value == 0.0


def test_imported_sessions_get_na_utilization_and_priced_cost() -> None:
    report = analyze(
        "all imported sessions", [_event("claude-opus-5")], snapshots=False, prices=load_prices()
    )
    assert report.utilization.value is None
    assert report.utilization.why == NO_SNAPSHOTS
    assert report.cost.value is not None
    assert report.cost.value > 0


@pytest.mark.parametrize(
    ("price", "written", "read"),
    [(Price(1.0, 2.0, None, 0.1), 1, 0), (Price(1.0, 2.0, 1.25, None), 0, 1)],
)
def test_used_cache_bucket_with_missing_rate_makes_the_whole_estimate_unknown(
    price: Price, written: int, read: int
) -> None:
    event = LedgerEvent(
        model="partial",
        input_tokens=100,
        cache_write_tokens=written,
        cache_read_tokens=read,
    )
    estimated = estimate_cost([event], PriceTable({"partial": price}))
    assert estimated.value is None
    assert estimated.kind == "unknown"
    assert estimated.unpriced == ("partial",)


def test_unused_missing_cache_rates_do_not_prevent_estimation() -> None:
    event = LedgerEvent(model="partial", input_tokens=1_000_000, output_tokens=100_000)
    estimated = estimate_cost([event], PriceTable({"partial": Price(1.0, 2.0, None, None)}))
    assert estimated.value == pytest.approx(1.2)
    assert estimated.kind == "estimated"
    assert estimated.unpriced == ()


def test_explicit_zero_cache_rates_are_free() -> None:
    event = LedgerEvent(model="free", cache_write_tokens=1_000_000, cache_read_tokens=1_000_000)
    estimated = estimate_cost([event], PriceTable({"free": Price(1.0, 2.0, 0.0, 0.0)}))
    assert estimated.value == 0.0
    assert estimated.kind == "estimated"


def test_gpt_55_unknown_cache_write_rate_cannot_be_charged_as_free() -> None:
    table = load_prices()
    written = LedgerEvent(model="gpt-5.5", input_tokens=1_000_000, cache_write_tokens=1)
    assert estimate_cost([written], table).value is None
    fresh = LedgerEvent(model="gpt-5.5", input_tokens=1_000_000)
    assert estimate_cost([fresh], table).value == 5.0
