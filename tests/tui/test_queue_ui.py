from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

import pytest
from textual.pilot import Pilot
from textual.widgets import Static

from cuanta.domain.cache import UNKNOWN_PREFIX, PrefixState, PrefixWindow
from cuanta.domain.queue import warm_message
from cuanta.tui.app import CuantaApp
from cuanta.tui.cache_text import queue_content
from cuanta.tui.i18n import Catalog
from tests.tui.fakes import FakeServices, snapshot
from tests.tui.test_app import drive, make_app
from tests.tui.test_snapshots import MODERN, SIZES, loaded
from tests.tui.test_t5_screens import render

SnapCompare = Callable[..., bool]
WARM = PrefixWindow(PrefixState.WARM, datetime(2026, 9, 29, 14, 32).astimezone())


def queued(prefix: PrefixWindow = WARM, count: int = 3) -> FakeServices:
    return FakeServices(home_snapshot=replace(snapshot(), queued=count, prefix=prefix))


@pytest.mark.parametrize(
    ("language", "expected"),
    [("en", "warm prefix until 14:32"), ("es", "prefijo caliente hasta 14:32")],
)
def test_the_queue_line_shows_the_warm_prefix_until_hh_mm(language: str, expected: str) -> None:
    t = Catalog(language)
    text = str(queue_content(t, 3, WARM))
    assert t("home.queue", count=3) in text and expected in text
    assert t("home.queue_command") in text
    unknown = str(queue_content(t, 1, UNKNOWN_PREFIX))
    assert t.message(warm_message(UNKNOWN_PREFIX, "")) in unknown
    assert "14:32" not in unknown


def test_home_shows_the_queue_line_only_when_mandates_are_queued() -> None:
    async def listed(app: CuantaApp, pilot: Pilot[None]) -> None:
        widget = app.query_one("#home-queue", Static)
        assert widget.display
        text = render(widget)
        assert "Queued mandates: 3" in text and "warm prefix until 14:32" in text

    drive(make_app(queued()), listed)

    async def empty(app: CuantaApp, pilot: Pilot[None]) -> None:
        assert not app.query_one("#home-queue", Static).display

    drive(make_app(FakeServices()), empty)


async def shown(pilot: Pilot[None]) -> None:
    await loaded(pilot)
    pilot.app.query_one("#home-queue").scroll_visible(animate=False)
    await loaded(pilot)


@pytest.mark.parametrize("language", ["en", "es"])
@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
def test_home_queue_snapshot(
    snap_compare: SnapCompare, language: str, size: tuple[int, int]
) -> None:
    app = CuantaApp(
        queued(), language, "calico-dark", motion=False, environ=MODERN, clock=lambda: 0.0
    )
    assert snap_compare(app, terminal_size=size, run_before=shown)


def unreadable() -> FakeServices:
    return FakeServices(home_snapshot=replace(snapshot(), queue_unreadable=True))


def test_home_warns_when_the_queue_file_is_unreadable() -> None:
    async def warned(app: CuantaApp, pilot: Pilot[None]) -> None:
        widget = app.query_one("#home-queue", Static)
        assert widget.display
        text = render(widget)
        assert app.catalog("home.queue_unreadable") in text
        assert app.catalog("home.queue_list_command") in text

    drive(make_app(unreadable()), warned)


@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
def test_home_queue_unreadable_snapshot(snap_compare: SnapCompare, size: tuple[int, int]) -> None:
    app = CuantaApp(
        unreadable(), "en", "calico-dark", motion=False, environ=MODERN, clock=lambda: 0.0
    )
    assert snap_compare(app, terminal_size=size, run_before=shown)
