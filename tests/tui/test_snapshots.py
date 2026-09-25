from __future__ import annotations

from collections.abc import Callable

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, Checkbox, DataTable, TabbedContent, TextArea

from cuanta.application.mandate_flow import MandateOptions
from cuanta.domain.mandate import MandateRequest
from cuanta.tui.app import CuantaApp
from cuanta.tui.screens.bench import BenchScreen
from cuanta.tui.screens.capsule import CapsuleScreen
from cuanta.tui.screens.confirm import ConfirmScreen
from cuanta.tui.screens.consent import ConsentScreen
from cuanta.tui.screens.diff import DiffScreen
from cuanta.tui.screens.onboarding import HelpScreen, OnboardingScreen
from cuanta.tui.screens.pipeline import PipelineScreen
from cuanta.tui.screens.result import ResultScreen
from cuanta.tui.views.init import InitView
from cuanta.tui.views.ledger import LedgerView
from cuanta.tui.views.spectrum import SpectrumView
from cuanta.tui.views.tests import TestsView
from cuanta.tui.widgets.wizard import MandateWizard
from tests.tui.fakes import (
    AGENTS,
    RED,
    FakeServices,
    pipeline_events,
    single_context_events,
    snapshot,
)
from tests.tui.test_app import settle
from tests.tui.test_onboarding import sample_bench
from tests.tui.test_wizard import STORY

SnapCompare = Callable[..., bool]
MODERN = {"WT_SESSION": "1"}
THEMES = ("calico-dark", "calico-light")
SIZES = ((120, 36), (80, 24))


async def loaded(pilot: Pilot[None]) -> None:
    app = pilot.app
    assert isinstance(app, CuantaApp)
    await settle(app, pilot)


def app_for(theme: str, services: FakeServices | None = None) -> CuantaApp:
    return CuantaApp(
        services or FakeServices(),
        "en",
        theme,
        motion=False,
        environ=MODERN,
        clock=lambda: 0.0,
    )


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
def test_home(snap_compare: SnapCompare, theme: str, size: tuple[int, int]) -> None:
    assert snap_compare(app_for(theme), terminal_size=size, run_before=loaded)


@pytest.mark.parametrize("theme", THEMES)
def test_home_fresh_project(snap_compare: SnapCompare, theme: str) -> None:
    services = FakeServices(snapshot(initialized=False, runs=()))
    assert snap_compare(app_for(theme, services), terminal_size=(120, 36), run_before=loaded)


@pytest.mark.parametrize("theme", THEMES)
def test_pending_section(snap_compare: SnapCompare, theme: str) -> None:
    assert snap_compare(app_for(theme), press=["4"], terminal_size=(120, 36), run_before=loaded)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
def test_tests_red(snap_compare: SnapCompare, theme: str, size: tuple[int, int]) -> None:
    app = app_for(theme, FakeServices(latest=RED))
    assert snap_compare(app, press=["2"], terminal_size=size, run_before=loaded)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
def test_capsule_viewer(snap_compare: SnapCompare, theme: str, size: tuple[int, int]) -> None:
    async def open_capsule(pilot: Pilot[None]) -> None:
        await loaded(pilot)
        await pilot.press("2")
        await loaded(pilot)
        view = pilot.app.query_one("#tests")
        assert isinstance(view, TestsView)
        view.post_message(TestsView.OpenCapsule(RED, RED.hairballs[0]))
        for _ in range(100):
            screen = pilot.app.screen
            if isinstance(screen, CapsuleScreen) and len(screen.views) == 3:
                break
            await pilot.pause(0.02)
        await loaded(pilot)

    assert snap_compare(app_for(theme), terminal_size=size, run_before=open_capsule)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
def test_health(snap_compare: SnapCompare, theme: str, size: tuple[int, int]) -> None:
    assert snap_compare(app_for(theme), press=["7"], terminal_size=size, run_before=loaded)


async def understood(pilot: Pilot[None], step: int = 1) -> MandateWizard:
    await loaded(pilot)
    await pilot.press("3")
    await loaded(pilot)
    wizard = pilot.app.query_one(MandateWizard)
    for _ in range(200):
        if wizard.engine:
            break
        await pilot.pause(0.02)
    if step == 0:
        return wizard
    wizard.query_one("#wiz-story", TextArea).text = STORY
    wizard.understand_now()
    for _ in range(300):
        if wizard.understanding is not None:
            break
        await pilot.pause(0.02)
    await loaded(pilot)
    if step == 2 or wizard.one_page:
        if not wizard.one_page:
            wizard.go(2)
        for _ in range(300):
            if wizard.query("#override-analyst") and wizard.estimate is not None:
                break
            await pilot.pause(0.02)
    await loaded(pilot)
    await pilot.pause(0.2)
    wizard.query_one("#wizard-body").scroll_home(animate=False)
    await loaded(pilot)
    return wizard


async def filled_form(pilot: Pilot[None]) -> None:
    await understood(pilot)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
def test_mandate_form(snap_compare: SnapCompare, theme: str, size: tuple[int, int]) -> None:
    app = app_for(theme, FakeServices(layout="one_page"))
    assert snap_compare(app, terminal_size=size, run_before=filled_form)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
def test_pipeline_finished(snap_compare: SnapCompare, theme: str, size: tuple[int, int]) -> None:
    async def run_mandate(pilot: Pilot[None]) -> None:
        await loaded(pilot)
        app = pilot.app
        assert isinstance(app, CuantaApp)
        app.push_screen(
            PipelineScreen(
                app.services,
                app.catalog,
                MandateRequest(
                    type="investigation", what="Check the cart", why="How does it work?"
                ),
                0,
                MandateOptions(),
                app.clock,
            )
        )
        await wait_screen(pilot, ResultScreen)
        await pilot.press("escape")
        for _ in range(200):
            screen = pilot.app.screen
            if isinstance(screen, PipelineScreen) and screen.report is not None:
                break
            await pilot.pause(0.02)
        await loaded(pilot)

    app = app_for(theme, FakeServices(events=single_context_events()))
    assert snap_compare(app, terminal_size=size, run_before=run_mandate)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
def test_pipeline_delegated_investigation(
    snap_compare: SnapCompare, theme: str, size: tuple[int, int]
) -> None:
    async def run_mandate(pilot: Pilot[None]) -> None:
        await loaded(pilot)
        app = pilot.app
        assert isinstance(app, CuantaApp)
        app.push_screen(
            PipelineScreen(
                app.services,
                app.catalog,
                MandateRequest(
                    type="investigation", what="Check the cart", why="How does it work?"
                ),
                0,
                MandateOptions(shape="pipeline"),
                app.clock,
            )
        )
        await wait_screen(pilot, ResultScreen)
        await pilot.press("escape")
        await wait_screen(pilot, PipelineScreen)

    services = FakeServices(events=pipeline_events((AGENTS[0],)))
    assert snap_compare(app_for(theme, services), terminal_size=size, run_before=run_mandate)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
def test_pipeline_feature(snap_compare: SnapCompare, theme: str, size: tuple[int, int]) -> None:
    async def run_mandate(pilot: Pilot[None]) -> None:
        await loaded(pilot)
        app = pilot.app
        assert isinstance(app, CuantaApp)
        app.push_screen(
            PipelineScreen(
                app.services,
                app.catalog,
                MandateRequest(type="feature", what="Add cart totals", why="Show the total"),
                0,
                MandateOptions(shape="pipeline"),
                app.clock,
            )
        )
        await wait_screen(pilot, ResultScreen)
        await pilot.press("escape")
        await wait_screen(pilot, PipelineScreen)

    assert snap_compare(app_for(theme), terminal_size=size, run_before=run_mandate)


async def spectrum_loaded(pilot: Pilot[None]) -> None:
    await loaded(pilot)
    await pilot.press("4")
    view = pilot.app.query_one(SpectrumView)
    for _ in range(200):
        if view.result is not None:
            break
        await pilot.pause(0.02)
    await loaded(pilot)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
def test_spectrum(snap_compare: SnapCompare, theme: str, size: tuple[int, int]) -> None:
    assert snap_compare(app_for(theme), terminal_size=size, run_before=spectrum_loaded)


@pytest.mark.parametrize("theme", THEMES)
def test_spectrum_leaks(snap_compare: SnapCompare, theme: str) -> None:
    async def leaks(pilot: Pilot[None]) -> None:
        await spectrum_loaded(pilot)
        view = pilot.app.query_one(SpectrumView)
        view.query_one("#spectrum-tabs", TabbedContent).active = "tab-leaks"
        view.scroll_end(animate=False)
        await loaded(pilot)

    assert snap_compare(app_for(theme), terminal_size=(120, 36), run_before=leaks)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
def test_ledger(snap_compare: SnapCompare, theme: str, size: tuple[int, int]) -> None:
    async def ledger(pilot: Pilot[None]) -> None:
        await loaded(pilot)
        await pilot.press("5")
        view = pilot.app.query_one(LedgerView)
        for _ in range(200):
            if view.runs:
                break
            await pilot.pause(0.02)
        view.query_one("#ledger-runs", DataTable).focus()
        await pilot.press("down")
        await loaded(pilot)

    assert snap_compare(app_for(theme), terminal_size=size, run_before=ledger)


async def init_finished(pilot: Pilot[None]) -> None:
    await loaded(pilot)
    await pilot.press("i")
    await loaded(pilot)
    view = pilot.app.query_one(InitView)
    view.query_one("#init-telemetry", Checkbox).value = False
    view.query_one("#init-start", Button).press()
    for _ in range(200):
        if view.report is not None:
            break
        await pilot.pause(0.02)
    await loaded(pilot)
    pilot.app.set_focus(None)
    await loaded(pilot)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
def test_init_results(snap_compare: SnapCompare, theme: str, size: tuple[int, int]) -> None:
    assert snap_compare(app_for(theme), terminal_size=size, run_before=init_finished)


@pytest.mark.parametrize("theme", THEMES)
def test_consent_modal(snap_compare: SnapCompare, theme: str) -> None:
    async def consent(pilot: Pilot[None]) -> None:
        await loaded(pilot)
        await pilot.press("i")
        await loaded(pilot)
        pilot.app.query_one("#init-start", Button).press()
        for _ in range(200):
            if isinstance(pilot.app.screen, ConsentScreen):
                break
            await pilot.pause(0.02)
        await loaded(pilot)

    assert snap_compare(app_for(theme), terminal_size=(120, 36), run_before=consent)


@pytest.mark.parametrize("theme", THEMES)
def test_diff_screen(snap_compare: SnapCompare, theme: str) -> None:
    async def diff(pilot: Pilot[None]) -> None:
        await init_finished(pilot)
        view = pilot.app.query_one(InitView)
        view.post_message(InitView.OpenNewFile(".claude/agents/tester.new.md"))
        for _ in range(200):
            screen = pilot.app.screen
            if isinstance(screen, DiffScreen) and screen.pair is not None:
                break
            await pilot.pause(0.02)
        await loaded(pilot)

    assert snap_compare(app_for(theme), terminal_size=(120, 36), run_before=diff)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("key", ["6", "8"], ids=["instinct", "settings"])
def test_instinct_and_settings(snap_compare: SnapCompare, theme: str, key: str) -> None:
    async def section(pilot: Pilot[None]) -> None:
        await loaded(pilot)
        await pilot.press(key)
        await loaded(pilot)
        await pilot.pause(0.2)
        await loaded(pilot)

    assert snap_compare(app_for(theme), terminal_size=(120, 36), run_before=section)


@pytest.mark.parametrize("theme", THEMES)
def test_loop_gated(snap_compare: SnapCompare, theme: str) -> None:
    async def gated(pilot: Pilot[None]) -> None:
        await loaded(pilot)
        app = pilot.app
        assert isinstance(app, CuantaApp)
        await app.action_goto("loop")
        await loaded(pilot)
        await pilot.pause(0.2)
        await loaded(pilot)

    services = FakeServices(gate_open=False)
    assert snap_compare(app_for(theme, services), terminal_size=(120, 36), run_before=gated)


def test_home_in_spanish(snap_compare: SnapCompare) -> None:
    app = CuantaApp(
        FakeServices(), "es", "calico-dark", motion=False, environ=MODERN, clock=lambda: 0.0
    )
    assert snap_compare(app, terminal_size=(120, 36), run_before=loaded)


@pytest.mark.parametrize("theme", THEMES)
def test_home_on_the_terminal_background(snap_compare: SnapCompare, theme: str) -> None:
    app = app_for(theme, FakeServices(background="terminal"))
    assert snap_compare(app, terminal_size=(120, 36), run_before=loaded)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
@pytest.mark.parametrize("key", ["9", "6"], ids=["models", "instinct"])
def test_models_and_instinct(
    snap_compare: SnapCompare, theme: str, size: tuple[int, int], key: str
) -> None:
    async def section(pilot: Pilot[None]) -> None:
        await loaded(pilot)
        await pilot.press(key)
        await loaded(pilot)
        await pilot.pause(0.2)
        await loaded(pilot)

    assert snap_compare(app_for(theme), terminal_size=size, run_before=section)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
@pytest.mark.parametrize("step", ["tell", "confirm", "team"])
def test_mandate_wizard(
    snap_compare: SnapCompare, theme: str, size: tuple[int, int], step: str
) -> None:
    async def wizard(pilot: Pilot[None]) -> None:
        await understood(pilot, ("tell", "confirm", "team").index(step))

    app = app_for(theme, FakeServices())
    assert snap_compare(app, terminal_size=size, run_before=wizard)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
def test_onboarding(snap_compare: SnapCompare, theme: str, size: tuple[int, int]) -> None:
    async def welcome(pilot: Pilot[None]) -> None:
        await loaded(pilot)
        for _ in range(100):
            if isinstance(pilot.app.screen, OnboardingScreen):
                break
            await pilot.pause(0.02)
        await loaded(pilot)

    app = app_for(theme, FakeServices(onboarded=False))
    assert snap_compare(app, terminal_size=size, run_before=welcome)


@pytest.mark.parametrize("theme", THEMES)
def test_help_screen(snap_compare: SnapCompare, theme: str) -> None:
    async def help_open(pilot: Pilot[None]) -> None:
        await loaded(pilot)
        await pilot.press("question_mark")
        for _ in range(100):
            if isinstance(pilot.app.screen, HelpScreen):
                break
            await pilot.pause(0.02)
        await loaded(pilot)

    assert snap_compare(app_for(theme), terminal_size=(120, 36), run_before=help_open)


@pytest.mark.parametrize("theme", THEMES)
def test_bench_screen(snap_compare: SnapCompare, theme: str) -> None:
    async def bench_open(pilot: Pilot[None]) -> None:
        await loaded(pilot)
        app = pilot.app
        assert isinstance(app, CuantaApp)
        app.action_bench()
        for _ in range(100):
            if isinstance(pilot.app.screen, BenchScreen):
                break
            await pilot.pause(0.02)
        await loaded(pilot)

    services = FakeServices(bench=sample_bench())
    assert snap_compare(app_for(theme, services), terminal_size=(120, 36), run_before=bench_open)


async def wait_screen(pilot: Pilot[None], kind: type[object]) -> None:
    for _ in range(200):
        if isinstance(pilot.app.screen, kind):
            break
        await pilot.pause(0.02)
    await loaded(pilot)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
def test_result_screen(snap_compare: SnapCompare, theme: str, size: tuple[int, int]) -> None:
    async def result_open(pilot: Pilot[None]) -> None:
        await loaded(pilot)
        app = pilot.app
        assert isinstance(app, CuantaApp)
        app.open_result("01JMANDATE0000000000000RUN1")
        await wait_screen(pilot, ResultScreen)

    assert snap_compare(app_for(theme), terminal_size=size, run_before=result_open)


@pytest.mark.parametrize("theme", THEMES)
def test_wizard_forge_gate(snap_compare: SnapCompare, theme: str) -> None:
    async def gate(pilot: Pilot[None]) -> None:
        await loaded(pilot)
        await pilot.press("3")
        await loaded(pilot)

    services = FakeServices(forge_ready=False)
    assert snap_compare(app_for(theme, services), terminal_size=(120, 36), run_before=gate)


@pytest.mark.parametrize("theme", THEMES)
def test_no_cap_confirmation(snap_compare: SnapCompare, theme: str) -> None:
    async def check(pilot: Pilot[None]) -> None:
        await loaded(pilot)
        app = pilot.app
        assert isinstance(app, CuantaApp)
        app.push_screen(
            ConfirmScreen(
                app.catalog,
                "wizard.no_cap_title",
                "wizard.no_cap_body",
                "wizard.no_cap_confirm",
                "wizard.no_cap_cancel",
            )
        )
        await wait_screen(pilot, ConfirmScreen)

    assert snap_compare(app_for(theme), terminal_size=(120, 36), run_before=check)
