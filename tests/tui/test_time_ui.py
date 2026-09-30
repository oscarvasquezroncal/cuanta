from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest
from textual.pilot import Pilot
from textual.widgets import Static, TabbedContent

from cuanta.application.spectrum import SpectrumResult
from cuanta.domain.time_anatomy import PHASES, PhaseTime, RequestTime, RoleTime, TimeReport
from cuanta.tui.app import CuantaApp
from cuanta.tui.i18n import Catalog
from cuanta.tui.screens.result import ResultScreen
from cuanta.tui.time_text import phase_label, time_content
from cuanta.tui.views.spectrum import SpectrumView
from tests.tui.fakes import FakeServices, sample_result
from tests.tui.test_app import drive, make_app, settle
from tests.tui.test_t5_screens import render, wait_for

SnapCompare = Callable[..., bool]


def sample_time() -> TimeReport:
    phases = (
        PhaseTime("sandbox_copy", 8.125, 1),
        PhaseTime("verification", 11.250, 1),
        PhaseTime("hooks"),
        PhaseTime("tool:Read", 0.5, 1),
    )
    return TimeReport(
        90.5,
        phases,
        (RoleTime("writer", (PhaseTime("verification", 11.250, 1),)),),
        (RequestTime("request-1", "writer", "sonnet", 20.125, 0.75, "implementation"),),
    )


class TimeServices(FakeServices):
    def __init__(self, time: TimeReport | None = None) -> None:
        super().__init__()
        self.time = sample_time() if time is None else time

    def spectrum(self, run_id: str) -> SpectrumResult:
        return replace(super().spectrum(run_id), time=self.time)


async def open_time(app: CuantaApp, pilot: Pilot[None], surface: str) -> Static:
    services = app.services
    assert isinstance(services, TimeServices)
    if surface == "result":
        app.push_screen(
            ResultScreen(services, app.catalog, replace(sample_result(), time=services.time))
        )
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        await settle(app, pilot)
        app.screen.query_one("#result-tabs", TabbedContent).active = "tab-time"
        widget = app.screen.query_one("#result-time", Static)
    else:
        await pilot.press("4")
        await wait_for(pilot, lambda: bool(app.query(SpectrumView)))
        view = app.query_one(SpectrumView)
        await wait_for(pilot, lambda: view.result is not None)
        view.query_one("#spectrum-tabs", TabbedContent).active = "tab-time"
        widget = view.query_one("#spectrum-time", Static)
    await settle(app, pilot)
    return widget


@pytest.mark.parametrize("language", ["en", "es"])
def test_time_text_localizes_every_phase_and_keeps_unknown_seconds(language: str) -> None:
    catalog = Catalog(language)
    missing = str(time_content(catalog, TimeReport()))
    assert catalog("time.title") in missing
    assert missing.count("n/a") == len(PHASES) + 2
    assert "0.000" not in missing
    assert all(catalog(f"time.{phase}") in missing for phase in PHASES)
    shown = str(time_content(catalog, sample_time()))
    for value in ("90.500", "8.125", "11.250", "20.125", "0.750", "Read", "request-1"):
        assert value in shown
    assert phase_label(catalog, "custom_phase") == "custom_phase"


@pytest.mark.parametrize("surface", ["result", "spectrum"])
@pytest.mark.parametrize("language", ["en", "es"])
def test_both_surfaces_display_wall_phases_roles_and_request_times(
    surface: str, language: str
) -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        text = render(await open_time(app, pilot, surface))
        assert app.catalog("time.title") in text
        assert app.catalog("time.verification") in text
        assert "90.500" in text and "11.250" in text and "0.750" in text
        assert "n/a" in text

    drive(make_app(TimeServices(), language=language), scenario, size=(100, 40))


@pytest.mark.parametrize("surface", ["result", "spectrum"])
@pytest.mark.parametrize("language", ["en", "es"])
def test_time_snapshot(snap_compare: SnapCompare, surface: str, language: str) -> None:
    app = make_app(TimeServices(), language=language)

    async def shown(pilot: Pilot[None]) -> None:
        await open_time(app, pilot, surface)

    assert snap_compare(app, terminal_size=(120, 46), run_before=shown)
