from __future__ import annotations

from textual.pilot import Pilot
from textual.widgets import Button, DataTable, RadioButton, Static

from cuanta.application.bench import BenchResult
from cuanta.domain.bench import BenchMeta, Condition, RunMetrics
from cuanta.tui.app import CuantaApp
from cuanta.tui.screens.bench import BenchScreen
from cuanta.tui.screens.onboarding import HelpScreen, OnboardingScreen
from cuanta.tui.widgets.header import EngineChips
from tests.tui.fakes import FakeServices
from tests.tui.test_app import drive, make_app, settle
from tests.tui.test_t5_screens import render, wait_for


def test_first_run_shows_onboarding_and_skip_remembers_it() -> None:
    services = FakeServices(onboarded=False)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await wait_for(pilot, lambda: isinstance(app.screen, OnboardingScreen))
        screen = app.screen
        assert isinstance(screen, OnboardingScreen)
        screen.query_one("#onboarding-skip", Button).press()
        await wait_for(pilot, lambda: services.saved.get("ui.onboarded") is True)
        assert not isinstance(app.screen, OnboardingScreen)
        assert services.routing_saved == {}

    drive(make_app(services), scenario)


def test_finishing_onboarding_saves_the_preset_and_it_reopens_from_the_palette() -> None:
    services = FakeServices(onboarded=False)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await wait_for(pilot, lambda: isinstance(app.screen, OnboardingScreen))
        screen = app.screen
        assert isinstance(screen, OnboardingScreen)
        next_button = screen.query_one("#onboarding-next", Button)
        next_button.press()
        await pilot.pause()
        assert "claude" in render(screen.query_one("#onboarding-engines", Static))
        next_button.press()
        await pilot.pause()
        screen.query_one("#preset-best", RadioButton).value = True
        await pilot.pause()
        assert str(next_button.label) == "Start"
        next_button.press()
        await wait_for(pilot, lambda: services.routing_saved == {"preset": "best"})
        assert services.saved.get("ui.onboarded") is True
        app.action_onboarding()
        await wait_for(pilot, lambda: isinstance(app.screen, OnboardingScreen))
        await settle(app, pilot)

    drive(make_app(services), scenario)


def test_question_mark_opens_help_for_the_current_screen() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("9")
        await settle(app, pilot)
        await pilot.press("question_mark")
        await wait_for(pilot, lambda: isinstance(app.screen, HelpScreen))
        screen = app.screen
        assert isinstance(screen, HelpScreen)
        assert screen.section == "models"
        assert "routing policy per role" in render(screen.query_one("#help-body", Static))
        await pilot.press("escape")
        await wait_for(pilot, lambda: not isinstance(app.screen, HelpScreen))

    drive(make_app(), scenario)


def test_engine_chips_open_the_models_screen() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        app.query_one(EngineChips).post_message(EngineChips.Opened())
        await wait_for(pilot, lambda: app.section == "models")

    drive(make_app(), scenario)


def sample_bench() -> BenchResult:
    meta = BenchMeta(
        "20260924T100000",
        "mini",
        1,
        7,
        "claude",
        "2.1.281",
        "sonnet",
        "2026-09-24T10:00:00",
        40.0,
        3.0,
        ("calc-add",),
    )
    runs = tuple(
        RunMetrics(
            "calc-add", condition, 1, "r", accepted, False, 900, 5000, 700, 300, cost, 30.0, 0, 0
        )
        for condition, accepted, cost in (
            (Condition.BASELINE, True, 0.21),
            (Condition.CUANTA, True, 0.18),
            (Condition.ROUTED, False, 0.12),
        )
    )
    return BenchResult(meta, runs, False, ".cuanta/bench/20260924T100000")


def test_bench_screen_shows_the_latest_results_read_only() -> None:
    services = FakeServices(bench=sample_bench())

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        app.action_bench()
        await wait_for(pilot, lambda: isinstance(app.screen, BenchScreen))
        screen = app.screen
        assert isinstance(screen, BenchScreen)
        await settle(app, pilot)
        assert "2.1.281" in render(screen.query_one("#bench-meta", Static))
        assert screen.query_one("#bench-summary", DataTable).row_count == 3
        assert screen.query_one("#bench-runs", DataTable).row_count == 3
        await pilot.press("escape")
        await wait_for(pilot, lambda: not isinstance(app.screen, BenchScreen))

    drive(make_app(services), scenario)


def test_bench_screen_without_results_points_to_the_cli() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        app.action_bench()
        await wait_for(pilot, lambda: isinstance(app.screen, BenchScreen))
        screen = app.screen
        assert isinstance(screen, BenchScreen)
        assert "No bench results" in render(screen.query_one("#bench-meta", Static))
        assert not screen.query(DataTable)

    drive(make_app(), scenario)
