from __future__ import annotations

from collections.abc import Callable

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, DataTable, Input, Static, TabbedContent

from cuanta.tui.app import CuantaApp
from cuanta.tui.views.map import MapView
from tests.tui.fakes import FakeServices
from tests.tui.test_app import drive, make_app, settle
from tests.tui.test_snapshots import app_for
from tests.tui.test_t5_screens import render, wait_for

SnapCompare = Callable[..., bool]


async def open_map(app: CuantaApp, pilot: Pilot[None]) -> MapView:
    await pilot.press("g")
    await wait_for(pilot, lambda: bool(app.query(MapView)))
    await settle(app, pilot)
    view = app.query_one(MapView)
    await wait_for(pilot, lambda: view.status is not None)
    return view


def test_map_search_fresh_stale_impact_and_revalidation() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        view = await open_map(app, pilot)
        assert "2 indexed files" in render(view.query_one("#map-files", Static))
        assert "Semantic off" in render(view.query_one("#map-semantic", Static))
        assert "90%" in render(view.query_one("#map-coverage", Static))
        view.query_one("#map-query", Input).value = "checkout"
        view.query_one("#map-search", Button).press()
        await wait_for(pilot, lambda: view.details is not None)
        assert view.query_one("#map-hits", DataTable).row_count == 1
        assert "Matched terms: cart, checkout" in render(view.query_one("#map-reasons", Static))
        assert "Checkout totals" in render(view.query_one("#map-fresh-facts", Static))
        assert "Old selector" in render(view.query_one("#map-stale-facts", Static))
        assert "src/shop/page.py" in render(view.query_one("#map-impact", Static))
        view.query_one("#map-file-tabs", TabbedContent).active = "map-stale-pane"
        await pilot.pause()
        assert "·" not in render(view.query_one("#map-stale-facts", Static))
        view.query_one("#map-revalidate", Button).press()
        await settle(app, pilot)
        assert services.calls.count("map_revalidate") == 1
        view.query_one("#map-rebuild", Button).press()
        await settle(app, pilot)
        assert "map_rebuild" in services.calls

    drive(make_app(services), scenario, size=(120, 46))


def test_map_labels_and_rank_reasons_are_in_spanish() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        view = await open_map(app, pilot)
        assert "Semántica desactivada" in render(view.query_one("#map-semantic", Static))
        assert "archivos indexados" in render(view.query_one("#map-files", Static))
        view.search("checkout")
        await wait_for(pilot, lambda: view.details is not None)
        assert "Términos coincidentes" in render(view.query_one("#map-reasons", Static))
        assert "Propósito" in render(view.query_one("#map-card", Static))
        assert "2 indexed files" not in render(view.query_one("#map-files", Static))

    drive(make_app(language="es"), scenario, size=(120, 46))


@pytest.mark.parametrize("theme", ["calico-dark", "calico-light"])
@pytest.mark.parametrize("language", ["en", "es"])
def test_map_snapshot(snap_compare: SnapCompare, theme: str, language: str) -> None:
    app = app_for(theme)
    if language == "es":
        app = make_app(language=language)
        app.theme = theme

    async def searched(pilot: Pilot[None]) -> None:
        view = await open_map(app, pilot)
        view.search("checkout")
        await wait_for(pilot, lambda: view.details is not None)
        await settle(app, pilot)

    assert snap_compare(app, terminal_size=(120, 46), run_before=searched)
