from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets import Static, TabbedContent

from cuanta.application.spectrum import SpectrumResult
from cuanta.domain.anatomy import AnatomyReport, Phase, analyze_anatomy
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.spectrum import resolve_agents
from cuanta.tui.anatomy_text import PHASE_STYLES, anatomy_content, phase_stack
from cuanta.tui.app import CuantaApp
from cuanta.tui.i18n import Catalog
from cuanta.tui.screens.result import ResultScreen
from cuanta.tui.views.spectrum import SpectrumView
from tests.tui.fakes import FakeServices, sample_result
from tests.tui.test_app import drive, make_app, settle
from tests.tui.test_t5_screens import render, wait_for

SnapCompare = Callable[..., bool]


def real_anatomy() -> AnatomyReport:
    fixture = Path(__file__).parents[1] / "fixtures" / "telemetry" / "real_investigation.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    return analyze_anatomy(resolve_agents([LedgerEvent(**value) for value in payload["events"]]))


class AnatomyServices(FakeServices):
    def __init__(self, anatomy: AnatomyReport | None = None) -> None:
        super().__init__()
        self.anatomy = anatomy if anatomy is not None else real_anatomy()

    def spectrum(self, run_id: str) -> SpectrumResult:
        result = super().spectrum(run_id)
        return replace(result, report=replace(result.report, anatomy=self.anatomy))


async def open_anatomy(app: CuantaApp, pilot: Pilot[None], surface: str) -> Static:
    if surface == "result":
        services = app.services
        assert isinstance(services, AnatomyServices)
        result_view = replace(sample_result(), anatomy=services.anatomy)
        app.push_screen(ResultScreen(services, app.catalog, result_view))
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        await settle(app, pilot)
        app.screen.query_one("#result-tabs", TabbedContent).active = "tab-consumption"
        widget = app.screen.query_one("#result-anatomy", Static)
    else:
        await pilot.press("4")
        await wait_for(pilot, lambda: bool(app.query(SpectrumView)))
        spectrum_view = app.query_one(SpectrumView)
        await wait_for(pilot, lambda: spectrum_view.result is not None)
        spectrum_view.query_one("#spectrum-tabs", TabbedContent).active = "tab-anatomy"
        widget = spectrum_view.query_one("#spectrum-anatomy", Static)
    await settle(app, pilot)
    return widget


def test_real_anatomy_is_one_colored_stack_with_exact_phase_table() -> None:
    report = real_anatomy()
    content = anatomy_content(Catalog("en"), report)
    lines = str(content).splitlines()
    assert lines[1] == "█" * 40
    assert len({span.style for span in content.spans if span.start < len(lines[0]) + 41}) == 5
    assert {phase for phase, _ in phase_stack(report)} == set(Phase)
    for value in ("65,648", "367,203", "59,855", "49,255", "$0.25", "$0.18", "$0.06", "$0.05"):
        assert value in str(content)
    assert "Observed phases" in str(content)
    assert "±50% + 256" in str(content)
    assert set(PHASE_STYLES.values()) <= {span.style for span in content.spans}


def test_anatomy_empty_and_missing_cost_are_localized() -> None:
    assert "Sin telemetría" in str(anatomy_content(Catalog("es"), AnatomyReport()))
    report = real_anatomy()
    unknown = replace(
        report,
        phases=tuple(
            replace(row, totals=replace(row.totals, cost_usd=None)) for row in report.phases
        ),
    )
    content = str(anatomy_content(Catalog("es"), unknown))
    assert "Anatomía del costo" in content
    assert "Exploración" in content
    assert "Fases observadas" in content
    assert Catalog("es")("spectrum.na") in content
    assert "Start" not in content
    assert "Handoff" not in content


@pytest.mark.parametrize("surface", ["result", "spectrum"])
@pytest.mark.parametrize("language", ["en", "es"])
def test_both_surfaces_show_the_same_phase_counts_and_costs(surface: str, language: str) -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        widget = await open_anatomy(app, pilot, surface)
        text = render(widget)
        assert app.catalog("anatomy.title") in text
        assert "65,648" in text
        assert "367,203" in text
        assert "49,255" in text
        assert "$0.25" in text
        assert "·" not in text
        assert widget.region.width >= 50

    drive(make_app(AnatomyServices(), language=language), scenario, size=(80, 34))


@pytest.mark.parametrize("surface", ["result", "spectrum"])
def test_no_telemetry_is_shown_as_unknown_in_both_surfaces(surface: str) -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        widget = await open_anatomy(app, pilot, surface)
        text = render(widget)
        assert "Sin telemetría" in text
        assert "█" not in text

    drive(make_app(AnatomyServices(AnatomyReport()), language="es"), scenario, size=(120, 46))


@pytest.mark.parametrize("theme", ["calico-dark", "calico-light"])
@pytest.mark.parametrize("language", ["en", "es"])
@pytest.mark.parametrize("surface", ["result", "spectrum"])
def test_anatomy_snapshot(
    snap_compare: SnapCompare, theme: str, language: str, surface: str
) -> None:
    app = make_app(AnatomyServices(), language=language)
    app.theme = theme

    async def shown(pilot: Pilot[None]) -> None:
        await open_anatomy(app, pilot, surface)

    assert snap_compare(app, terminal_size=(120, 46), run_before=shown)
