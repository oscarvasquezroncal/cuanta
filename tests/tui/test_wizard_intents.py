from __future__ import annotations

from textual.pilot import Pilot
from textual.widgets import Input, Label, Select, TextArea

from cuanta.domain.mandate import MandateRequest
from cuanta.tui.app import CuantaApp
from cuanta.tui.widgets.wizard import IntentCard, MandateWizard
from tests.tui.fakes import FakeServices
from tests.tui.test_app import drive, make_app
from tests.tui.test_t5_screens import render, wait_for
from tests.tui.test_wizard import current, open_wizard, tell


async def choose(wizard: MandateWizard, pilot: Pilot[None], kind: str) -> None:
    if wizard.kind == kind:
        return
    wizard.query_one(f"#intent-{kind}", IntentCard).post_message(IntentCard.Chosen(kind))
    await wait_for(pilot, lambda: wizard.kind == kind)


def test_each_intent_asks_its_own_questions() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, "Add CSV export to the orders page")
        await choose(wizard, pilot, "feature")
        assert render(wizard.query_one("#wiz-what-label", Label)) == "What should it do?"
        assert render(wizard.query_one("#wiz-why-label", Label)) == "Acceptance criteria"
        assert not wizard.query_one("#wiz-evidence-actions").display
        assert not wizard.query_one("#limits-tests").display
        assert wizard.query_one("#limits-constraints").display
        wizard.query_one("#wiz-why", TextArea).text = "The CSV has a header row"
        request = wizard.request()
        assert request.tests == "The CSV has a header row"
        assert request.why == ""

        await choose(wizard, pilot, "refactor")
        assert render(wizard.query_one("#wiz-why-label", Label)) == "Invariants"
        assert wizard.request().constraints == "The CSV has a header row"
        assert not wizard.query_one("#limits-constraints").display

        await choose(wizard, pilot, "investigation")
        assert render(wizard.query_one("#wiz-what-label", Label)) == (
            "What do you want to understand?"
        )
        assert render(wizard.query_one("#wiz-where-label", Label)).startswith("Scope")
        assert wizard.query_one("#wiz-deliverable-field").display
        wizard.query_one("#wiz-deliverable", Select).value = "risks"
        investigation = wizard.request()
        assert investigation.why == "The CSV has a header row"
        assert investigation.tests == "Deliverable: a list of risks, most serious first"
        assert not wizard.query_one("#limits-tests").display
        assert not wizard.query_one("#limits-constraints").display

    drive(make_app(services), scenario, size=(120, 50))


def test_prefill_maps_fields_back_for_each_intent() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        wizard.prefilled(
            MandateRequest(
                type="feature",
                what="Add CSV export",
                tests="one row per order",
                out_of_scope="JSON export",
            )
        )
        await wait_for(pilot, lambda: current(wizard) == "confirm")
        assert wizard.query_one("#wiz-why", TextArea).text == "one row per order"
        assert wizard.query_one("#wiz-out", Input).value == "JSON export"
        wizard.prefilled(
            MandateRequest(
                type="investigation",
                what="How fonts load",
                why="Where are they imported?",
                tests="Deliverable: a diagram of the flow (Mermaid) with a short explanation",
            )
        )
        await pilot.pause()
        assert wizard.query_one("#wiz-deliverable", Select).value == "diagram"
        assert wizard.query_one("#wiz-why", TextArea).text == "Where are they imported?"

    drive(make_app(services), scenario, size=(120, 50))
