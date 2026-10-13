from __future__ import annotations

from cuanta.domain.engine import ModelUsage, StepUsage
from cuanta.domain.live_run import LiveRun, SpendWatch
from cuanta.domain.messages import english
from cuanta.domain.pricing import Price, PriceTable
from cuanta.domain.progress import LiveStatus
from cuanta.tui.i18n import Catalog

PRICES = PriceTable({"claude-sonnet-5-5": Price(2.0, 10.0, 2.5, 0.1)})


def step(message: str, output: int) -> StepUsage:
    return StepUsage(ModelUsage("claude-sonnet-5-5", output_tokens=output), message_id=message)


def test_the_live_line_prices_each_request_once_and_carries_the_forecast() -> None:
    live = LiveRun(0.0, spend=SpendWatch(0.5, 1.0, PRICES))
    live.observe(step("one", 20_000))
    live.observe(step("one", 20_000))
    state = live.take(1.0)
    assert state is not None
    assert state.spent_usd == 0.2
    assert (state.p50_usd, state.p90_usd) == (0.5, 1.0)
    assert live.crossed() == ()


def test_crossing_the_median_and_the_p90_warns_once_each() -> None:
    live = LiveRun(0.0, spend=SpendWatch(0.5, 1.0, PRICES))
    live.observe(step("one", 60_000))
    assert [english(item) for item in live.crossed()] == [
        "spend passed the forecast median: $0.60 of $0.50"
    ]
    assert live.crossed() == ()
    live.observe(step("two", 50_000))
    assert [english(item) for item in live.crossed()] == [
        "spend passed the forecast P90: $1.10 over $1.00"
    ]
    assert live.crossed() == ()


def test_a_jump_past_both_marks_warns_for_each_and_without_a_watch_nothing_is_priced() -> None:
    live = LiveRun(0.0, spend=SpendWatch(0.5, 1.0, PRICES))
    live.observe(step("one", 200_000))
    assert len(live.crossed()) == 2
    plain = LiveRun(0.0)
    plain.observe(step("one", 200_000))
    state = plain.take(1.0)
    assert state is not None and state.spent_usd is None
    assert plain.crossed() == ()


def test_the_line_shows_dollars_beside_the_forecast_in_both_languages() -> None:
    state = LiveStatus(
        65.0, 1200, "main", "Edit", "src/cart.py", spent_usd=0.42, p50_usd=0.6, p90_usd=1.2
    )
    assert Catalog("en").live(state) == (
        "1:05 · $0.42 (forecast $0.60–$1.20) · 1,200 tokens · main · Edit · src/cart.py"
    )
    assert Catalog("es").live(state).startswith("1:05 · $0.42 (previsto $0.60–$1.20) · 1,200")
    unknown = LiveStatus(5.0, 10, "main", "", "", spent_usd=0.01)
    assert Catalog("en").live(unknown).startswith("0:05 · $0.01 · 10 tokens")
    legacy = LiveStatus(5.0, 10, "main", "", "")
    assert Catalog("en").live(legacy).startswith("0:05 · 10 tokens")
    spanish = Catalog("es")
    live = LiveRun(0.0, spend=SpendWatch(0.5, 1.0, PRICES))
    live.observe(step("one", 60_000))
    assert [spanish.message(item) for item in live.crossed()] == [
        "el gasto pasó la mediana prevista: $0.60 de $0.50"
    ]
