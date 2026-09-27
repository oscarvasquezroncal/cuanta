from __future__ import annotations

from dataclasses import replace

from textual.pilot import Pilot
from textual.widgets import Button, Static, TabbedContent

from cuanta.application.spectrum import SpectrumResult
from cuanta.domain.index_metrics import IndexMetrics
from cuanta.tui.app import CuantaApp
from cuanta.tui.i18n import Catalog
from cuanta.tui.index_text import index_lines
from cuanta.tui.screens.result import ResultScreen
from cuanta.tui.views.map import MapView
from cuanta.tui.views.spectrum import SpectrumView
from tests.tui.fakes import FakeServices, sample_result
from tests.tui.test_app import drive, make_app, settle
from tests.tui.test_t5_screens import render, wait_for

METRICS = IndexMetrics(
    index_calls=3,
    raw_reads=1,
    exploration_calls=4,
    index_hit_rate=0.75,
    exploration_tokens_estimate=275,
    stale_facts=2,
    findings_saved=4,
    guard_violations=("src/protected.py",),
    out_of_plan_edits=("src/extra.py",),
)


class IndexedServices(FakeServices):
    def spectrum(self, run_id: str) -> SpectrumResult:
        result = super().spectrum(run_id)
        return replace(result, report=replace(result.report, index=METRICS))


def test_result_consumption_and_findings_saved_open_map() -> None:
    view = replace(sample_result(), index=METRICS)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        app.push_screen(ResultScreen(app.services, app.catalog, view))
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        await settle(app, pilot)
        screen = app.screen
        assert isinstance(screen, ResultScreen)
        saved = render(screen.query_one("#result-map-summary", Static))
        assert "4 findings saved to the Map" in saved
        assert "Edits outside the plan: 1" in saved and "src/extra.py" in saved
        assert "Protected-path edits: 1" in saved and "src/protected.py" in saved
        screen.query_one("#result-tabs", TabbedContent).active = "tab-consumption"
        content = render(screen.query_one("#result-consumption", Static))
        assert "Index hit rate: 75%" in content
        assert "Estimated exploration tokens: 275" in content
        assert "3 index calls, 1 raw reads, 4 exploration calls" in content
        screen.query_one("#result-map", Button).press()
        await wait_for(pilot, lambda: app.section == "map")
        await settle(app, pilot)
        assert app.query_one(MapView).status is not None

    drive(make_app(), scenario, size=(120, 48))


def test_spectrum_shows_same_index_math_with_estimate_label() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("4")
        await wait_for(pilot, lambda: bool(app.query(SpectrumView)))
        await settle(app, pilot)
        view = app.query_one(SpectrumView)
        content = render(view.query_one("#spectrum-index", Static))
        assert "Index hit rate: 75%" in content
        assert "Estimated exploration tokens: 275 (returned bytes ÷ 4)" in content
        assert "Stale facts: 2" in content
        assert "Protected-path edits: 1" in content

    drive(make_app(IndexedServices()), scenario, size=(120, 48))


def test_no_exploration_rate_stays_unknown_and_spanish_estimate_is_explicit() -> None:
    t = Catalog("en")
    assert f"Index hit rate: {t('spectrum.na')}" in index_lines(t, IndexMetrics())
    spanish = "\n".join(index_lines(Catalog("es"), METRICS))
    assert "Tokens de exploración estimados: 275" in spanish
    assert "Uso del índice: 75%" in spanish
