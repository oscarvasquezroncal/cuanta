from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest
from textual.pilot import Pilot
from textual.widgets import DataTable, Static, TabbedContent

from cuanta.application.spectrum import SpectrumResult
from cuanta.domain.cost_trend import CostTrend, TrendRow
from cuanta.domain.governor_report import BlockedCalls
from cuanta.domain.run_metrics import BUCKETS, BucketMetric, Reactions, RunMetrics
from cuanta.tui.app import CuantaApp
from cuanta.tui.i18n import Catalog
from cuanta.tui.metrics_text import metrics_content, trend_cells
from cuanta.tui.screens.result import ResultScreen
from cuanta.tui.views.spectrum import TREND_TAB, SpectrumView
from tests.tui.fakes import FakeServices, sample_result
from tests.tui.test_app import drive, make_app, settle
from tests.tui.test_snapshots import SIZES
from tests.tui.test_t5_screens import render, wait_for

SnapCompare = Callable[..., bool]

FULL = RunMetrics(
    run_id="01JMANDATE0000000000000RUN1",
    provider="claude",
    task_type="feature",
    started_at="2026-09-24T10:00:00",
    outcome="accepted",
    p50_usd=0.31,
    p90_usd=0.45,
    cap_usd=0.6,
    actual_usd=0.38,
    buckets=(
        BucketMetric("start", 40_100, 38_200),
        BucketMetric("exploration", 20_000, 55_400),
        BucketMetric("writing", 5_000, 7_100),
        BucketMetric("verification", 4_000, None),
        BucketMetric("handoff", 600, 450),
    ),
    total_tokens=412_160,
    changed_lines=64,
    blocked=BlockedCalls(reads=3, searches=1, tests=2, tokens=12_500),
    finishes=Reactions(1, None),
    rotations=Reactions(1, 0.224),
    first_warm_share=0.78,
    warm_share=0.91,
    pack_tokens=4_812,
    senior_input_tokens=52_300,
)
UNKNOWN = RunMetrics(
    run_id="01JMANDATE0000000000000RUN1", buckets=tuple(BucketMetric(name) for name in BUCKETS)
)
TREND = CostTrend(
    20,
    9,
    (
        TrendRow("claude", "fix", 3, 1, 0.52, True, False, (None, 0.61, 0.52)),
        TrendRow(
            "claude",
            "feature",
            4,
            2,
            0.44,
            False,
            False,
            (0.2, 0.35, 0.51, 0.44),
        ),
        TrendRow("codex", "feature", 2, 1, 1.18, False, True, (None, 1.18)),
    ),
)


@pytest.mark.parametrize("language", ["en", "es"])
def test_the_panel_lists_every_metric(language: str) -> None:
    t = Catalog(language)
    text = str(metrics_content(t, FULL))
    assert t("metrics_panel.title") in text
    for value in ("$0.3100", "$0.4500", "$0.3800", "$0.6000", "63%", "$0.0700"):
        assert value in text
    for name in BUCKETS:
        assert t(f"metrics_panel.bucket_{name}") in text
    assert "38,200" in text and "55,400" in text
    assert t("metrics_panel.tokens", count="6,440") in text
    assert t("metrics_panel.tokens", count="12,500") in text
    assert "78%" in text and "91%" in text
    assert t("metrics_panel.tokens", count="4,812") in text
    assert t("metrics_panel.tokens", count="52,300") in text
    assert "$0.2240" in text


@pytest.mark.parametrize("language", ["en", "es"])
def test_unknown_values_read_na_never_zero(language: str) -> None:
    t = Catalog(language)
    text = str(metrics_content(t, UNKNOWN))
    assert "$0" not in text and "0%" not in text
    assert t("metrics_panel.tokens", count="0") not in text
    assert text.count(t("spectrum.na")) == 28
    estimated = str(metrics_content(t, replace(FULL, estimated=True)))
    assert t("cost.estimated", cost="$0.3800") in estimated
    below = str(metrics_content(t, replace(FULL, actual_usd=0.5)))
    assert "-$0.0500" in below


async def open_metrics(app: CuantaApp, pilot: Pilot[None], metrics: RunMetrics) -> Static:
    view = replace(sample_result(), task_type="feature", metrics=metrics)
    app.push_screen(ResultScreen(app.services, app.catalog, view))
    await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
    await settle(app, pilot)
    app.screen.query_one("#result-tabs", TabbedContent).active = "tab-consumption"
    await settle(app, pilot)
    widget = app.screen.query_one("#result-metrics", Static)
    widget.scroll_visible(animate=False)
    await settle(app, pilot)
    return widget


def test_the_result_shows_metrics_only_for_attempts() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        widget = await open_metrics(app, pilot, FULL)
        assert app.catalog("metrics_panel.title") in render(widget)
        app.pop_screen()
        app.push_screen(ResultScreen(app.services, app.catalog, sample_result()))
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        await settle(app, pilot)
        assert not app.screen.query("#result-metrics")

    drive(make_app(FakeServices()), scenario, size=(120, 46))


@pytest.mark.parametrize("language", ["en", "es"])
@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
def test_metrics_result_snapshot(
    snap_compare: SnapCompare, language: str, size: tuple[int, int]
) -> None:
    app = make_app(FakeServices(), language)

    async def shown(pilot: Pilot[None]) -> None:
        await open_metrics(app, pilot, FULL)

    assert snap_compare(app, terminal_size=size, run_before=shown)


class TrendServices(FakeServices):
    def __init__(self, trend: CostTrend) -> None:
        super().__init__()
        self.trend = trend

    def spectrum(self, run_id: str) -> SpectrumResult:
        return replace(super().spectrum(run_id), trend=self.trend)


async def open_trend(app: CuantaApp, pilot: Pilot[None]) -> SpectrumView:
    await pilot.press("4")
    await wait_for(pilot, lambda: bool(app.query(SpectrumView)))
    view = app.query_one(SpectrumView)
    await wait_for(pilot, lambda: view.result is not None)
    await settle(app, pilot)
    tabs = view.query_one("#spectrum-tabs", TabbedContent)
    if not tabs.get_tab(TREND_TAB).has_class("-hidden"):
        tabs.active = TREND_TAB
    await settle(app, pilot)
    return view


@pytest.mark.parametrize("language", ["en", "es"])
def test_the_trend_rows_show_cost_per_accepted_change(language: str) -> None:
    t = Catalog(language)
    rows = [trend_cells(t, row) for row in TREND.rows]
    assert rows[0] == (
        t("costs.mix_claude"),
        t("costs.type_fix"),
        "3",
        "1",
        "≥$0.52",
        "·█▁",
    )
    assert rows[1][4] == "$0.44" and rows[1][5] == "▁▄█▆"
    assert rows[2][0] == t("costs.mix_codex") and rows[2][4] == "$1.18*"


def test_the_spectrum_shows_the_trend_tab_only_with_attempts() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        view = await open_trend(app, pilot)
        assert view.query_one("#spectrum-tabs", TabbedContent).active == TREND_TAB
        table = view.query_one("#spectrum-trend", DataTable)
        assert table.row_count == 3
        assert app.catalog("spectrum.trend_title", limit=20) in render(
            view.query_one("#spectrum-trend-title", Static)
        )

    drive(make_app(TrendServices(TREND)), scenario)

    async def hidden(app: CuantaApp, pilot: Pilot[None]) -> None:
        view = await open_trend(app, pilot)
        tabs = view.query_one("#spectrum-tabs", TabbedContent)
        assert tabs.get_tab(TREND_TAB).has_class("-hidden")
        assert tabs.active != TREND_TAB

    drive(make_app(FakeServices()), hidden)


@pytest.mark.parametrize("language", ["en", "es"])
@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
def test_spectrum_trend_snapshot(
    snap_compare: SnapCompare, language: str, size: tuple[int, int]
) -> None:
    app = make_app(TrendServices(TREND), language)

    async def shown(pilot: Pilot[None]) -> None:
        view = await open_trend(app, pilot)
        view.query_one("#spectrum-tabs", TabbedContent).scroll_visible(animate=False)
        await settle(app, pilot)

    assert snap_compare(app, terminal_size=size, run_before=shown)
