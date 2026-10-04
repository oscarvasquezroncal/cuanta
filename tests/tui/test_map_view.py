from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, DataTable, Input, Static, TabbedContent

from cuanta.application.code_index import IndexService
from cuanta.application.map import MapStatus
from cuanta.domain.code_index import IndexStatus
from cuanta.tui.app import CuantaApp
from cuanta.tui.services import ContainerServices
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
        await wait_for(pilot, lambda: not view.query_one("#map-rebuild", Button).disabled)
        view.query_one("#map-rebuild", Button).press()
        await settle(app, pilot)
        assert "map_rebuild" in services.calls

    drive(make_app(services), scenario, size=(120, 46))


class SlowRebuild(FakeServices):
    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    def map_status(self, rebuild: bool = False) -> MapStatus:
        status = super().map_status(rebuild)
        if rebuild:
            self.release.wait(10)
        return status


def test_rebuild_and_revalidate_wait_while_the_map_loads() -> None:
    services = SlowRebuild()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        view = await open_map(app, pilot)
        rebuild = view.query_one("#map-rebuild", Button)
        revalidate = view.query_one("#map-revalidate", Button)
        rebuild.press()
        await wait_for(pilot, lambda: services.calls.count("map_rebuild") == 1)
        busy = (rebuild.disabled, revalidate.disabled)
        rebuild.press()
        revalidate.press()
        await pilot.pause(0.3)
        presses = (services.calls.count("map_rebuild"), services.calls.count("map_revalidate"))
        services.release.set()
        assert busy == (True, True)
        assert presses == (1, 0)
        await wait_for(pilot, lambda: not rebuild.disabled and not revalidate.disabled)

    drive(make_app(services), scenario, size=(120, 46))


def test_overlapping_map_rebuilds_in_one_app_never_refuse_each_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app.py").write_text("value = 0\n", encoding="utf-8")
    services = ContainerServices(tmp_path)
    assert services.map_status().status.files == 1
    original = IndexService.update

    def slow(self: IndexService) -> IndexStatus:
        time.sleep(3)
        return original(self)

    monkeypatch.setattr(IndexService, "update", slow)
    errors: list[Exception] = []

    def press() -> None:
        try:
            services.map_status(True)
        except Exception as error:
            errors.append(error)

    first = threading.Thread(target=press)
    first.start()
    time.sleep(0.5)
    second = threading.Thread(target=press)
    second.start()
    first.join()
    second.join()
    assert errors == []


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
