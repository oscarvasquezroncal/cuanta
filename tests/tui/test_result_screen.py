from __future__ import annotations

from dataclasses import replace

from textual.pilot import Pilot
from textual.widgets import Button, DataTable, Input, Markdown, Static, TextArea

from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.overhead import session_overhead
from cuanta.tui.app import CuantaApp
from cuanta.tui.screens.result import ResultScreen, parse_file_link
from cuanta.tui.screens.run_file import RunFileScreen
from cuanta.tui.views.ledger import LedgerView
from tests.tui.fakes import FakeServices, sample_result
from tests.tui.test_app import drive, make_app, settle
from tests.tui.test_mandate_ui import launch_bug
from tests.tui.test_t5_screens import render, wait_for
from tests.tui.test_wizard import STORY, current, open_wizard, tell

RUN_ID = "01JMANDATE0000000000000RUN1"


def test_file_links_parse() -> None:
    assert parse_file_link("cuanta-file:src/app/page.tsx:12") == ("src/app/page.tsx", 12)
    assert parse_file_link("https://example.com") is None
    assert parse_file_link("cuanta-file:src/app/page.tsx:x") is None


def test_result_shows_jev_fallback_reason() -> None:
    view = replace(sample_result(), fallback_error="jev answered HTTP 503", fallback_from="jev")

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        app.push_screen(ResultScreen(app.services, app.catalog, view))
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        screen = app.screen
        assert isinstance(screen, ResultScreen)
        await settle(app, pilot)
        visible = render(screen.query_one("#result-instinct-fallback", Static))
        assert "Jev failed: jev answered HTTP 503; heuristic answered" in visible

    drive(make_app(), scenario, size=(120, 40))


def test_result_header_names_the_run_shape() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        for view, expected in (
            (sample_result(), "single context"),
            (replace(sample_result(), single=False), "pipeline"),
            (sample_result(simple=True), "simple mode"),
            (replace(sample_result(), shape_known=False), "unknown shape"),
        ):
            app.push_screen(ResultScreen(app.services, app.catalog, view))
            await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
            await settle(app, pilot)
            assert expected in render(app.screen.query_one("#result-facts", Static))
            app.pop_screen()
            await pilot.pause()

    drive(make_app(), scenario, size=(120, 40))


def test_result_shows_first_request_cache_state() -> None:
    overhead = session_overhead(
        [
            LedgerEvent(
                kind="api_request",
                ts="2026-01-05T10:00:00Z",
                input_tokens=4_000,
                cache_read_tokens=20_000,
                cache_write_tokens=3_000,
            )
        ],
        0,
    )

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        for view, expected in (
            (replace(sample_result(), overhead=overhead), "warm cache · 20,000 tokens read (74%)"),
            (sample_result(), "cache unknown"),
        ):
            app.push_screen(ResultScreen(app.services, app.catalog, view))
            await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
            await settle(app, pilot)
            assert expected in render(app.screen.query_one("#result-cache", Static))
            app.pop_screen()
            await pilot.pause()

    drive(make_app(), scenario, size=(120, 40))


def test_result_header_shape_in_spanish() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        app.push_screen(ResultScreen(app.services, app.catalog, sample_result()))
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        await settle(app, pilot)
        facts = render(app.screen.query_one("#result-facts", Static))
        assert "contexto único" in facts
        assert "pipeline" not in facts

    drive(make_app(language="es"), scenario, size=(120, 40))


def test_result_shows_turns_and_marks_a_turn_limit_cut() -> None:
    normal = sample_result()
    stopped = replace(
        normal,
        run=replace(
            normal.run, max_turns=40, turns=40, end_reason="error_max_turns", status="failed"
        ),
    )

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        app.push_screen(ResultScreen(app.services, app.catalog, stopped))
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        await settle(app, pilot)
        facts = render(app.screen.query_one("#result-facts", Static))
        assert "turns 40/40" in facts
        assert "cut by turn limit" in render(app.screen.query_one("#result-turns-cut", Static))

    drive(make_app(), scenario, size=(120, 40))


async def run_to_result(app: CuantaApp, pilot: Pilot[None]) -> ResultScreen:
    await launch_bug(app, pilot)
    await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen), attempts=400)
    screen = app.screen
    assert isinstance(screen, ResultScreen)
    await settle(app, pilot)
    return screen


def test_a_finished_run_opens_its_result_and_saves_it_to_docs() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        screen = await run_to_result(app, pilot)
        facts = render(screen.query_one("#result-facts", Static))
        assert "Investigate" in facts
        assert "$0.14" in facts
        assert "17 s" in facts
        split = render(screen.query_one("#result-split", Static))
        assert "fixed session context ≈ 51,697 (97%)" in split
        assert screen.query_one("#result-save", Button).variant == "primary"
        assert screen.query(Markdown)
        screen.query_one("#result-save", Button).press()
        await wait_for(pilot, lambda: services.saved_results == [RUN_ID])
        await settle(app, pilot)
        assert any("docs/investigations/" in note for note in notes(app))
        screen.query_one("#result-export", Button).press()
        await wait_for(pilot, lambda: services.exported_results == [RUN_ID])

    drive(make_app(services), scenario, size=(120, 40))


def notes(app: CuantaApp) -> list[str]:
    return [str(notification.message) for notification in app._notifications]


def test_continue_prefills_the_next_mandate_from_the_report() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        app.open_result(RUN_ID)
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        screen = app.screen
        assert isinstance(screen, ResultScreen)
        await settle(app, pilot)
        screen.query_one("#result-continue", Button).press()
        await wait_for(pilot, lambda: not isinstance(app.screen, ResultScreen))
        await settle(app, pilot)
        assert app.section == "mandates"
        assert wizard.kind == "refactor"
        await wait_for(pilot, lambda: current(wizard) == "confirm")
        assert (
            wizard.query_one("#wiz-what", TextArea).text == "Load the fonts once, from layout.tsx"
        )
        assert wizard.query_one("#wiz-where", Input).value == "src/app/layout.tsx"
        assert wizard.query_one("#wiz-out", Input).value == "the page content"

    drive(make_app(services), scenario, size=(120, 40))


def test_changed_files_open_their_diff() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        app.push_screen(ResultScreen(app.services, app.catalog, sample_result()))
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        screen = app.screen
        assert isinstance(screen, ResultScreen)
        await settle(app, pilot)
        assert screen.query_one("#result-files", DataTable).row_count == 1
        screen.open_file("src/app/page.tsx", 0)
        await wait_for(pilot, lambda: isinstance(app.screen, RunFileScreen))
        await settle(app, pilot)
        body = render(app.screen.query_one("#run-file-body", Static))
        assert "-export const hero = 1;" in body
        assert "+export const hero = 2;" in body
        await pilot.press("escape")
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))

    drive(make_app(), scenario, size=(120, 40))


def test_the_ledger_opens_a_run_result() -> None:
    services = FakeServices()
    services.results["01JABCDEF0000000000000MAND1"] = sample_result("01JABCDEF0000000000000MAND1")

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("5")
        await settle(app, pilot)
        view = app.query_one(LedgerView)
        table = view.query_one("#ledger-runs", DataTable)
        table.focus()
        await wait_for(pilot, lambda: table.row_count > 0)
        mandate = next(run for run in view.shown if run.id == "01JABCDEF0000000000000MAND1")
        view.show_details(mandate)
        await pilot.pause()
        assert view.selected == "01JABCDEF0000000000000MAND1"
        view.query_one("#ledger-result", Button).press()
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        screen = app.screen
        assert isinstance(screen, ResultScreen)
        assert screen.view.run.id == "01JABCDEF0000000000000MAND1"
        await settle(app, pilot)

    drive(make_app(services), scenario, size=(120, 40))


def test_the_wizard_offers_init_or_simple_mode_without_forge() -> None:
    services = FakeServices(forge_ready=False)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await settle(app, pilot)
        assert wizard.query_one("#forge-gate").display
        assert "usually $1.59" in str(wizard.query_one("#wiz-init", Button).label)
        wizard.query_one("#wiz-story", TextArea).text = STORY
        wizard.query_one("#wiz-next", Button).press()
        await pilot.pause()
        assert current(wizard) == "tell"
        assert "Choose a path first" in render(wizard.query_one("#wiz-error", Static))
        wizard.query_one("#wiz-simple", Button).press()
        await pilot.pause()
        assert not wizard.query_one("#forge-gate").display
        assert "Simple mode" in render(wizard.query_one("#wiz-mode", Static))
        await tell(wizard, pilot, STORY)
        assert wizard.options().simple
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: current(wizard) == "team")
        cards = wizard.query_one("#team-cards")
        await wait_for(
            pilot, lambda: "Simple mode" in " ".join(render(item) for item in cards.query(Static))
        )

    drive(make_app(services), scenario, size=(120, 50))


def test_the_init_path_goes_to_initialize() -> None:
    services = FakeServices(forge_ready=False)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await settle(app, pilot)
        wizard.query_one("#wiz-init", Button).press()
        await wait_for(pilot, lambda: app.section == "init")

    drive(make_app(services), scenario, size=(120, 50))


def test_the_app_can_open_straight_on_a_run_result() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        screen = app.screen
        assert isinstance(screen, ResultScreen)
        assert screen.view.run.id == RUN_ID
        await settle(app, pilot)

    app = CuantaApp(FakeServices(), "en", "calico-dark", motion=False, open_run=RUN_ID)
    drive(app, scenario)
