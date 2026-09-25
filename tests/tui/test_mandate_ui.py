from __future__ import annotations

import time

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, Input, Static, TextArea

from cuanta.application.mandate_flow import MandateOptions
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.pipeline import CardState
from cuanta.tui.app import CuantaApp
from cuanta.tui.screens.attach import AttachScreen
from cuanta.tui.screens.pipeline import PipelineScreen
from cuanta.tui.screens.result import ResultScreen
from cuanta.tui.views.mandate import MandateView
from cuanta.tui.widgets.wizard import MandateWizard
from tests.tui.fakes import AGENTS, RED, FakeServices, pipeline_events, single_context_events
from tests.tui.test_app import at, drive, make_app, settle
from tests.tui.test_t5_screens import render, wait_for
from tests.tui.test_wizard import current, open_wizard, tell

BUG = "Fix the cart total: AssertionError: [total] expected 10 got 12. Don't touch payments."


def notes(app: CuantaApp) -> list[str]:
    return [str(note.message) for note in app._notifications]


async def launch_bug(app: CuantaApp, pilot: Pilot[None]) -> MandateWizard:
    wizard = await open_wizard(app, pilot)
    await tell(wizard, pilot, BUG)
    assert wizard.kind == "bug"
    wizard.query_one("#wiz-next", Button).press()
    await wait_for(pilot, lambda: current(wizard) == "team")
    await wait_for(pilot, lambda: bool(wizard.query("#override-senior")))
    wizard.query_one("#wiz-next", Button).press()
    return wizard


def test_the_story_fills_a_bug_request() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, BUG)
        request = wizard.request()
        assert request.type == "bug"
        assert "AssertionError: [total] expected 10 got 12" in request.why
        assert request.out_of_scope == "Don't touch payments"
        assert wizard.query_one("#wiz-evidence-actions").display

    drive(make_app(services), scenario, size=(120, 50))


def test_launch_is_blocked_without_out_of_scope() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, "Fix the cart total: AssertionError: expected 10 got 12")
        assert wizard.query_one("#wiz-out", Input).value == ""
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(
            pilot, lambda: "Required" in render(wizard.query_one("#wiz-out-error", Static))
        )
        assert current(wizard) == "confirm"
        assert services.requests == []

    drive(make_app(services), scenario, size=(120, 50))


def test_preview_shows_command_and_prompt() -> None:
    services = FakeServices(layout="one_page")

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        wizard.query_one("#wiz-story", TextArea).text = BUG
        wizard.query_one("#wiz-understand", Button).press()
        await wait_for(pilot, lambda: wizard.kind == "bug")
        wizard.query_one("#wiz-preview", Button).press()
        await wait_for(pilot, lambda: wizard.query_one("#preview-card").display)
        assert '"<prompt>"' in render(wizard.query_one("#preview-command", Static))
        assert "WHAT: Fix the cart total" in wizard.query_one("#preview-prompt", TextArea).text
        assert services.requests[0].out_of_scope == "Don't touch payments"

    drive(make_app(services), scenario, size=(120, 50))


def test_fake_engine_run_lights_four_cards_in_order() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await launch_bug(app, pilot)
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        screen = app.screen_stack[-2]
        assert isinstance(screen, PipelineScreen)
        result = app.screen
        assert isinstance(result, ResultScreen)
        assert result.view.run.id == "01JMANDATE0000000000000RUN1"
        await pilot.press("escape")
        await wait_for(pilot, lambda: app.screen is screen)
        assert screen.report is not None
        assert screen.query_one("#pipeline-result", Button).display
        assert screen.pipeline.order == [0, 1, 2, 3]
        assert [card.state for card in screen.pipeline.cards] == [CardState.DONE] * 4
        assert screen.query_one("#card-senior").has_class("-done")
        summary = render(screen.query_one("#pipeline-summary", Static))
        assert "Mandate finished" in summary
        assert "src/shop/cart.py" in summary
        assert "$0.42" in summary
        assert not screen.query_one("#pipeline-stop", Button).display
        await pilot.click("#pipeline-spectrum")
        await pilot.pause()
        assert await at(app, pilot, "spectrum")

    drive(make_app(services), scenario, size=(120, 50))


@pytest.mark.parametrize(
    ("language", "title"),
    [("en", "Analyst (single context)"), ("es", "Analista (contexto único)")],
)
def test_single_context_investigation_shows_one_localized_analyst_card(
    language: str, title: str
) -> None:
    services = FakeServices(events=single_context_events())

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        screen = PipelineScreen(
            services,
            app.catalog,
            MandateRequest(type="investigation", what="Check the cart", why="How does it work?"),
            0,
            MandateOptions(),
        )
        app.push_screen(screen)
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        await pilot.press("escape")
        await wait_for(pilot, lambda: app.screen is screen)
        assert [card.stage for card in screen.pipeline.cards] == ["analyst"]
        assert [card.state for card in screen.pipeline.cards] == [CardState.DONE]
        assert len(screen.query(".agent-card")) == 1
        assert title in render(screen.query_one("#card-analyst .card-title", Static))
        assert screen.pipeline.cards[0].tools == 1
        assert screen.pipeline.cards[0].tokens == 1100

    drive(make_app(services, language), scenario)


def test_delegated_investigation_shows_only_its_analyst_role() -> None:
    services = FakeServices(events=pipeline_events((AGENTS[0],)))

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        screen = PipelineScreen(
            services,
            app.catalog,
            MandateRequest(type="investigation", what="Check the cart", why="How does it work?"),
            0,
            MandateOptions(shape="pipeline"),
        )
        app.push_screen(screen)
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        await pilot.press("escape")
        await wait_for(pilot, lambda: app.screen is screen)
        assert [card.stage for card in screen.pipeline.cards] == ["analyst"]
        assert screen.pipeline.cards[0].agent == AGENTS[0]
        assert len(screen.query(".agent-card")) == 1
        assert "Analyst" in render(screen.query_one("#card-analyst .card-title", Static))
        assert "single context" not in render(screen.query_one("#card-analyst .card-title", Static))

    drive(make_app(services), scenario)


def test_stop_terminates_the_running_mandate() -> None:
    services = FakeServices(block=True)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await launch_bug(app, pilot)
        await wait_for(pilot, lambda: isinstance(app.screen, PipelineScreen))
        screen = app.screen
        assert isinstance(screen, PipelineScreen)
        await wait_for(pilot, lambda: screen.pipeline.order == [0])
        assert screen.pipeline.cards[0].state is CardState.ACTIVE
        assert screen.running
        started = time.monotonic()
        await pilot.click("#pipeline-stop")
        await wait_for(pilot, lambda: screen.report is not None)
        assert time.monotonic() - started < 5
        assert services.stops == 1
        await wait_for(
            pilot, lambda: "Stop requested; the engine process was terminated." in notes(app)
        )
        assert "Mandate did not finish cleanly" in render(
            screen.query_one("#pipeline-summary", Static)
        )
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        await pilot.press("escape")
        await wait_for(pilot, lambda: app.screen is screen)
        await pilot.press("escape")
        await wait_for(pilot, lambda: not isinstance(app.screen, PipelineScreen))

    drive(make_app(services), scenario)


def test_capsule_fix_prefills_the_wizard() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        app.mandate_prefill = RED.hairballs[0]
        await app.action_goto("mandates")
        await settle(app, pilot)
        view = app.query_one(MandateView)
        wizard = view.wizard
        await wait_for(pilot, lambda: current(wizard) == "confirm")
        assert wizard.kind == "bug"
        assert (
            wizard.query_one("#wiz-what", TextArea).text
            == "make cuanta test green by fixing sig-a1b2"
        )
        assert "[total]" in wizard.query_one("#wiz-why", TextArea).text
        assert wizard.query_one("#wiz-where", Input).value == "src/shop/cart.py:42"
        assert view.signatures == 1
        assert app.mandate_prefill is None

    drive(make_app(), scenario, size=(120, 50))


def test_use_last_failure_and_attach_file() -> None:
    services = FakeServices(evidence_files={"logs/ci.txt": "Traceback: boom"})

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, BUG)
        view = app.query_one(MandateView)
        wizard.query_one("#wiz-failure", Button).press()
        await wait_for(pilot, lambda: view.signatures == 2)
        assert "[total]" in wizard.query_one("#wiz-why", TextArea).text
        await wait_for(
            pilot, lambda: "Loaded 2 failing signatures from the last test run." in notes(app)
        )
        wizard.query_one("#wiz-attach", Button).press()
        await wait_for(pilot, lambda: isinstance(app.screen, AttachScreen))
        await pilot.press(*"logs/ci.txt", "enter")
        await wait_for(
            pilot, lambda: "Traceback: boom" in wizard.query_one("#wiz-why", TextArea).text
        )
        wizard.query_one("#wiz-attach", Button).press()
        await wait_for(pilot, lambda: isinstance(app.screen, AttachScreen))
        await pilot.press(*"missing.log", "enter")
        await wait_for(
            pilot, lambda: any(note.startswith("Could not read missing.log") for note in notes(app))
        )

    drive(make_app(services), scenario, size=(120, 50))


def test_no_engine_warns() -> None:
    services = FakeServices(engines=(("claude", False), ("codex", False), ("opencode", False)))

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("3")
        await settle(app, pilot)
        await wait_for(pilot, lambda: any("No engine is available" in note for note in notes(app)))

    drive(make_app(services), scenario)


def test_the_wizard_speaks_spanish() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        assert str(wizard.query_one("#wiz-next", Button).label) == "Entender"
        assert str(app.query_one("#layout-one_page", Button).label) == "Una página"
        assert "Cuéntame qué necesitas" in render(
            wizard.query_one("#step-tell .card-title", Static)
        )

    drive(make_app(language="es"), scenario, size=(120, 50))
