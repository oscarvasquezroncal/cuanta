from __future__ import annotations

import json
from dataclasses import replace

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, Checkbox, Input, Select, Static, TextArea

from cuanta.application.assistant import sent_payload
from cuanta.application.intake import Understanding
from cuanta.tui.app import CuantaApp
from cuanta.tui.screens.confirm import ConfirmScreen
from cuanta.tui.screens.pipeline import PipelineScreen
from cuanta.tui.views.mandate import MandateView
from cuanta.tui.widgets.wizard import IntentCard, MandateWizard
from tests.tui.fakes import FakeServices
from tests.tui.test_app import drive, make_app, settle
from tests.tui.test_t5_screens import render, wait_for

STORY = (
    "Quiero entender para qué es esta landing, cómo funciona el carrito y si el hero afecta "
    "el rendimiento. No cambies nada."
)


async def open_wizard(app: CuantaApp, pilot: Pilot[None]) -> MandateWizard:
    await pilot.press("3")
    await settle(app, pilot)
    view = app.query_one(MandateView)
    wizard = view.query_one(MandateWizard)
    await wait_for(pilot, lambda: wizard.display and wizard.engine != "")
    return wizard


def current(wizard: MandateWizard) -> str:
    return ("tell", "confirm", "team")[wizard.step]


def launched(app: CuantaApp) -> PipelineScreen | None:
    return next((item for item in app.screen_stack if isinstance(item, PipelineScreen)), None)


async def tell(wizard: MandateWizard, pilot: Pilot[None], story: str) -> None:
    wizard.query_one("#wiz-story", TextArea).text = story
    wizard.query_one("#wiz-next", Button).press()
    await wait_for(pilot, lambda: wizard.understanding is not None and current(wizard) == "confirm")


def test_the_wizard_walks_three_steps_and_launches() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        wizard.query_one("#wiz-next", Button).press()
        error = wizard.query_one("#wiz-error", Static)
        await wait_for(pilot, lambda: "Write what you need first" in render(error))
        await tell(wizard, pilot, STORY)
        assert wizard.kind == "investigation"
        assert services.understood == [STORY]
        why = wizard.query_one("#wiz-why", TextArea).text
        assert "¿Para qué es esta landing?" in why
        assert "¿Cómo funciona el carrito?" in why
        assert "¿El hero afecta el rendimiento?" in why
        assert wizard.query_one("#wiz-out", Input).value == "No cambies nada"
        assert "read-only" in render(wizard.query_one("#wiz-detected", Static))
        assert wizard.query("#place-0")
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: current(wizard) == "team")
        await wait_for(pilot, lambda: bool(wizard.query("#override-analyst")))
        assert not wizard.query("#override-senior")
        assert not wizard.query("#override-orchestrator")
        estimate = wizard.query_one("#wiz-estimate", Static)
        await wait_for(pilot, lambda: "Based on 1 similar run: $0.55" in render(estimate))
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: launched(app) is not None)
        screen = launched(app)
        assert screen is not None
        assert screen.request.type == "investigation"
        assert screen.options.depth == "normal"
        await wait_for(pilot, lambda: services.last == STORY)
        assert wizard.step == 0
        assert wizard.query_one("#wiz-story", TextArea).text == ""
        assert wizard.understanding is None
        assert wizard.kind == ""
        assert wizard.query_one("#wiz-reuse").display

    drive(make_app(services), scenario, size=(120, 50))


def test_reuse_last_request_after_a_launch() -> None:
    services = FakeServices(last="Fix the cart total: TypeError: price is undefined")

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await wait_for(pilot, lambda: wizard.query_one("#wiz-reuse").display)
        wizard.query_one("#wiz-reuse", Button).press()
        await pilot.pause()
        assert wizard.story == services.last

    drive(make_app(services), scenario, size=(120, 50))


def test_unlaunched_stories_are_autosaved_and_listed_as_drafts() -> None:
    services = FakeServices(stories={"dold": "Explain how the checkout computes taxes"})

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await wait_for(pilot, lambda: bool(wizard.query("#draft-dold")))
        wizard.query_one("#wiz-story", TextArea).text = "Add a dark mode toggle"
        await wait_for(pilot, lambda: wizard.draft_id in services.stories, attempts=400)
        assert services.stories[wizard.draft_id] == "Add a dark mode toggle"
        wizard.query_one("#draft-dold", Button).press()
        await wait_for(pilot, lambda: wizard.story == "Explain how the checkout computes taxes")
        assert wizard.draft_id == "dold"

    drive(make_app(services), scenario, size=(120, 50))


def test_a_low_confidence_type_must_be_confirmed() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, "The landing page, the hero section and the footer.")
        understanding = wizard.understanding
        assert understanding is not None
        assert understanding.needs_confirm
        assert wizard.kind == ""
        detected = render(wizard.query_one("#wiz-detected", Static))
        assert "pick the type below" in detected
        wizard.query_one("#wiz-out", Input).value = "the footer"
        wizard.query_one("#wiz-next", Button).press()
        error = wizard.query_one("#wiz-error", Static)
        await wait_for(pilot, lambda: "What do you want to do?" in render(error))
        assert current(wizard) == "confirm"
        wizard.query_one("#intent-feature", IntentCard).post_message(IntentCard.Chosen("feature"))
        await wait_for(pilot, lambda: wizard.kind == "feature")
        wizard.query_one("#wiz-why", TextArea).text = "The landing and hero remain visible"
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: current(wizard) == "team")
        await wait_for(pilot, lambda: bool(wizard.query("#override-senior")))

    drive(make_app(services), scenario, size=(120, 50))


def test_missing_chips_answer_per_type() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, STORY)
        await wait_for(pilot, lambda: bool(wizard.query("#answer-deliverable-diagram")))
        wizard.query_one("#answer-deliverable-diagram", Button).press()
        await wait_for(pilot, lambda: not wizard.query("#answer-deliverable-diagram"))
        assert wizard.query_one("#wiz-deliverable", Select).value == "diagram"
        assert wizard.request().tests.startswith("Deliverable: a diagram")

    drive(make_app(services), scenario, size=(120, 50))


def test_depth_sets_the_cap_and_no_cap_needs_confirmation() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, STORY)
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: current(wizard) == "team")
        cap = wizard.query_one("#wiz-cap-note", Static)
        assert "$0.60" in render(cap)
        wizard.query_one("#depth-deep", Button).press()
        await wait_for(pilot, lambda: "$1.50" in render(cap))
        await wait_for(pilot, lambda: services.team_options[-1].depth == "deep")
        wizard.query_one("#wiz-custom-cap", Button).press()
        await pilot.pause()
        assert wizard.query_one("#wiz-cap-field").display
        wizard.query_one("#wiz-budget", Input).value = "abc"
        wizard.query_one("#wiz-next", Button).press()
        error = wizard.query_one("#wiz-error", Static)
        await wait_for(pilot, lambda: "number above 0" in render(error))
        wizard.query_one("#wiz-budget", Input).value = "0.4"
        await pilot.pause()
        assert wizard.options().budget_usd == 0.4
        wizard.query_one("#wiz-no-cap", Checkbox).value = True
        await wait_for(
            pilot, lambda: isinstance(app.screen, ConfirmScreen) and app.screen.query("#confirm-ok")
        )
        app.screen.query_one("#confirm-ok", Button).press()
        await wait_for(pilot, lambda: wizard.no_cap)
        assert "No spending cap" in render(cap)
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: launched(app) is not None)
        screen = launched(app)
        assert screen is not None
        assert screen.options.no_cap
        assert screen.options.depth == "deep"

    drive(make_app(services), scenario, size=(120, 50))


def test_declining_no_cap_keeps_the_cap() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, STORY)
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: current(wizard) == "team")
        wizard.query_one("#wiz-no-cap", Checkbox).value = True
        await wait_for(
            pilot,
            lambda: isinstance(app.screen, ConfirmScreen) and app.screen.query("#confirm-cancel"),
        )
        app.screen.query_one("#confirm-cancel", Button).press()
        await wait_for(pilot, lambda: not isinstance(app.screen, ConfirmScreen))
        assert not wizard.no_cap
        assert not wizard.query_one("#wiz-no-cap", Checkbox).value

    drive(make_app(services), scenario, size=(120, 50))


def test_one_page_layout_stacks_everything_with_a_summary() -> None:
    services = FakeServices(layout="one_page")

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        assert wizard.one_page
        assert wizard.query_one("#wiz-summary").display
        assert not wizard.query_one("#wizard-nav").display
        assert wizard.query_one("#step-team").display
        wizard.query_one("#wiz-story", TextArea).text = STORY
        wizard.query_one("#wiz-understand", Button).press()
        await wait_for(pilot, lambda: bool(wizard.query("#override-analyst")))
        summary = wizard.query_one("#summary-body", Static)
        await wait_for(pilot, lambda: "Based on 1 similar run" in render(summary))
        assert "Investigate" in render(summary)
        assert wizard.step == 0
        wizard.query_one("#wiz-launch", Button).press()
        await wait_for(pilot, lambda: launched(app) is not None)

    drive(make_app(services), scenario, size=(120, 50))


def test_understanding_card_shows_jev_fallback_in_spanish() -> None:
    class FallbackServices(FakeServices):
        def understand(self, story: str) -> Understanding:
            return replace(
                super().understand(story),
                fallback_error="jev answered HTTP 503",
                fallback_from="jev",
            )

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, STORY)
        detected = render(wizard.query_one("#wiz-detected", Static))
        assert "Jev falló: jev answered HTTP 503; respondió el heurístico" in detected

    drive(make_app(FallbackServices(), language="es"), scenario, size=(120, 50))


@pytest.mark.parametrize(
    ("layout", "language", "label"),
    [("guided", "en", "Engine"), ("one_page", "es", "Motor")],
)
def test_team_engine_select_uses_installed_engines_and_launches_choice(
    layout: str, language: str, label: str
) -> None:
    services = FakeServices(
        layout=layout,
        engines=(("claude", True), ("codex", True), ("opencode", False)),
    )
    story = "Fix the cart total: AssertionError: expected 10 got 12. Don't touch payments."

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        if layout == "guided":
            await tell(wizard, pilot, story)
            wizard.query_one("#wiz-next", Button).press()
            await wait_for(pilot, lambda: current(wizard) == "team")
        else:
            wizard.query_one("#wiz-story", TextArea).text = story
            wizard.query_one("#wiz-understand", Button).press()
            await wait_for(pilot, lambda: wizard.kind == "bug")
        selector = wizard.query_one("#wiz-engine", Select)
        assert label in render(wizard.query_one("#wiz-engine-label", Static))
        assert wizard.engines == ("claude", "codex")
        assert [value for _, value in selector._options if isinstance(value, str) and value] == [
            "claude",
            "codex",
        ]
        assert selector.value == "claude"
        selector.value = "codex"
        await wait_for(
            pilot,
            lambda: (
                wizard.engine == "codex"
                and wizard.plan is not None
                and wizard.query_one("#team-cards").display
                and bool(wizard.query("#override-senior"))
                and "gpt-5.6-sol"
                in [value for _, value in wizard.query_one("#override-senior", Select)._options]
                and bool(wizard.query_one("#override-senior", Select).query("#label"))
            ),
        )
        override = wizard.query_one("#override-senior", Select)
        assert override.value == ""
        assert not wizard.options().route.role_models
        override.value = "gpt-5.6-sol"
        assert ("senior", "gpt-5.6-sol") in wizard.options().route.role_models
        wizard.query_one("#wiz-next" if layout == "guided" else "#wiz-launch", Button).press()
        await wait_for(pilot, lambda: launched(app) is not None)
        screen = launched(app)
        assert screen is not None
        assert screen.options.engine == "codex"
        assert ("senior", "gpt-5.6-sol") in screen.options.route.role_models

    drive(make_app(services, language), scenario, size=(120, 50))


@pytest.mark.parametrize("layout", ["guided", "one_page"])
@pytest.mark.parametrize("action", ["launch", "preview"])
def test_empty_investigation_questions_block_before_pipeline_in_spanish(
    layout: str, action: str
) -> None:
    services = FakeServices(layout=layout)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        if layout == "guided":
            await tell(wizard, pilot, STORY)
            wizard.query_one("#wiz-next", Button).press()
            await wait_for(pilot, lambda: current(wizard) == "team")
        else:
            wizard.query_one("#wiz-story", TextArea).text = STORY
            wizard.query_one("#wiz-understand", Button).press()
            await wait_for(pilot, lambda: wizard.understanding is not None)
        questions = wizard.query_one("#wiz-why", TextArea)
        questions.text = ""
        button = (
            "#wiz-next"
            if layout == "guided" and action == "launch"
            else "#wiz-team-preview"
            if layout == "guided"
            else "#wiz-launch"
            if action == "launch"
            else "#wiz-preview"
        )
        wizard.query_one(button, Button).press()
        error = wizard.query_one("#wiz-error", Static)
        await wait_for(pilot, lambda: "Preguntas que debe responder" in render(error))
        assert current(wizard) == "confirm"
        assert "-invalid" in questions.classes
        assert app.focused is questions
        assert launched(app) is None
        assert not wizard.query_one("#preview-card").display

    drive(make_app(services, language="es"), scenario, size=(120, 50))


def test_switching_layout_saves_the_setting() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        app.query_one("#layout-one_page", Button).press()
        await wait_for(pilot, lambda: services.saved.get("ui.mandate_layout") == "one_page")
        assert wizard.one_page
        app.query_one("#layout-guided", Button).press()
        await wait_for(pilot, lambda: services.saved.get("ui.mandate_layout") == "guided")
        assert not wizard.one_page

    drive(make_app(services), scenario, size=(120, 50))


def test_refine_with_ai_shows_the_cost_then_a_diff_to_accept() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, "Add CSV export to the orders page")
        wizard.query_one("#wiz-improve", Button).press()
        note = wizard.query_one("#wiz-improve-note", Static)
        await wait_for(pilot, lambda: "claude:haiku" in render(note))
        assert services.improvements == [False]
        wizard.query_one("#wiz-improve", Button).press()
        await wait_for(pilot, lambda: services.improvements == [False, True])
        await wait_for(pilot, lambda: "(clear)" in render(note))
        wizard.query_one("#wiz-accept", Button).press()
        await pilot.pause()
        assert wizard.query_one("#wiz-what", TextArea).text.endswith("(clear)")

    drive(make_app(services), scenario, size=(120, 50))


def test_see_what_is_sent_matches_the_payload() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, "fix login; token=abc123secret")
        wizard.query_one("#wiz-sent", Button).press()
        body = wizard.query_one("#wiz-sent-body", Static)
        await wait_for(pilot, lambda: "kind" in render(body))
        expected = json.dumps(sent_payload(wizard.request(), remote=True), indent=2)
        assert expected in render(body)

    drive(make_app(services), scenario, size=(120, 50))
