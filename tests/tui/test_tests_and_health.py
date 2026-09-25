from __future__ import annotations

import os
import sys

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, DataTable, Input, Static, TextArea

from cuanta.domain.fixes import FixAction
from cuanta.tui.app import CuantaApp
from cuanta.tui.screens.capsule import CapsuleScreen, lexer_for, render_lines
from cuanta.tui.views.health import CheckRow, HealthView
from cuanta.tui.views.tests import TestsView
from tests.tui.fakes import GREEN, RED, FakeServices
from tests.tui.test_app import at, drive, make_app, settle, text_of


def on_capsule(app: CuantaApp) -> bool:
    return isinstance(app.screen, CapsuleScreen)


def notes(app: CuantaApp) -> list[str]:
    return [str(note.message) for note in app._notifications]


def test_red_suite_clicks_through_to_capsule_viewer() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("2")
        assert "No test runs yet" in text_of(app, "#tests-empty")
        view = app.query_one("#tests", TestsView)
        view.query_one("#run-tests", Button).press()
        for _ in range(200):
            if view.summary is not None and not view.running:
                break
            await pilot.pause(0.02)
        await settle(app, pilot)
        assert services.calls.count("run_tests") == 1
        assert "Tests finished: 12 failed in 2 hairballs" in notes(app)
        table = app.query_one("#hairballs", DataTable)
        assert table.row_count == 2
        assert "12 failed, 400 passed in 8.4s" in text_of(app, "#tests-summary")
        table.focus()
        await pilot.press("enter")
        await settle(app, pilot)
        screen = app.screen
        assert isinstance(screen, CapsuleScreen)
        assert screen.hairball is not None
        assert screen.hairball.id == "sig-a1b2"
        body = str(screen.query_one("#body-L2", Static).render())
        assert "assert total == 10" in body
        assert "[total]" in body
        await pilot.click("#capsule-search")
        await pilot.press(*"keyerror")
        await pilot.pause()
        assert str(screen.query_one("#capsule-matches", Static).render()) == "1 matches"
        await pilot.click("#capsule-fix")
        await pilot.pause()
        assert not isinstance(app.screen, CapsuleScreen)
        assert await at(app, pilot, "mandates")
        assert app.mandate_prefill is None
        what = app.query_one("#wiz-what", TextArea).text
        assert what == "make cuanta test green by fixing sig-a1b2"

    drive(make_app(services), scenario)


def test_green_run_toast_uses_the_action_name() -> None:
    services = FakeServices(run_result=GREEN)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.click("#action-tests")
        await settle(app, pilot)
        assert await at(app, pilot, "tests")
        assert "Tests finished: 412 passed" in notes(app)
        assert not app.query_one("#hairballs", DataTable).display
        assert str(app.query_one("#run-tests", Button).label) == "Run tests"

    drive(make_app(services), scenario)


def test_tests_screen_shows_the_last_stored_run() -> None:
    services = FakeServices(latest=RED)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("2")
        await settle(app, pilot)
        assert app.query_one("#hairballs", DataTable).row_count == 2
        assert "cap-9f8e" not in text_of(app, "#tests-summary")

    drive(make_app(services), scenario)


def test_capsule_escape_returns_to_tests() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("2")
        await settle(app, pilot)
        view = app.query_one("#tests", TestsView)
        view.post_message(TestsView.OpenCapsule(RED, None))
        await settle(app, pilot)
        assert on_capsule(app)
        app.screen.query_one("#capsule-search", Input).blur()
        await pilot.press("escape")
        assert not on_capsule(app)
        assert await at(app, pilot, "tests")

    drive(make_app(), scenario)


def test_health_lists_every_check_and_copies_external_fixes() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("7")
        await pilot.pause()
        rows = list(app.query(CheckRow))
        assert len(rows) == 6
        assert "3 ok, 3 warnings, 0 failed" in text_of(app, "#health-summary")
        codex = next(row for row in rows if row.check.name == "engine codex")
        await pilot.click(f"#{codex.id} .fix-button")
        await pilot.pause()
        assert services.copied == ["npm install -g @openai/codex"]
        assert app.clipboard == "npm install -g @openai/codex"
        assert "Copied: npm install -g @openai/codex" in notes(app)

    drive(make_app(services), scenario)


def test_health_runs_cuanta_fixes_through_use_cases() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("7")
        await pilot.pause()
        telemetry = next(row for row in app.query(CheckRow) if row.check.name == "telemetry claude")
        assert str(telemetry.query_one(".fix-button", Button).label) == "Fix"
        await pilot.click(f"#{telemetry.id} .fix-button")
        await settle(app, pilot)
        assert [fix.action for fix in services.applied] == [FixAction.TELEMETRY_ON]
        assert services.applied[0].argument == "claude"
        assert "telemetry claude: claude: on" in notes(app)
        assert "doctor" in services.calls

    drive(make_app(services), scenario)


def test_health_run_checks_button_reloads() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("7")
        await settle(app, pilot)
        await pilot.click("#health-run")
        await settle(app, pilot)
        assert services.calls.count("doctor") == 1
        assert len(app.query_one(HealthView).query(CheckRow)) == 6

    drive(make_app(services), scenario)


def test_capsule_rendering_highlights_and_counts() -> None:
    lines = ((7, "KeyError: 'MX'"), (12, "ok keyerror again"))
    text, matches = render_lines(lines, lexer_for("pytest"), True, "keyerror")
    assert matches == 2
    assert text.plain.splitlines()[0].startswith(" 7  KeyError")
    assert lexer_for("jest") == "text"


@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("CUANTA_CLIPBOARD_TEST") != "1",
    reason="writes the real Windows clipboard; opt in with CUANTA_CLIPBOARD_TEST=1",
)
def test_windows_clipboard_round_trip() -> None:
    import ctypes

    from cuanta.adapters.system.clipboard import CF_UNICODETEXT, copy_native

    assert copy_native("cuanta ✓ clipboard")
    user = vars(ctypes)["WinDLL"]("user32")
    kernel = vars(ctypes)["WinDLL"]("kernel32")
    user.GetClipboardData.restype = ctypes.c_void_p
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalLock.argtypes = (ctypes.c_void_p,)
    kernel.GlobalUnlock.argtypes = (ctypes.c_void_p,)
    assert user.OpenClipboard(None)
    try:
        handle = user.GetClipboardData(CF_UNICODETEXT)
        pointer = kernel.GlobalLock(handle)
        value = ctypes.wstring_at(pointer)
        kernel.GlobalUnlock(handle)
    finally:
        user.CloseClipboard()
    assert value == "cuanta ✓ clipboard"
