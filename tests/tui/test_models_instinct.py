from __future__ import annotations

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, DataTable, Input, Select, Static, Switch

from cuanta.domain.calibration import Calibration
from cuanta.tui.app import CuantaApp
from cuanta.tui.views.instinct import InstinctView
from cuanta.tui.views.models import ModelsView
from tests.tui.fakes import FakeServices
from tests.tui.test_app import drive, make_app, settle
from tests.tui.test_t5_screens import notes, render, wait_for


async def open_models(app: CuantaApp, pilot: Pilot[None]) -> ModelsView:
    await pilot.press("9")
    await settle(app, pilot)
    view = app.query_one(ModelsView)
    await wait_for(pilot, lambda: bool(view.entries))
    return view


def test_models_screen_lists_the_catalog_and_saves_a_tier() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        view = await open_models(app, pilot)
        table = view.query_one("#models-table", DataTable)
        assert table.row_count == 5
        assert "claude, codex" in render(view.query_one("#catalog-meta", Static))
        table.move_cursor(row=2)
        await pilot.pause()
        assert view.query_one("#model-tier", Select).value == "economy"
        view.query_one("#model-tier", Select).value = "standard"
        view.query_one("#models-set-tier", Button).press()
        await wait_for(pilot, lambda: bool(services.tier_changes))
        assert services.tier_changes == [("claude:haiku", "standard")]
        await wait_for(pilot, lambda: any("haiku is now" in note for note in notes(app)))

    drive(make_app(services), scenario, size=(120, 50))


def test_probe_shows_the_cost_before_spending() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        view = await open_models(app, pilot)
        view.query_one("#models-probe", Button).press()
        await wait_for(pilot, lambda: bool(services.probes))
        await wait_for(pilot, lambda: "$0.0003" in render(view.query_one("#probe-note", Static)))
        assert services.probes == [("claude:opus", False)]
        assert str(view.query_one("#models-probe", Button).label) == "Spend it"
        view.query_one("#models-probe", Button).press()
        await wait_for(pilot, lambda: len(services.probes) == 2)
        assert services.probes[-1] == ("claude:opus", True)
        await wait_for(pilot, lambda: "answered" in render(view.query_one("#probe-note", Static)))

    drive(make_app(services), scenario, size=(120, 50))


def test_routing_editor_saves_every_field() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        view = await open_models(app, pilot)
        assert view.query_one("#tier-senior", Select).value == "premium"
        view.query_one("#route-preset", Select).value = "save"
        await pilot.pause()
        assert view.query_one("#tier-senior", Select).value == "standard"
        view.query_one("#decide-docs", Switch).value = False
        view.query_one("#down-claude", Button).press()
        await pilot.pause()
        assert view.engines == ["codex", "claude", "opencode"]
        view.query_one("#cap-mandate", Input).value = "abc"
        view.query_one("#routing-save", Button).press()
        await pilot.pause()
        assert "numbers" in render(view.query_one("#routing-error", Static))
        assert services.routing_saved == {}
        view.query_one("#cap-mandate", Input).value = "3"
        view.query_one("#cap-frontier", Switch).value = True
        view.query_one("#routing-save", Button).press()
        await wait_for(pilot, lambda: bool(services.routing_saved))
        saved = services.routing_saved
        assert saved["preset"] == "save"
        assert saved["roles.senior"] == "standard"
        assert saved["decide.docs"] is False
        assert saved["decide.senior"] is True
        assert saved["engines"] == ["codex", "claude", "opencode"]
        assert saved["caps.mandate_usd"] == 3.0
        assert saved["caps.frontier"] is True
        stats = view.query_one("#routing-stats", DataTable)
        assert stats.row_count == 2

    drive(make_app(services), scenario, size=(120, 60))


def test_instinct_jev_card_tests_the_connection_and_shows_sentences() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("6")
        await settle(app, pilot)
        view = app.query_one(InstinctView)
        facts = view.query_one("#jev-facts", Static)
        await wait_for(pilot, lambda: "TYPESAFE_API_KEY" in render(facts))
        assert "42 questions" in render(facts)
        view.query_one("#jev-test", Button).press()
        status = view.query_one("#jev-status", Static)
        await wait_for(pilot, lambda: "Connected" in render(status))
        assert "typesafe/jev-1.13 via TypeSafe" in render(status)
        assert "212 ms" in render(facts)
        table = view.query_one("#decisions-table", DataTable)
        await wait_for(pilot, lambda: table.row_count == 1)
        assert "Mandate scope → normal (70%)" in str(table.get_row_at(0)[0])
        view.query_one("#instinct-preview", Button).press()
        body = view.query_one("#preview-body", Static)
        await wait_for(pilot, lambda: "login timeout" in render(body))
        assert "never your code" in render(view.query_one("#preview-help", Static))

    drive(make_app(services), scenario, size=(120, 50))


@pytest.mark.parametrize("language", ["en", "es"])
def test_instinct_shows_forecast_calibration_only_once_forecasts_exist(language: str) -> None:
    rows = (
        Calibration("claude", "fix", 6, 1, 0.0421, 0.18, 5 / 6, 0.31),
        Calibration("codex", "feature", 2, 0, None, None, None, None),
    )
    services = FakeServices(calibration=rows)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("6")
        await settle(app, pilot)
        view = app.query_one(InstinctView)
        table = view.query_one("#calibration-table", DataTable)
        await wait_for(pilot, lambda: table.row_count == 2)
        assert view.query_one("#calibration-card").display
        first = [str(cell) for cell in table.get_row_at(0)]
        assert first == ["claude", "fix", "6", "1", "$0.0421", "18%", "83%", "31%"]
        missing = "n/a" if language == "en" else "n/d"
        assert [str(cell) for cell in table.get_row_at(1)][4:] == [missing] * 4
        title = "Forecast calibration" if language == "en" else "Calibración del pronóstico"
        assert title in render(view.query_one("#calibration-card .card-title", Static))

    drive(make_app(services, language=language), scenario, size=(120, 50))


def test_instinct_hides_calibration_without_forecasts() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("6")
        await settle(app, pilot)
        view = app.query_one(InstinctView)
        await wait_for(pilot, lambda: view.query_one("#decisions-table", DataTable).row_count == 1)
        await settle(app, pilot)
        assert not view.query_one("#calibration-card").display
        assert view.query_one("#calibration-table", DataTable).row_count == 0

    drive(make_app(FakeServices()), scenario, size=(120, 50))
