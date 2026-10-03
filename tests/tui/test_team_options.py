from __future__ import annotations

from collections.abc import Callable

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, Checkbox, Label, Select, Static

from cuanta.domain.claude_variants import VARIANTS
from cuanta.tui.app import CuantaApp
from cuanta.tui.widgets.wizard import IntentCard, MandateWizard
from tests.tui.fakes import TEAM_CATALOG, FakeServices
from tests.tui.test_app import drive, make_app
from tests.tui.test_snapshots import loaded
from tests.tui.test_t5_screens import render, wait_for
from tests.tui.test_wizard import current, launched, open_wizard, reach_team, tell
from tests.tui.test_wizard_intents import choose

BADGE = "Add a badge to the cart. Don't touch payments."
REFACTOR = "Refactor the cart module into smaller functions. Don't touch payments."
MODEL = "#wiz-implementation-model"
VARIANT = "#wiz-implementation-variant"
FAST_OUTPUT = frozenset(name for name in VARIANTS if name.startswith("fast"))


def is_fast(wizard: MandateWizard) -> bool:
    return wizard.fast_profile


def options_text(wizard: MandateWizard) -> list[str]:
    select = wizard.query_one("#wiz-implementation-model", Select)
    return [str(prompt) for prompt, _ in select._options]


def shown(wizard: MandateWizard, selector: str) -> str:
    return render(wizard.query_one(selector, Select).query_one("#label", Static))


def values(wizard: MandateWizard, selector: str) -> list[str]:
    return [str(value) for _, value in wizard.query_one(selector, Select)._options]


def card_text(wizard: MandateWizard, role: str) -> str:
    card = wizard.query_one(f"#override-{role}", Select).parent
    assert card is not None
    return render(card.query_one(".team-body", Static))


def has_card(wizard: MandateWizard, role: str) -> bool:
    return wizard.plan is not None and bool(wizard.query(f"#override-{role}"))


async def on_team(wizard: MandateWizard, pilot: Pilot[None]) -> None:
    await tell(wizard, pilot, BADGE)
    wizard.go(2)
    await wait_for(pilot, lambda: current(wizard) == "team")


async def balanced_choices(wizard: MandateWizard, pilot: Pilot[None]) -> None:
    wizard.query_one("#wiz-implementation-model", Select).value = "claude-opus-5-5"
    wizard.query_one("#wiz-implementation-variant", Select).value = "ultracode"
    wizard.query_one("#wiz-pure", Checkbox).value = True
    await pilot.pause()


def test_the_balanced_team_takes_a_model_a_variant_and_pure() -> None:
    services = FakeServices(engines=(("claude", True), ("codex", True)), catalog=TEAM_CATALOG)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await reach_team(wizard, pilot)
        await wait_for(
            pilot, lambda: wizard.plan is not None and bool(wizard.query("#override-senior"))
        )
        assert not is_fast(wizard)
        assert wizard.query_one("#implementation-options").display
        assert wizard.query_one("#wiz-pure-row").display
        assert not wizard.query_one("#wiz-implementation-pure-note").display
        assert render(wizard.query_one("#wiz-implementation-model-label", Label)) == (
            "Main session model"
        )
        assert options_text(wizard)[0] == "Keep the plan"
        wizard.query_one("#override-senior", Select).value = "haiku"
        await pilot.pause()
        assert wizard.options().route.role_models == (("senior", "haiku"),)
        await balanced_choices(wizard, pilot)
        await wait_for(pilot, lambda: services.team_options[-1].pure)
        seen = services.team_options[-1]
        assert (seen.profile, seen.model, seen.variant, seen.pure) == (
            "balanced",
            "claude-opus-5-5",
            "ultracode",
            True,
        )
        assert seen.route.role_models == ()
        await wait_for(
            pilot, lambda: wizard.plan is not None and bool(wizard.query("#override-senior"))
        )
        assert wizard.query_one("#override-senior", Select).disabled
        depth = render(wizard.query_one("#wiz-depth-note", Static))
        assert "Ultracode" in depth
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: launched(app) is not None)
        screen = launched(app)
        assert screen is not None
        chosen = screen.options
        assert (chosen.profile, chosen.model, chosen.variant, chosen.pure) == (
            "balanced",
            "claude-opus-5-5",
            "ultracode",
            True,
        )
        assert chosen.route.role_models == ()

    drive(make_app(services), scenario, size=(120, 50))


def test_pure_off_gives_the_team_cards_their_picks_back() -> None:
    services = FakeServices(engines=(("claude", True), ("codex", True)), catalog=TEAM_CATALOG)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await reach_team(wizard, pilot)
        await wait_for(
            pilot, lambda: wizard.plan is not None and bool(wizard.query("#override-senior"))
        )
        wizard.query_one("#override-senior", Select).value = "haiku"
        await pilot.pause()
        pure = wizard.query_one("#wiz-pure", Checkbox)
        pure.value = True
        await wait_for(pilot, lambda: services.team_options[-1].pure)
        pure.value = False
        await wait_for(pilot, lambda: not services.team_options[-1].pure)
        await wait_for(
            pilot, lambda: wizard.plan is not None and bool(wizard.query("#override-senior"))
        )
        assert not wizard.query_one("#override-senior", Select).disabled
        assert wizard.options().route.role_models == (("senior", "haiku"),)
        assert not wizard.options().pure

    drive(make_app(services), scenario, size=(120, 50))


def test_the_fast_team_keeps_its_own_model_label_and_implied_pure() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, BADGE)
        wizard.query_one("#implementation-fast", Button).press()
        await wait_for(pilot, lambda: wizard.fast_profile)
        assert render(wizard.query_one("#wiz-implementation-model-label", Label)) == (
            "One model for the run"
        )
        assert options_text(wizard)[0] == "By kind of change"
        assert wizard.query_one("#wiz-implementation-pure-note").display
        assert not wizard.query_one("#wiz-pure-row").display
        wizard.query_one("#wiz-pure", Checkbox).value = True
        await pilot.pause()
        assert not wizard.options().pure

    drive(make_app(FakeServices()), scenario, size=(120, 50))


def test_the_options_follow_the_engine_and_simple_mode() -> None:
    services = FakeServices(engines=(("claude", True), ("codex", True)), catalog=TEAM_CATALOG)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await reach_team(wizard, pilot)
        await balanced_choices(wizard, pilot)
        wizard.choose_provider("codex")
        await wait_for(pilot, lambda: services.team_options[-1].engine == "codex")
        assert not wizard.query_one("#implementation-options").display
        seen = services.team_options[-1]
        assert (seen.model, seen.variant, seen.pure) == ("", "", False)
        wizard.choose_provider("claude")
        await wait_for(pilot, lambda: services.team_options[-1].engine == "claude")
        assert services.team_options[-1].pure
        wizard.simple = True
        assert (wizard.options().model, wizard.options().variant, wizard.options().pure) == (
            "",
            "",
            False,
        )

    drive(make_app(services), scenario, size=(120, 50))


def test_the_auto_chip_returns_to_the_automatic_profile() -> None:
    services = FakeServices(profile="auto")

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, BADGE)
        wizard.go(2)
        await wait_for(pilot, lambda: current(wizard) == "team")
        auto = wizard.query_one("#implementation-auto", Button)
        balanced = wizard.query_one("#implementation-balanced", Button)
        fast = wizard.query_one("#implementation-fast", Button)
        assert auto.has_class("-on") and fast.has_class("-current")
        assert is_fast(wizard) and wizard.options().profile == "auto"
        balanced.press()
        await wait_for(pilot, lambda: wizard.options().profile == "balanced")
        assert not auto.has_class("-on") and balanced.has_class("-current")
        assert not is_fast(wizard)
        await wait_for(pilot, lambda: services.team_options[-1].profile == "balanced")
        auto.press()
        await wait_for(pilot, lambda: wizard.options().profile == "auto")
        assert auto.has_class("-on") and fast.has_class("-current")
        assert not balanced.has_class("-current") and is_fast(wizard)
        await wait_for(pilot, lambda: services.team_options[-1].profile == "auto")
        fast.press()
        await wait_for(pilot, lambda: wizard.options().profile == "fast")
        assert not auto.has_class("-on") and fast.has_class("-current")
        auto.press()
        await wait_for(pilot, lambda: wizard.options().profile == "auto")

    drive(make_app(services), scenario, size=(120, 50))


def test_a_configured_balanced_profile_can_still_go_auto() -> None:
    services = FakeServices(profile="balanced")

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, BADGE)
        wizard.go(2)
        auto = wizard.query_one("#implementation-auto", Button)
        assert not auto.has_class("-on") and not is_fast(wizard)
        auto.press()
        await wait_for(pilot, lambda: wizard.fast_profile)
        assert auto.has_class("-on") and wizard.options().profile == "auto"

    drive(make_app(services), scenario, size=(120, 50))


def test_the_spanish_team_step_says_sin_limites_until_a_limit_is_set() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await reach_team(wizard, pilot)
        note = wizard.query_one("#wiz-limits-note", Static)
        await wait_for(pilot, lambda: render(note) == "sin límites")
        assert str(wizard.query_one("#implementation-auto", Button).label) == "Auto"
        assert render(wizard.query_one("#wiz-implementation-model-label", Label)) == (
            "Modelo de la sesión principal"
        )
        wizard.query_one("#wiz-limits", Checkbox).value = True
        await wait_for(pilot, lambda: render(note) == "límites: tope de gasto $2.00 · 40 turnos")

    drive(make_app(services, language="es"), scenario, size=(120, 50))


def test_the_model_and_variant_selects_show_the_labels_of_the_chosen_profile() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await on_team(wizard, pilot)
        assert (shown(wizard, MODEL), shown(wizard, VARIANT)) == (
            "Keep the plan",
            "Effort from depth",
        )
        wizard.query_one("#implementation-fast", Button).press()
        await wait_for(pilot, lambda: wizard.fast_profile)
        await pilot.pause()
        assert (shown(wizard, MODEL), shown(wizard, VARIANT)) == (
            "By kind of change",
            "By kind of change",
        )
        wizard.query_one("#implementation-balanced", Button).press()
        await wait_for(pilot, lambda: not wizard.fast_profile)
        await pilot.pause()
        assert (shown(wizard, MODEL), shown(wizard, VARIANT)) == (
            "Keep the plan",
            "Effort from depth",
        )
        assert wizard.options().model == ""

    drive(make_app(FakeServices()), scenario, size=(120, 50))


def test_an_explicit_fast_profile_returns_to_auto_when_fast_cannot_run() -> None:
    services = FakeServices(engines=(("claude", True), ("codex", True)))

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await on_team(wizard, pilot)
        auto = wizard.query_one("#implementation-auto", Button)
        fast = wizard.query_one("#implementation-fast", Button)
        fast.press()
        await wait_for(pilot, lambda: wizard.options().profile == "fast")
        wizard.choose_provider("codex")
        assert wizard.options().profile == "auto"
        assert auto.has_class("-on") and not wizard.fast_profile
        await wait_for(
            pilot, lambda: [item.engine for item in services.team_options[-1:]] == ["codex"]
        )
        assert services.team_options[-1].profile == "auto"
        wizard.choose_provider("claude")
        assert wizard.options().profile == "auto" and auto.has_class("-on")
        fast.press()
        await wait_for(pilot, lambda: wizard.options().profile == "fast")
        wizard.query_one("#wiz-engine", Select).value = "codex"
        await wait_for(pilot, lambda: wizard.engine == "codex")
        assert wizard.options().profile == "auto"
        wizard.choose_provider("claude")
        fast.press()
        await wait_for(pilot, lambda: wizard.options().profile == "fast")
        wizard.go(1)
        await wait_for(pilot, lambda: current(wizard) == "confirm")
        card = wizard.query_one("#intent-investigation", IntentCard)
        card.post_message(IntentCard.Chosen("investigation"))
        await wait_for(pilot, lambda: wizard.kind == "investigation")
        assert wizard.options().profile == "auto"
        assert auto.has_class("-on")

    drive(make_app(services), scenario, size=(120, 50))


def test_the_orchestrator_card_runs_on_the_chosen_main_session_model() -> None:
    services = FakeServices(engines=(("claude", True), ("codex", True)), catalog=TEAM_CATALOG)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await reach_team(wizard, pilot)
        await wait_for(pilot, lambda: has_card(wizard, "orchestrator"))
        assert "Claude, sonnet, standard tier" in card_text(wizard, "orchestrator")
        wizard.query_one("#override-orchestrator", Select).value = "haiku"
        await pilot.pause()
        assert wizard.options().route.role_models == (("orchestrator", "haiku"),)
        wizard.query_one(MODEL, Select).value = "claude-opus-5-5"
        await wait_for(
            pilot,
            lambda: [item.model for item in services.team_options[-1:]] == ["claude-opus-5-5"],
        )
        await wait_for(
            pilot,
            lambda: (
                has_card(wizard, "orchestrator")
                and "Claude, opus, premium tier" in card_text(wizard, "orchestrator")
            ),
        )
        shown_card = card_text(wizard, "orchestrator")
        assert "the main session runs on the chosen model claude-opus-5-5" in shown_card
        assert wizard.query_one("#override-orchestrator", Select).disabled
        assert not wizard.query_one("#override-senior", Select).disabled
        assert wizard.options().route.role_models == ()
        wizard.query_one(MODEL, Select).value = values(wizard, MODEL)[0]
        await wait_for(pilot, lambda: [item.model for item in services.team_options[-1:]] == [""])
        await wait_for(
            pilot,
            lambda: (
                has_card(wizard, "orchestrator")
                and "Claude, sonnet, standard tier" in card_text(wizard, "orchestrator")
            ),
        )
        assert not wizard.query_one("#override-orchestrator", Select).disabled
        assert wizard.options().route.role_models == (("orchestrator", "haiku"),)

    drive(make_app(services), scenario, size=(120, 50))


def test_fast_output_variants_are_offered_only_on_an_opus_main_session() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await on_team(wizard, pilot)
        assert not FAST_OUTPUT & set(values(wizard, VARIANT))
        model = wizard.query_one(MODEL, Select)
        model.value = "claude-opus-5-5"
        await wait_for(pilot, lambda: set(values(wizard, VARIANT)) >= FAST_OUTPUT)
        wizard.query_one(VARIANT, Select).value = "fast-low"
        await wait_for(pilot, lambda: wizard.options().variant == "fast-low")
        model.value = "claude-sonnet-5"
        await wait_for(pilot, lambda: not FAST_OUTPUT & set(values(wizard, VARIANT)))
        assert shown(wizard, VARIANT) == "Effort from depth"
        assert not wizard.options().variant.startswith("fast")
        await wait_for(
            pilot,
            lambda: [item.model for item in services.team_options[-1:]] == ["claude-sonnet-5"],
        )
        assert not services.team_options[-1].variant.startswith("fast")
        wizard.query_one("#implementation-fast", Button).press()
        await wait_for(pilot, lambda: wizard.fast_profile)
        assert shown(wizard, MODEL) == "Sonnet 5"
        assert not FAST_OUTPUT & set(values(wizard, VARIANT))
        model.value = values(wizard, MODEL)[0]
        await wait_for(pilot, lambda: set(values(wizard, VARIANT)) >= FAST_OUTPUT)
        assert shown(wizard, MODEL) == "By kind of change"

    drive(make_app(services), scenario, size=(120, 50))


def test_fast_by_kind_offers_fast_output_only_for_a_kind_whose_choice_is_opus() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, REFACTOR)
        await choose(wizard, pilot, "refactor")
        wizard.go(2)
        await wait_for(pilot, lambda: current(wizard) == "team")
        wizard.choose_depth("quick")
        wizard.query_one("#implementation-fast", Button).press()
        await wait_for(pilot, lambda: wizard.fast_profile)
        assert shown(wizard, MODEL) == "By kind of change"
        assert not FAST_OUTPUT & set(values(wizard, VARIANT))
        model = wizard.query_one(MODEL, Select)
        model.value = "claude-opus-5-5"
        await wait_for(pilot, lambda: set(values(wizard, VARIANT)) >= FAST_OUTPUT)
        model.value = values(wizard, MODEL)[0]
        await wait_for(pilot, lambda: not FAST_OUTPUT & set(values(wizard, VARIANT)))
        assert not wizard.options().variant.startswith("fast")
        await choose(wizard, pilot, "bug")
        await wait_for(pilot, lambda: set(values(wizard, VARIANT)) >= FAST_OUTPUT)
        assert shown(wizard, MODEL) == "By kind of change"

    drive(make_app(services), scenario, size=(120, 50))


def test_the_auto_profile_offers_fast_output_only_after_an_opus_pick() -> None:
    services = FakeServices(profile="auto")

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await on_team(wizard, pilot)
        assert wizard.fast_profile and shown(wizard, MODEL) == "By kind of change"
        assert not FAST_OUTPUT & set(values(wizard, VARIANT))
        wizard.query_one(MODEL, Select).value = "claude-opus-5-5"
        await wait_for(pilot, lambda: set(values(wizard, VARIANT)) >= FAST_OUTPUT)
        wizard.query_one(VARIANT, Select).value = "fast-low"
        await wait_for(pilot, lambda: wizard.options().variant == "fast-low")
        assert (wizard.options().profile, wizard.options().model) == ("auto", "claude-opus-5-5")

    drive(make_app(services), scenario, size=(120, 50))


def test_effort_from_depth_sends_no_variant_when_none_is_configured() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await on_team(wizard, pilot)
        assert shown(wizard, VARIANT) == "Effort from depth"
        assert wizard.options().variant == ""
        await wait_for(pilot, lambda: services.team_options != [])
        assert services.team_options[-1].variant == ""
        wizard.choose_depth("deep")
        assert wizard.options().variant == ""
        variant = wizard.query_one(VARIANT, Select)
        variant.value = "high"
        await wait_for(pilot, lambda: wizard.options().variant == "high")
        variant.value = values(wizard, VARIANT)[0]
        await wait_for(pilot, lambda: wizard.options().variant == "")
        assert shown(wizard, VARIANT) == "Effort from depth"

    drive(make_app(services), scenario, size=(120, 50))


def test_effort_from_depth_sends_the_depth_effort_over_a_configured_variant() -> None:
    services = FakeServices(variant="ultracode")

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await on_team(wizard, pilot)
        assert shown(wizard, VARIANT) == "Ultracode"
        assert wizard.options().variant == "ultracode"
        wizard.query_one(VARIANT, Select).value = values(wizard, VARIANT)[0]
        await wait_for(pilot, lambda: wizard.options().variant != "ultracode")
        assert shown(wizard, VARIANT) == "Effort from depth"
        assert wizard.options().variant == "medium"
        await wait_for(
            pilot, lambda: [item.variant for item in services.team_options[-1:]] == ["medium"]
        )
        wizard.choose_depth("deep")
        assert wizard.options().variant == "high"
        await wait_for(
            pilot, lambda: [item.variant for item in services.team_options[-1:]] == ["high"]
        )
        note = render(wizard.query_one("#wiz-depth-note", Static))
        assert note.endswith("high effort.")

    drive(make_app(services), scenario, size=(120, 50))


def test_pure_puts_every_role_card_on_the_pure_model() -> None:
    services = FakeServices(engines=(("claude", True), ("codex", True)), catalog=TEAM_CATALOG)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await reach_team(wizard, pilot)
        await wait_for(pilot, lambda: has_card(wizard, "orchestrator"))
        assert "Claude, sonnet, standard tier" in card_text(wizard, "orchestrator")
        await balanced_choices(wizard, pilot)
        await wait_for(pilot, lambda: [item.pure for item in services.team_options[-1:]] == [True])
        await wait_for(
            pilot,
            lambda: (
                has_card(wizard, "orchestrator") and "opus" in card_text(wizard, "orchestrator")
            ),
        )
        roles = [
            str(select.id).removeprefix("override-")
            for select in wizard.query("#team-cards Select")
        ]
        assert "orchestrator" in roles and "senior" in roles
        for role in roles:
            shown_card = card_text(wizard, role)
            assert "Claude, opus, premium tier" in shown_card, role
            assert "pure: --pure runs every role on claude-opus-5-5" in shown_card, role

    drive(make_app(services), scenario, size=(120, 50))


@pytest.mark.parametrize("language", ["en", "es"])
@pytest.mark.parametrize("size", [(120, 36), (80, 24)], ids=("wide", "narrow"))
def test_balanced_team_options_snapshot(
    snap_compare: Callable[..., bool], language: str, size: tuple[int, int]
) -> None:
    async def balanced_team(pilot: Pilot[None]) -> None:
        app = pilot.app
        assert isinstance(app, CuantaApp)
        await loaded(pilot)
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, BADGE)
        wizard.go(2)
        await balanced_choices(wizard, pilot)
        await wait_for(pilot, lambda: wizard.estimate is not None)
        await loaded(pilot)
        await pilot.pause(0.2)
        await loaded(pilot)
        wizard.query_one("#implementation-row").scroll_visible(animate=False, top=True)
        await loaded(pilot)

    app = CuantaApp(
        FakeServices(),
        language,
        "calico-dark",
        motion=False,
        environ={"WT_SESSION": "1"},
        clock=lambda: 0.0,
    )
    assert snap_compare(app, terminal_size=size, run_before=balanced_team)
