from __future__ import annotations

from textual.pilot import Pilot
from textual.widgets import Button, Checkbox, Static

from cuanta.tui.app import CuantaApp
from cuanta.tui.screens.confirm import ConfirmScreen
from cuanta.tui.screens.result import ResultScreen
from tests.tui.fakes import (
    SANDBOX_RUN,
    FakeServices,
    sandbox_handoff,
    sandbox_result,
    sandbox_trial,
)
from tests.tui.test_app import drive, make_app, settle
from tests.tui.test_t5_screens import render, wait_for
from tests.tui.test_wizard import STORY, current, launched, open_wizard, tell


def _services(**changes: object) -> FakeServices:
    services = FakeServices(
        results={SANDBOX_RUN: sandbox_result()}, handoffs={SANDBOX_RUN: sandbox_handoff()}
    )
    for name, value in changes.items():
        setattr(services, name, value)
    return services


async def _open(app: CuantaApp, pilot: Pilot[None], view_run: str = SANDBOX_RUN) -> ResultScreen:
    view = app.services.result_view(view_run)
    assert view is not None
    app.push_screen(ResultScreen(app.services, app.catalog, view))
    await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
    screen = app.screen
    assert isinstance(screen, ResultScreen)
    await settle(app, pilot)
    await wait_for(pilot, lambda: screen.query_one("#result-trial-handoff").display)
    return screen


def test_the_team_step_switch_launches_in_an_isolated_copy() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, STORY)
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: current(wizard) == "team")
        note = wizard.query_one("#wiz-sandbox-note", Static)
        assert not note.display
        assert not wizard.options().sandbox
        wizard.query_one("#wiz-sandbox", Checkbox).value = True
        await wait_for(pilot, lambda: wizard.sandbox and bool(note.display))
        assert "apply or discard" in render(note)
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: launched(app) is not None)
        screen = launched(app)
        assert screen is not None and screen.options.sandbox
        assert not screen.options.temporary_copy

    drive(make_app(services), scenario, size=(120, 50))


def test_result_shows_the_trial_and_applies_after_confirmation() -> None:
    services = _services()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        screen = await _open(app, pilot)
        line = render(screen.query_one("#result-trial-line", Static))
        assert "Isolated copy" in line and "3 files, +33 −1" in line
        assert "waiting for your decision" in line
        handoff = render(screen.query_one("#result-trial-handoff", Static))
        assert "Suggested commit: feat(seo): add sitemap and robots routes" in handoff
        assert "Suggested branch: feat/add-sitemap-and-robots-routes-0run" in handoff
        assert not screen.query_one("#result-trial-notes").display
        screen.query_one("#result-apply", Button).press()
        await wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        details = render(app.screen.query_one("#confirm-details", Static))
        assert "added  src/app/sitemap.ts" in details
        assert "3 files will be written" in render(app.screen.query_one("#confirm-body", Static))
        app.screen.query_one("#confirm-ok", Button).press()
        await wait_for(pilot, lambda: services.applied_trials == [SANDBOX_RUN])
        await wait_for(pilot, lambda: not screen.query_one("#result-apply").display)
        assert not screen.query_one("#result-discard").display
        assert screen.query_one("#result-copy-commands").display
        assert "applied on 2026-09-26 10:00" in render(
            screen.query_one("#result-trial-line", Static)
        )

    drive(make_app(services), scenario, size=(120, 44))


def test_cancelling_the_summary_writes_nothing() -> None:
    services = _services()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        screen = await _open(app, pilot)
        screen.query_one("#result-apply", Button).press()
        await wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        app.screen.query_one("#confirm-cancel", Button).press()
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        await settle(app, pilot)
        assert services.applied_trials == []

    drive(make_app(services), scenario, size=(120, 44))


def test_discard_and_copy_commands() -> None:
    services = _services()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        screen = await _open(app, pilot)
        screen.query_one("#result-copy-commands", Button).press()
        await wait_for(pilot, lambda: bool(services.copied))
        assert services.copied[-1] == sandbox_handoff().chained
        assert (
            " && git switch -c feat/add-sitemap-and-robots-routes-0run && " in (services.copied[-1])
        )
        assert app.clipboard == services.copied[-1]
        screen.query_one("#result-discard", Button).press()
        await wait_for(pilot, lambda: services.discarded_trials == [SANDBOX_RUN])
        await wait_for(
            pilot, lambda: "discarded on" in render(screen.query_one("#result-trial-line", Static))
        )
        assert not screen.query_one("#result-trial-actions").display

    drive(make_app(services), scenario, size=(120, 44))


def test_drift_dependencies_and_breaches_are_explained_and_block_apply() -> None:
    drifted = sandbox_result(drift=("src/app/layout.tsx",), trial=sandbox_trial(dependencies=3))
    breach = sandbox_result(trial=sandbox_trial(task_type="investigation", kept=True))
    moved = sandbox_result(drift=("src/app/layout.tsx",))
    services = FakeServices(
        results={SANDBOX_RUN: drifted, "01JBREACH": breach, "01JMOVED": moved},
        handoffs={SANDBOX_RUN: sandbox_handoff(("CLAUDE.md",))},
    )

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        view = app.services.result_view(SANDBOX_RUN)
        assert view is not None
        app.push_screen(ResultScreen(app.services, app.catalog, view))
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        screen = app.screen
        await settle(app, pilot)
        notes = render(screen.query_one("#result-trial-notes", Static))
        assert "node_modules in the project changed during the run (3 files)" in notes
        assert "npm ci" in notes
        assert "Changed in the project since the copy: src/app/layout.tsx" in notes
        assert not screen.query_one("#result-apply", Button).display
        assert not screen.query_one("#result-copy-commands", Button).display
        app.pop_screen()
        drift_only = app.services.result_view("01JMOVED")
        assert drift_only is not None
        app.push_screen(ResultScreen(app.services, app.catalog, drift_only))
        await wait_for(pilot, lambda: app.screen is not screen)
        drift_screen = app.screen
        await settle(app, pilot)
        assert drift_screen.query_one("#result-apply", Button).disabled
        handoff = drift_screen.query_one("#result-trial-handoff", Static)
        await wait_for(pilot, lambda: "uncommitted" in render(handoff))
        assert "CLAUDE.md" in render(handoff)
        app.pop_screen()
        other = app.services.result_view("01JBREACH")
        assert other is not None
        app.push_screen(ResultScreen(app.services, app.catalog, other))
        await wait_for(pilot, lambda: app.screen is not screen)
        await settle(app, pilot)
        notes = render(app.screen.query_one("#result-trial-notes", Static))
        assert "The investigation changed files in the copy" in notes
        assert "The copy is kept at /tmp/cuanta-sandbox/shop-1a2b3c4d/shop" in notes
        assert not app.screen.query_one("#result-apply", Button).display
        assert not app.screen.query_one("#result-copy-commands", Button).display

    drive(make_app(services), scenario, size=(120, 44))


def test_apply_failures_are_reported_and_the_trial_stays_pending() -> None:
    services = _services(trial_error="refusing to apply: 1 files changed since the copy")

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        screen = await _open(app, pilot)
        screen.apply_answer(True)
        await wait_for(
            pilot,
            lambda: any("refusing to apply" in str(item.message) for item in app._notifications),
        )
        assert screen.query_one("#result-trial-actions").display

    drive(make_app(services), scenario, size=(120, 44))


def test_the_sandbox_result_speaks_spanish() -> None:
    services = _services()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        screen = await _open(app, pilot)
        assert "Copia aislada" in render(screen.query_one("#result-trial-line", Static))
        assert "Rama sugerida" in render(screen.query_one("#result-trial-handoff", Static))
        assert str(screen.query_one("#result-apply", Button).label) == "Aplicar cambios"
        assert str(screen.query_one("#result-discard", Button).label) == "Descartar"
        assert str(screen.query_one("#result-copy-commands", Button).label) == "Copiar comandos"

    drive(make_app(services, "es"), scenario, size=(120, 44))
