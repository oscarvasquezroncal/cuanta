from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, Checkbox, Input, Select, Static, TextArea

from cuanta.application.assistant import sent_payload
from cuanta.application.intake import Understanding
from cuanta.domain.cache import UNKNOWN_PREFIX, PrefixState, PrefixWindow
from cuanta.domain.change_plan import ChangePlan
from cuanta.domain.envelope import RoleInput, RoleModel, envelope
from cuanta.domain.messages import msg
from cuanta.domain.routing import Provider, Role
from cuanta.domain.team import ProviderAdvice
from cuanta.tui.app import CuantaApp
from cuanta.tui.cache_text import clock_time
from cuanta.tui.screens.confirm import ConfirmScreen
from cuanta.tui.screens.pipeline import PipelineScreen
from cuanta.tui.views.mandate import MandateView
from cuanta.tui.widgets.wizard import IntentCard, MandateWizard
from tests.tui.fakes import TEAM_CATALOG, FakeServices, sample_forecast
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


def test_team_shows_the_measured_prefix_window() -> None:
    until = datetime(2026, 1, 5, 10, 10, tzinfo=UTC)
    services = FakeServices(prefix=PrefixWindow(PrefixState.WARM, until))

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, STORY)
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: current(wizard) == "team")
        prefix = wizard.query_one("#wiz-prefix", Static)
        await wait_for(pilot, lambda: "last observed Claude prefix warm until" in render(prefix))
        assert clock_time(until) in render(prefix)
        assert "claude" in services.prefix_engines
        services.prefix = UNKNOWN_PREFIX
        wizard.query_one("#wiz-engine", Select).value = "opencode"
        await wait_for(pilot, lambda: "opencode" in services.prefix_engines)
        assert "last observed prefix: unknown" in render(prefix)

    drive(make_app(services), scenario, size=(120, 50))


@pytest.mark.parametrize("tight", [False, True], ids=["comfortable", "tight"])
def test_team_shows_the_forecast_line_and_a_tight_verdict_with_its_first_suggestion(
    tight: bool,
) -> None:
    base = sample_forecast()
    p90 = base.envelope.p90_usd or 0.0
    forecast = sample_forecast(cap=p90 * 0.95) if tight else base
    services = FakeServices(forecast=forecast)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, STORY)
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: current(wizard) == "team")
        estimate = wizard.query_one("#wiz-estimate", Static)
        await wait_for(pilot, lambda: "Forecast $" in render(estimate))
        shown = render(estimate)
        assert "Based on 1 similar run" not in shown
        assert "warm cache (78%)" in shown
        assert ("Tight: the P90 is within 15% of the" in shown) is tight
        assert ("Try: " in shown) is tight
        if tight:
            assert f"{forecast.envelope.suggestions[0].p90_usd:.2f}" in shown

    drive(make_app(services), scenario, size=(120, 50))


def test_an_unpriced_forecast_keeps_the_history_estimate_above_it() -> None:
    base = sample_forecast()
    inputs = replace(
        base.inputs, roles=(RoleInput(Role.SENIOR, RoleModel("unlisted-model", None)),)
    )
    services = FakeServices(forecast=replace(base, inputs=inputs, envelope=envelope(inputs)))

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, STORY)
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: current(wizard) == "team")
        estimate = wizard.query_one("#wiz-estimate", Static)
        await wait_for(pilot, lambda: "Forecast n/a" in render(estimate))
        assert "Based on 1 similar run: $0.55" in render(estimate)

    drive(make_app(services), scenario, size=(120, 50))


def test_a_failed_forecast_is_a_warning_under_the_history_estimate() -> None:
    failure = msg("envelope.failed", error="the code index is being written")
    services = FakeServices(forecast_error=failure)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, STORY)
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: current(wizard) == "team")
        estimate = wizard.query_one("#wiz-estimate", Static)
        await wait_for(pilot, lambda: "Forecast unavailable" in render(estimate))
        shown = " ".join(render(estimate).split())
        assert "Based on 1 similar run: $0.55" in shown
        assert "the code index is being written" in shown

    drive(make_app(services), scenario, size=(120, 50))


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


def test_team_step_shows_the_turn_limit_and_repaints_on_engine_change() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, STORY)
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: current(wizard) == "team")
        cap = wizard.query_one("#wiz-cap-note", Static)
        assert "$0.60" in render(cap)
        assert "Turn limit: 40" in render(cap)
        wizard.query_one("#depth-deep", Button).press()
        await wait_for(pilot, lambda: "Turn limit: 80" in render(cap))
        wizard.query_one("#wiz-engine", Select).value = "opencode"
        await wait_for(pilot, lambda: wizard.engine == "opencode")
        assert "Turn limit" not in render(cap)
        wizard.query_one("#wiz-engine", Select).value = "claude"
        await wait_for(pilot, lambda: "Turn limit: 80" in render(cap))
        assert wizard.options().max_turns == 0

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
        await wait_for(pilot, lambda: app.focused is questions)
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


async def reach_team(wizard: MandateWizard, pilot: Pilot[None]) -> None:
    await tell(wizard, pilot, STORY)
    wizard.query_one("#intent-feature", IntentCard).post_message(IntentCard.Chosen("feature"))
    await wait_for(pilot, lambda: wizard.kind == "feature")
    wizard.query_one("#wiz-why", TextArea).text = "The checkout needs a canonical tag"
    wizard.query_one("#wiz-out", Input).value = "the footer"
    wizard.query_one("#wiz-next", Button).press()
    await wait_for(pilot, lambda: current(wizard) == "team")


def choices(wizard: MandateWizard, role: str) -> dict[str, str]:
    select = wizard.query_one(f"#override-{role}", Select)
    return {str(value): str(prompt) for prompt, value in select._options}


def test_provider_chips_pick_one_provider_and_show_each_roles_model_and_price() -> None:
    services = FakeServices(engines=(("claude", True), ("codex", True)), catalog=TEAM_CATALOG)
    services.change_plan_result = ChangePlan(verify=("npx tsc --noEmit", "npm run build"))
    services.advice = ProviderAdvice(Provider.CLAUDE, 2, 2, 0.91)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await reach_team(wizard, pilot)
        note = wizard.query_one("#wiz-provider-note", Static)
        assert "One Claude session" in render(note)
        assert wizard.query_one("#provider-claude", Button).has_class("-current")
        assert not wizard.query_one("#provider-codex", Button).has_class("-current")
        await wait_for(pilot, lambda: "recommend the Claude team: $0.9100" in render(note))
        cards = wizard.query_one("#team-cards")
        await wait_for(pilot, lambda: "Claude, opus, premium tier" in render_all(cards))
        assert "$4.00 in, $20.00 out per million tokens" in render_all(cards)
        assert choices(wizard, "senior") == {
            "": "Keep the plan",
            "opus": "opus $4.00/$20.00",
            "sonnet": "sonnet $2.00/$10.00",
            "haiku": "haiku $1.00/$5.00",
        }

        wizard.query_one("#provider-codex", Button).press()
        await wait_for(pilot, lambda: services.team_options[-1].engine == "codex")
        await wait_for(pilot, lambda: "One launch per role" in render(note))
        assert wizard.query_one("#provider-codex", Button).has_class("-current")
        assert not wizard.query_one("#provider-claude", Button).has_class("-current")
        await wait_for(pilot, lambda: "Codex, gpt-6-sol, premium tier" in render_all(cards))
        text = render_all(cards)
        assert "Codex, gpt-6-sol, standard tier" in text
        assert "Codex, gpt-6-luna, economy tier" in text
        assert "$2.00 in, $10.00 out per million tokens" in text
        assert "$0.10 in, $0.50 out per million tokens" in text
        assert "gpt-5.6" not in text
        assert "Claude" not in text and "opus" not in text
        assert "Orchestrator" not in text
        assert "Enforced:" in text and "Checked after the run:" in text
        assert "Context: index tools and the anchored handoff chain" in text
        assert "Codex cannot run builds on this Windows host; cuanta verifies instead" in text
        assert " · " not in text
        assert choices(wizard, "docs") == {
            "": "Keep the plan",
            "gpt-5.6-sol": "gpt-5.6-sol $4.00/$20.00",
            "gpt-9-private": "gpt-9-private (no price)",
            "gpt-6-sol": "gpt-6-sol $2.00/$10.00",
            "gpt-6-luna": "gpt-6-luna $0.10/$0.50",
        }
        verify = wizard.query_one("#wiz-verify-note", Static)
        await wait_for(pilot, lambda: "npx tsc --noEmit, npm run build" in render(verify))
        assert verify.display
        assert wizard.options().engine == "codex"

        wizard.query_one("#wiz-team-preview", Button).press()
        card = wizard.query_one("#preview-card")
        await wait_for(pilot, lambda: card.display)
        team = wizard.query_one("#preview-team", Static)
        assert team.display
        assert "One launch per role" in render(wizard.query_one("#preview-command", Static))
        shown = render(team)
        assert "Model per role" in shown
        assert "Analyst: gpt-6-sol, standard tier" in shown
        assert "Senior: gpt-6-sol, premium tier" in shown
        assert "Tester: gpt-6-sol, standard tier" in shown
        assert "Docs: gpt-6-luna, economy tier" in shown
        assert "Orchestrator" not in shown

        wizard.query_one("#provider-claude", Button).press()
        await wait_for(pilot, lambda: wizard.options().engine == "claude")
        await wait_for(pilot, lambda: "One Claude session" in render(note))
        await wait_for(pilot, lambda: "Claude, opus, premium tier" in render_all(cards))
        wizard.query_one("#wiz-team-preview", Button).press()
        await wait_for(pilot, lambda: "Orchestrator: sonnet, standard tier" in render(team))
        assert "Senior: opus, premium tier" in render(team)
        assert '"<prompt>"' in render(wizard.query_one("#preview-command", Static))

    drive(make_app(services), scenario, size=(120, 50))


def test_the_engine_menu_marks_the_provider_chip_before_any_team_is_planned() -> None:
    services = FakeServices(engines=(("claude", True), ("codex", True)), catalog=TEAM_CATALOG)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await reach_team(wizard, pilot)
        wizard.kind = ""
        wizard.query_one("#wiz-engine", Select).value = "codex"
        await pilot.pause()
        assert wizard.engine == "codex"
        assert wizard.query_one("#provider-codex", Button).has_class("-current")
        assert not wizard.query_one("#provider-claude", Button).has_class("-current")
        assert "One launch per role" in render(wizard.query_one("#wiz-provider-note", Static))

    drive(make_app(services), scenario, size=(120, 50))


def test_the_provider_step_speaks_spanish() -> None:
    services = FakeServices(engines=(("claude", True), ("codex", True)), catalog=TEAM_CATALOG)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await reach_team(wizard, pilot)
        assert "Equipo Claude" in str(wizard.query_one("#provider-claude", Button).label)
        assert "Equipo GPT" in str(wizard.query_one("#provider-codex", Button).label)
        note = wizard.query_one("#wiz-provider-note", Static)
        assert "Una sesión de Claude" in render(note)
        wizard.choose_provider("codex")
        await wait_for(pilot, lambda: "Un lanzamiento por rol" in render(note))
        cards = wizard.query_one("#team-cards")
        await wait_for(pilot, lambda: "Codex, gpt-6-sol, nivel premium" in render_all(cards))
        assert "$2.00 entrada, $10.00 salida por millón de tokens" in render_all(cards)
        assert choices(wizard, "senior")["gpt-9-private"] == "gpt-9-private (sin precio)"

    drive(make_app(services, language="es"), scenario, size=(120, 50))


def render_all(widget: object) -> str:
    from textual.widget import Widget

    assert isinstance(widget, Widget)
    return "\n".join(render(item) for item in widget.query(Static))
