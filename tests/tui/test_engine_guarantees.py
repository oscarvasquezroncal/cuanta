from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, Checkbox, Select, Static

from cuanta.tui.app import CuantaApp
from cuanta.tui.screens.result import ResultScreen
from tests.tui.fakes import FakeServices, sample_result
from tests.tui.test_app import drive, make_app, settle
from tests.tui.test_t5_screens import render, wait_for
from tests.tui.test_wizard import STORY, current, launched, open_wizard, tell


@pytest.mark.parametrize("language", ["en", "es"])
def test_team_updates_guarantees_and_refuses_unsafe_investigations(language: str) -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, STORY)
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: current(wizard) == "team")
        selector = wizard.query_one("#wiz-engine", Select)
        selector.value = "codex"
        await wait_for(pilot, lambda: wizard.engine == "codex")
        await wait_for(
            pilot,
            lambda: (
                wizard.plan is not None
                and all(route.engine == "codex" for route in wizard.plan.routes)
            ),
        )
        guarantees = render(wizard.query_one("#wiz-guarantees", Static))
        warning = render(wizard.query_one("#wiz-guarantee-warning", Static))
        assert "JSONL" in guarantees
        assert ("checked after the run" if language == "en" else "comprobado después") in guarantees
        assert not warning.strip()
        wizard.query_one("#wiz-limits", Checkbox).value = True
        expected = "cannot enforce" if language == "en" else "no puede aplicar"
        await wait_for(
            pilot,
            lambda: expected in render(wizard.query_one("#wiz-guarantee-warning", Static)),
        )
        assert not wizard.query_one("#wiz-next", Button).disabled
        selector.value = "opencode"
        await wait_for(pilot, lambda: wizard.engine == "opencode")
        warning = render(wizard.query_one("#wiz-guarantee-warning", Static))
        assert "graphify" in warning
        assert ("unavailable" if language == "en" else "no están disponibles") in warning
        assert wizard.query_one("#wiz-next", Button).disabled
        wizard.launch()
        await settle(app, pilot)
        assert launched(app) is None
        assert "graphify" in render(wizard.query_one("#wiz-error", Static))
        selector.value = "claude"
        await wait_for(pilot, lambda: wizard.engine == "claude")
        assert not wizard.query_one("#wiz-next", Button).disabled
        assert not render(wizard.query_one("#wiz-guarantee-warning", Static)).strip()

    services = FakeServices(engines=(("claude", True), ("codex", True), ("opencode", True)))
    drive(make_app(services, language=language), scenario, size=(120, 50))


@pytest.mark.parametrize("language", ["en", "es"])
def test_result_distinguishes_unknown_free_and_estimated_cost(language: str) -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        original = sample_result()
        for run, expected in (
            (
                replace(original.run, cost_usd=None, cost_source="unknown"),
                "n/a" if language == "en" else "n/d",
            ),
            (replace(original.run, cost_usd=0.0, cost_source="reported"), "$0.00"),
            (
                replace(original.run, cost_usd=0.12, cost_source="estimated"),
                "$0.12 (estimated)" if language == "en" else "$0.12 (estimado)",
            ),
        ):
            app.push_screen(ResultScreen(app.services, app.catalog, replace(original, run=run)))
            await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
            await settle(app, pilot)
            assert expected in render(app.screen.query_one("#result-facts", Static))
            app.pop_screen()
            await pilot.pause()

    drive(make_app(language=language), scenario, size=(120, 40))


@pytest.mark.parametrize(
    ("reason", "expected", "spanish"),
    [
        (
            "error_cost_unknown",
            "did not report the cost needed to enforce the $0.60 cap",
            "no informó el costo necesario para aplicar el tope de $0.60",
        ),
        (
            "error_max_budget_usd",
            "stopped at the spend cap of $0.60",
            "se detuvo en el tope de gasto de $0.60",
        ),
    ],
)
def test_result_explains_budget_termination(reason: str, expected: str, spanish: str) -> None:
    def scenario_for(text: str) -> Callable[[CuantaApp, Pilot[None]], Awaitable[None]]:
        async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
            original = sample_result()
            view = replace(original, run=replace(original.run, status="failed", end_reason=reason))
            app.push_screen(ResultScreen(app.services, app.catalog, view))
            await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
            await settle(app, pilot)
            assert text in render(app.screen.query_one("#result-stop", Static))

        return scenario

    drive(make_app(), scenario_for(expected), size=(120, 40))
    drive(make_app(language="es"), scenario_for(spanish), size=(120, 40))
