from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, ContentSwitcher, DataTable, Select, Static
from textual.worker import WorkerCancelled

from cuanta.application.doctor import result
from cuanta.domain.cache import PrefixState, PrefixWindow
from cuanta.domain.ledger import Run
from cuanta.domain.messages import msg
from cuanta.domain.progress import Status
from cuanta.domain.terminal import TerminalKind, TerminalReport
from cuanta.domain.voice import Mood
from cuanta.tui.app import CuantaApp
from cuanta.tui.widgets.header import ListenerChip
from cuanta.tui.widgets.michi import Michi
from tests.tui.fakes import FakeServices, snapshot

MODERN = {"WT_SESSION": "1"}
Scenario = Callable[[CuantaApp, Pilot[None]], Awaitable[None]]


def make_app(services: FakeServices | None = None, language: str = "en") -> CuantaApp:
    return CuantaApp(
        services or FakeServices(), language, "calico-dark", motion=False, environ=MODERN
    )


SETTLE_BUDGET_S = 10.0


def settled(app: CuantaApp) -> bool:
    classes = set(app.screen.classes) | set(app.screen_stack[0].classes)
    return app.home_loaded and bool(classes & {"-short", "-tall"})


async def settle(app: CuantaApp, pilot: Pilot[None]) -> None:
    deadline = time.monotonic() + SETTLE_BUDGET_S
    await pilot.pause(0.01)
    while not settled(app):
        if time.monotonic() > deadline:
            raise AssertionError(
                f"app did not settle within {SETTLE_BUDGET_S}s: "
                f"home_loaded={app.home_loaded}, screen classes={sorted(app.screen.classes)}"
            )
        await pilot.pause(0.01)
    for _ in range(3):
        with suppress(WorkerCancelled):
            await app.workers.wait_for_complete()
        await pilot.wait_for_scheduled_animations()
        await pilot.pause(0.05)


def drive(app: CuantaApp, scenario: Scenario, size: tuple[int, int] = (120, 36)) -> None:
    async def main() -> None:
        async with app.run_test(size=size) as pilot:
            await settle(app, pilot)
            await scenario(app, pilot)
            await settle(app, pilot)

    asyncio.run(main())


def test_driver_finishes_pending_widget_mounts_before_shutdown() -> None:
    completed: list[str] = []

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        async def mount() -> None:
            await asyncio.sleep(0)
            selector = Select([("Ready", "ready")], allow_blank=False)
            await app.base.mount(selector)
            completed.append(str(selector.query_one("#label", Static).render()))

        app.run_worker(mount())

    drive(make_app(), scenario)
    assert completed == ["Ready"]


def text_of(app: CuantaApp, selector: str) -> str:
    return str(app.query_one(selector, Static).render())


def current(app: CuantaApp) -> str | None:
    return app.base.query_one("#main", ContentSwitcher).current


async def at(app: CuantaApp, pilot: Pilot[None], section: str) -> bool:
    for _ in range(200):
        if current(app) == section:
            await pilot.pause()
            return True
        await pilot.pause(0.02)
    return False


def test_home_shows_project_runs_and_next_step() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        facts = text_of(app, "#project-facts")
        assert "shop" in facts
        assert "pytest + mypy" in facts
        assert "npm install -g @openai/codex" in text_of(app, "#next-fix")
        assert app.query_one("#runs", DataTable).row_count == 3
        assert "4.3M tokens this week" in text_of(app, "#week-total")
        assert "claude" in text_of(app, "#engine-chips")
        assert app.query_one("#home-michi", Michi).mood is Mood.WATCHING

    drive(make_app(), scenario)


def test_sidebar_click_and_number_keys_switch_sections() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.click("#nav-spectrum")
        await pilot.pause()
        assert await at(app, pilot, "spectrum")
        assert app.query_one("#nav-spectrum").has_class("-active")
        await pilot.press("7")
        assert await at(app, pilot, "health")
        await pilot.press("1")
        assert await at(app, pilot, "home")
        app.query_one("#nav-ledger").focus()
        await pilot.press("enter")
        assert await at(app, pilot, "ledger")

    drive(make_app(), scenario)


def test_quick_actions_and_letter_shortcuts() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.click("#action-tests")
        assert await at(app, pilot, "tests")
        await pilot.press("1", "i")
        assert await at(app, pilot, "init")
        await pilot.press("s")
        assert await at(app, pilot, "spectrum")

    drive(make_app(), scenario)


def test_next_step_button_copies_external_fix() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        assert str(app.query_one("#next-run", Button).label) == "Copy"
        await pilot.click("#next-run")
        assert services.copied == ["npm install -g @openai/codex"]
        assert await at(app, pilot, "home")

    drive(make_app(services), scenario)


def test_fresh_project_invites_initialize() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        assert "Let's set up this project" in text_of(app, "#next-title")
        assert str(app.query_one("#next-run", Button).label) == "Initialize project"
        assert app.query_one("#home-michi", Michi).mood is Mood.SLEEPY
        await pilot.click("#next-run")
        assert await at(app, pilot, "init")

    drive(make_app(FakeServices(snapshot(initialized=False, runs=()))), scenario)


def test_empty_ledger_invites_an_action() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        assert app.query_one("#no-runs", Static).display
        assert not app.query_one("#runs", DataTable).display

    drive(make_app(FakeServices(snapshot(runs=()))), scenario)


def test_palette_runs_a_command() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("ctrl+p")
        await pilot.pause()
        await pilot.press(*"open spectrum")
        await pilot.pause(0.3)
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert await at(app, pilot, "spectrum")

    drive(make_app(), scenario)


def test_reload_and_load_failure_toast() -> None:
    services = FakeServices(fail="ledger is locked")

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        assert any("ledger is locked" in str(note.message) for note in app._notifications)
        services.fail = ""
        await pilot.press("r")
        await settle(app, pilot)
        assert "shop" in text_of(app, "#project-facts")
        assert services.calls == ["home", "home"]

    drive(make_app(services), scenario)


def test_spanish_catalog_drives_the_ui() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        assert "Inicio" in str(app.query_one("#nav-home").query_one(".nav-label", Static).render())
        assert str(app.query_one("#action-tests", Button).label) == "Ejecutar pruebas"

    drive(make_app(language="es"), scenario)


def test_layout_breakpoints() -> None:
    sizes = {(120, 36): {"-full", "-tall"}, (80, 24): {"-icons", "-short"}, (70, 24): {"-single"}}
    for size, expected in sizes.items():

        async def scenario(app: CuantaApp, pilot: Pilot[None], wanted: set[str] = expected) -> None:
            assert wanted <= set(app.screen.classes)

        drive(make_app(), scenario, size)


def test_legacy_console_uses_ansi_and_offers_a_dismissable_tip() -> None:
    legacy = TerminalReport(
        TerminalKind.LEGACY, msg("terminal.conhost", window="ConsoleWindowClass")
    )
    services = FakeServices()
    app = CuantaApp(services, "en", "auto", motion=False, environ={}, terminal=legacy)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        assert app.theme == "ansi-dark"
        assert "Windows Terminal" in text_of(app, "#legacy-tip-text")
        app.query_one("#legacy-tip-forever", Button).press()
        await settle(app, pilot)
        assert not app.query("#legacy-tip")
        assert services.saved == {"terminal.tip_dismissed": True}

    drive(app, scenario)


def test_dismissed_tip_stays_hidden_and_modern_terminal_keeps_calico() -> None:
    legacy = TerminalReport(TerminalKind.LEGACY, msg("terminal.vt_off"))
    quiet = CuantaApp(
        FakeServices(tip_dismissed=True), "en", "auto", motion=False, environ={}, terminal=legacy
    )

    async def dismissed(app: CuantaApp, pilot: Pilot[None]) -> None:
        assert not app.query("#legacy-tip")

    drive(quiet, dismissed)
    modern = TerminalReport(
        TerminalKind.MODERN, msg("terminal.conpty", window="PseudoConsoleWindow")
    )
    app = CuantaApp(FakeServices(), "en", "auto", motion=False, environ={}, terminal=modern)

    async def calico(app: CuantaApp, pilot: Pilot[None]) -> None:
        assert app.theme == "calico-dark"
        assert not app.query("#legacy-tip")

    drive(app, calico)


def test_terminal_background_is_transparent() -> None:
    app = make_app(FakeServices(background="terminal"))

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        assert app.has_class("-transparent")
        assert app.ansi_color
        assert app.screen.styles.background.ansi == -1

    drive(app, scenario)


@pytest.mark.perf
def test_first_paint_is_fast() -> None:
    timings: list[float] = []

    async def main() -> None:
        started = time.perf_counter()
        app = make_app()
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            timings.append(time.perf_counter() - started)

    asyncio.run(main())
    assert timings[0] < float(os.environ.get("CUANTA_PAINT_BUDGET_S", "0.7"))


def test_michi_blinks_when_motion_is_on() -> None:
    app = CuantaApp(FakeServices(), "en", "calico-dark", motion=True, environ=MODERN)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        michi = app.query_one("#home-michi", Michi)
        michi.blink()
        assert michi.blinking
        await pilot.pause(0.3)
        assert not michi.blinking

    drive(app, scenario)


def test_quitting_right_after_launch_is_clean() -> None:
    async def main() -> None:
        for size in ((120, 36), (80, 24)):
            app = make_app()
            async with app.run_test(size=size) as pilot:
                await pilot.pause()
                await pilot.press("4", "5", "3")
            assert app.closing

    asyncio.run(main())


def test_shutdown_marks_closing_before_mount_wait() -> None:
    async def main() -> None:
        app = make_app()
        async with app._mounting:
            shutdown = asyncio.create_task(app._shutdown())
            await asyncio.sleep(0)
            assert app.closing
            shutdown.cancel()
        with suppress(asyncio.CancelledError):
            await shutdown

    asyncio.run(main())


def test_closing_drops_navigation_keys() -> None:
    async def main() -> None:
        app = make_app()
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            app.closing = True
            with patch.object(app, "action_goto") as goto:
                await pilot.press("4", "5", "3")
                goto.assert_not_called()

    asyncio.run(main())


def test_an_unpriced_run_shows_na_not_zero_dollars() -> None:
    unpriced = Run("01JABCDEF0000000000000CODX1", "mandate", "codex", status="ok", cost_usd=None)
    for language, shown in (("en", "n/a"), ("es", "n/d")):
        services = FakeServices(home_snapshot=snapshot(runs=(unpriced,)))

        async def scenario(app: CuantaApp, pilot: Pilot[None], shown: str = shown) -> None:
            await settle(app, pilot)
            row = app.query_one("#runs", DataTable).get_row(unpriced.id)
            cost = str(row[3])
            assert cost == shown
            assert "$0.00" not in cost

        drive(make_app(services, language), scenario)


def test_home_shows_the_measured_prefix_window_or_unknown() -> None:
    until = datetime(2026, 1, 5, 10, 10, tzinfo=UTC)
    for language, prefix, expected in (
        (
            "en",
            PrefixWindow(PrefixState.WARM, until),
            "last observed Claude prefix warm until",
        ),
        ("es", PrefixWindow(PrefixState.UNKNOWN), "último prefijo observado: desconocido"),
    ):
        services = FakeServices(home_snapshot=replace(snapshot(), prefix=prefix))

        async def scenario(app: CuantaApp, pilot: Pilot[None], expected: str = expected) -> None:
            await settle(app, pilot)
            assert expected in str(app.query_one("#home-prefix", Static).render())

        drive(make_app(services, language), scenario)


def test_home_primary_action_follows_the_forge_state() -> None:
    for initialized, primary, init_label in (
        (True, "action-mandates", "Refresh knowledge"),
        (False, "action-init", "Initialize project"),
    ):
        services = FakeServices(home_snapshot=snapshot(initialized=initialized))

        async def scenario(
            app: CuantaApp, pilot: Pilot[None], primary: str = primary, label: str = init_label
        ) -> None:
            await settle(app, pilot)
            buttons = list(app.query_one("#actions").query(Button))
            assert buttons[0].id == primary
            assert buttons[0].variant == "primary"
            assert [button.id for button in buttons if button.variant == "primary"] == [primary]
            assert str(app.query_one("#action-init", Button).label) == label

        drive(make_app(services), scenario)


WIRED = (
    result(
        "telemetry claude", Status.OK, msg("wiring.env_block", endpoint="http://127.0.0.1:4318")
    ),
    result("listener", Status.WARN, msg("doctor.listener.off"), "cuanta listen --background"),
)


def test_listener_is_never_the_next_step() -> None:
    home = snapshot(checks=WIRED)
    assert home.next_step is None or home.next_step.name != "listener"


def test_auto_listener_starts_when_telemetry_is_wired_and_owns_it() -> None:
    services = FakeServices(home_snapshot=snapshot(checks=WIRED))

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await settle(app, pilot)
        for _ in range(100):
            if services.listener_calls:
                break
            await pilot.pause(0.02)
        assert services.listener_calls == ["start"]
        assert app.listener_owned

    drive(make_app(services), scenario)


def test_manual_listener_stays_off_and_the_chip_toggles_it() -> None:
    services = FakeServices(home_snapshot=snapshot(checks=WIRED), listener_mode="manual")

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await settle(app, pilot)
        assert services.listener_calls == []
        chip = app.query_one("#listener-chip", ListenerChip)
        assert not chip.running
        chip.post_message(ListenerChip.Toggled(False))
        for _ in range(100):
            if services.listener_calls:
                break
            await pilot.pause(0.02)
        assert services.listener_calls == ["start"]
        assert not app.listener_owned

    drive(make_app(services), scenario)


def test_no_view_mounts_after_shutdown_starts() -> None:
    async def main() -> None:
        app = make_app()
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            app.closing = True
            await app.ensure_view("ledger")
            assert not app.base.query("#ledger")
            app.closing = False
            await app.ensure_view("ledger")
            assert app.base.query("#ledger")

    asyncio.run(main())
