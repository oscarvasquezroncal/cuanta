from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, Static, TabbedContent

from cuanta.application.spectrum import SpectrumResult
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.messages import msg
from cuanta.domain.read_efficiency import ReadEfficiency, read_efficiency
from cuanta.tui.app import CuantaApp
from cuanta.tui.i18n import Catalog
from cuanta.tui.read_efficiency_text import read_efficiency_content, utilization_note
from cuanta.tui.screens.result import ResultScreen
from cuanta.tui.views.spectrum import SpectrumView
from tests.tui.fakes import FakeServices, sample_result
from tests.tui.test_app import drive, make_app, settle
from tests.tui.test_t5_screens import render, wait_for

SnapCompare = Callable[..., bool]


def observed_efficiency(task_type: str) -> ReadEfficiency:
    events = [
        LedgerEvent(kind="tool_result", tool_name="Read", file_path="src/a.py", success=True),
        LedgerEvent(
            kind="index_call",
            source="cuanta_mcp",
            tool_name="page",
            file_path="src/b.py",
            success=True,
        ),
        LedgerEvent(kind="tool_result", tool_name="Read", file_path="src/c.py", success=True),
    ]
    return read_efficiency(events, "The behavior starts at src/a.py:5.", ("src/b.py",), task_type)


class EfficiencyServices(FakeServices):
    def __init__(self, task_type: str = "investigation", *, empty: bool = False) -> None:
        super().__init__()
        self.task_type = task_type
        self.efficiency = ReadEfficiency() if empty else observed_efficiency(task_type)

    def spectrum(self, run_id: str) -> SpectrumResult:
        result = super().spectrum(run_id)
        use = replace(
            result.report.utilization,
            value=self.efficiency.value,
            label=self.efficiency.label,
            formula=self.efficiency.formula,
            why=msg(f"read_efficiency.{self.efficiency.why}")
            if self.efficiency.value is None
            else None,
        )
        return replace(
            result,
            report=replace(result.report, read_efficiency=self.efficiency, utilization=use),
        )


async def open_efficiency(app: CuantaApp, pilot: Pilot[None], surface: str) -> Static:
    services = app.services
    assert isinstance(services, EfficiencyServices)
    if surface == "result":
        view = replace(
            sample_result(), task_type=services.task_type, read_efficiency=services.efficiency
        )
        app.push_screen(ResultScreen(services, app.catalog, view))
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        await settle(app, pilot)
        app.screen.query_one("#result-tabs", TabbedContent).active = "tab-consumption"
        widget = app.screen.query_one("#result-read-efficiency", Static)
    else:
        await pilot.press("4")
        await wait_for(pilot, lambda: bool(app.query(SpectrumView)))
        spectrum = app.query_one(SpectrumView)
        await wait_for(pilot, lambda: spectrum.result is not None)
        widget = spectrum.query_one("#metric-utilization .metric-note", Static)
    await settle(app, pilot)
    return widget


@pytest.mark.parametrize("language", ["en", "es"])
@pytest.mark.parametrize(("task_type", "useful"), [("investigation", 1), ("bug", 2)])
def test_the_localized_summary_shows_the_ratio_and_its_actual_formula(
    language: str, task_type: str, useful: int
) -> None:
    t = Catalog(language)
    metric = observed_efficiency(task_type)
    content = str(read_efficiency_content(t, metric))
    assert f"{useful / 3:.0%}" in content
    assert t("read_efficiency.counts", useful=useful, read=3) in content
    key = "formula_investigation" if task_type == "investigation" else "formula_code"
    assert t(f"read_efficiency.{key}") in content
    assert t("read_efficiency.label_v2") in content
    assert "heuristic v1" not in content


@pytest.mark.parametrize("surface", ["result", "spectrum"])
@pytest.mark.parametrize("language", ["en", "es"])
@pytest.mark.parametrize(("task_type", "useful"), [("investigation", 1), ("bug", 2)])
@pytest.mark.parametrize("width", [80, 120])
def test_actual_consumption_and_spectrum_show_visible_formula_and_keep_controls(
    surface: str, language: str, task_type: str, useful: int, width: int
) -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        widget = await open_efficiency(app, pilot, surface)
        t = app.catalog
        text = render(widget)
        assert t("read_efficiency.counts", useful=useful, read=3) in text
        formula = "formula_investigation" if task_type == "investigation" else "formula_code"
        assert t(f"read_efficiency.{formula}") in text
        assert widget.region.height > 1
        assert widget.region.x >= 0
        assert widget.region.right <= width
        if surface == "result":
            assert f"{useful / 3:.0%}" in text
            assert app.screen.query_one("#result-save", Button).display
            assert app.screen.query_one("#result-continue", Button).display
            assert app.screen.query_one("#result-tabs", TabbedContent).active == "tab-consumption"
        else:
            value = app.query_one("#metric-utilization .metric-value", Static)
            assert f"{useful / 3:.0%}" in render(value)
            metric = app.query_one("#metric-utilization")
            assert widget.region.y >= metric.region.y
            assert widget.region.bottom <= metric.region.bottom
            assert app.query_one("#spectrum-import", Button).display
            assert app.query_one("#spectrum-tabs", TabbedContent).active == "tab-agent"

    drive(make_app(EfficiencyServices(task_type), language=language), scenario, size=(width, 46))


@pytest.mark.parametrize("surface", ["result", "spectrum"])
@pytest.mark.parametrize("language", ["en", "es"])
def test_missing_observations_are_unknown_in_actual_views(surface: str, language: str) -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        widget = await open_efficiency(app, pilot, surface)
        reason = (
            app.catalog("read_efficiency.unknown")
            if surface == "result"
            else app.catalog.message(msg("read_efficiency.no_reads"))
        )
        assert reason in render(widget)
        value = (
            render(widget)
            if surface == "result"
            else render(app.query_one("#metric-utilization .metric-value", Static))
        )
        assert app.catalog("spectrum.na") in value
        assert "0%" not in value

    drive(make_app(EfficiencyServices(empty=True), language=language), scenario, size=(120, 46))


@pytest.mark.parametrize("language", ["en", "es"])
def test_pipeline_fallback_keeps_the_v1_formula_and_label(language: str) -> None:
    t = Catalog(language)
    note = utilization_note(t, "heuristic v1", "useful tool tokens / total API tokens")
    assert t("spectrum.heuristic_label") in note
    assert t("read_efficiency.formula_v1") in note
    assert t("read_efficiency.label_v2") not in note


def test_result_refresh_repaints_the_observed_efficiency() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        widget = await open_efficiency(app, pilot, "result")
        screen = app.screen
        assert isinstance(screen, ResultScreen)
        assert "33%" in render(widget)
        screen._refresh(replace(screen.view, read_efficiency=observed_efficiency("bug")))
        await settle(app, pilot)
        assert "67%" in render(widget)
        assert "2 useful / 3 files read" in render(widget)

    drive(make_app(EfficiencyServices()), scenario, size=(120, 46))


@pytest.mark.parametrize("theme", ["calico-dark", "calico-light"])
@pytest.mark.parametrize("language", ["en", "es"])
@pytest.mark.parametrize("surface", ["result", "spectrum"])
def test_read_efficiency_snapshot(
    snap_compare: SnapCompare, theme: str, language: str, surface: str
) -> None:
    app = make_app(EfficiencyServices("bug" if surface == "result" else "investigation"), language)
    app.theme = theme

    async def shown(pilot: Pilot[None]) -> None:
        await open_efficiency(app, pilot, surface)

    assert snap_compare(app, terminal_size=(120, 46), run_before=shown)
