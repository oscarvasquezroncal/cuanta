from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import CodeType

from textual.pilot import Pilot
from textual.widgets import Button, Checkbox, DataTable, Input, RadioButton, Select, Static

from cuanta.domain.progress import Status
from cuanta.tui.app import CuantaApp
from cuanta.tui.screens.consent import ConsentScreen
from cuanta.tui.screens.diff import DiffScreen
from cuanta.tui.views.init import InitView
from cuanta.tui.views.instinct import InstinctView
from cuanta.tui.views.loop import LoopView
from cuanta.tui.views.settings import SettingsView
from cuanta.tui.widgets.telemetry import TelemetryCard
from tests.tui.fakes import FakeServices
from tests.tui.test_app import drive, make_app, settle


def notes(app: CuantaApp) -> list[str]:
    return [str(note.message) for note in app._notifications]


def render(widget: Static) -> str:
    return str(widget.render())


WAIT_STEP_S = 0.02


def describe(check: Callable[[], object]) -> str:
    code = getattr(check, "__code__", None)
    if isinstance(code, CodeType):
        return f"{code.co_qualname} at {Path(code.co_filename).name}:{code.co_firstlineno}"
    return repr(check)


async def wait_for(pilot: Pilot[None], check: Callable[[], object], attempts: int = 500) -> None:
    for _ in range(attempts):
        if check():
            return
        await pilot.pause(WAIT_STEP_S)
    if not check():
        raise AssertionError(
            f"condition {describe(check)} did not hold after {attempts} x {WAIT_STEP_S}s"
        )


async def answer_consent(app: CuantaApp, pilot: Pilot[None], button: str) -> None:
    await wait_for(pilot, lambda: isinstance(app.screen, ConsentScreen))
    await pilot.pause()
    app.screen.query_one(f"#consent-{button}", Button).press()
    await pilot.pause()


async def open_init(app: CuantaApp, pilot: Pilot[None]) -> InitView:
    await pilot.press("i")
    await settle(app, pilot)
    return app.query_one(InitView)


def test_consent_modal_lists_files_and_backups_then_initializes() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        view = await open_init(app, pilot)
        view.query_one("#init-budget", Input).value = "2.5"
        await pilot.click("#init-start")
        await wait_for(pilot, lambda: isinstance(app.screen, ConsentScreen))
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ConsentScreen)
        files = render(screen.query_one("#consent-files", Static))
        assert "/work/shop/.claude/settings.local.json" in files
        assert "/work/shop/.cuanta/backups/claude-settings.local.json.bak" in files
        await answer_consent(app, pilot, "allow")
        await wait_for(pilot, lambda: view.report is not None)
        await settle(app, pilot)
        options, consent, budget = services.init_calls[0]
        assert (options.dry_run, consent, budget) == (False, True, 2.5)
        assert all(status is Status.OK for status, _ in view.stage_status.values())
        assert "run FORGE" in render(view.query_one("#stage-forge", Static))
        summary = render(view.query_one("#init-summary", Static))
        assert "ok · Skill tool allowed" in summary
        assert "four agents present" in summary
        assert view.query_one("#init-new-files", DataTable).row_count == 1
        assert "Initialize finished: all verify checks passed" in notes(app)

    drive(make_app(services), scenario)


def test_denying_consent_initializes_without_telemetry_and_cancel_does_nothing() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        view = await open_init(app, pilot)
        await pilot.click("#init-start")
        await answer_consent(app, pilot, "cancel")
        await settle(app, pilot)
        assert services.init_calls == []
        await pilot.click("#init-start")
        await answer_consent(app, pilot, "deny")
        await wait_for(pilot, lambda: view.report is not None)
        assert services.init_calls[0][1] is False
        assert view.stage_status["telemetry"][0] is Status.SKIP

    drive(make_app(services), scenario)


def test_dry_run_skips_consent_and_lists_planned_changes() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        view = await open_init(app, pilot)
        view.query_one("#init-dry", Checkbox).value = True
        await pilot.click("#init-start")
        await wait_for(pilot, lambda: view.report is not None)
        assert not isinstance(app.screen, ConsentScreen)
        assert services.init_calls[0][0].dry_run
        summary = render(view.query_one("#init-summary", Static))
        assert "write: CLAUDE.md" in summary
        await wait_for(pilot, lambda: "Dry run finished: nothing was written" in notes(app))

    drive(make_app(services), scenario)


def test_new_file_diff_keep_mine_and_use_new() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        view = await open_init(app, pilot)
        view.query_one("#init-telemetry", Checkbox).value = False
        await pilot.click("#init-start")
        await wait_for(pilot, lambda: view.report is not None)
        table = view.query_one("#init-new-files", DataTable)
        table.focus()
        await pilot.press("enter")
        await wait_for(pilot, lambda: isinstance(app.screen, DiffScreen))
        screen = app.screen
        assert isinstance(screen, DiffScreen)
        await wait_for(pilot, lambda: screen.pair is not None)
        assert "report hairballs" in render(screen.query_one("#diff-right", Static))
        assert "run pytest" in render(screen.query_one("#diff-left", Static))
        await pilot.click("#diff-use")
        await wait_for(pilot, lambda: not isinstance(app.screen, DiffScreen))
        await settle(app, pilot)
        assert services.resolved == [(".claude/agents/tester.new.md", False)]
        assert "Replaced .claude/agents/tester.md with the new version" in notes(app)
        assert not table.display

    drive(make_app(services), scenario)


def test_health_telemetry_switch_and_listener() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("7")
        await settle(app, pilot)
        card = app.query_one(TelemetryCard)
        await wait_for(pilot, lambda: card.panel is not None and card.query(".telemetry-toggle"))
        info = " ".join(render(item) for item in card.query(".telemetry-info").results(Static))
        assert "/work/shop/.claude/settings.local.json" in info
        assert "claude-settings.local.json.bak" in info
        assert "none (the file did not exist)" in info
        toggles = list(card.query(".telemetry-toggle").results(Button))
        assert toggles[1].disabled
        toggles[0].press()
        await wait_for(pilot, lambda: "claude" in services.telemetry_on)
        await settle(app, pilot)
        assert "claude: on" in notes(app)
        card.scroll_visible()
        await pilot.pause()
        app.query_one("#listener-toggle", Button).press()
        await wait_for(pilot, lambda: services.listener_running)
        await settle(app, pilot)
        assert "Listener started on port 4318" in notes(app)
        await wait_for(
            pilot, lambda: "running" in render(card.query_one("#listener-status", Static))
        )
        assert "1,234 events written" in render(card.query_one("#listener-status", Static))

    drive(make_app(services), scenario)


def test_instinct_requires_consent_for_remote_backends() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("6")
        await settle(app, pilot)
        view = app.query_one(InstinctView)
        await wait_for(pilot, lambda: bool(view.statuses))
        assert view.query_one("#backend-heuristic", RadioButton).value
        view.query_one("#backend-llm", RadioButton).value = True
        await pilot.pause()
        assert view.query_one("#instinct-consent", Checkbox).display
        view.query_one("#instinct-use", Button).press()
        await pilot.pause()
        assert "llm is remote: allow sending context first" in notes(app)
        assert services.backend == "heuristic"
        view.query_one("#instinct-consent", Checkbox).value = True
        view.query_one("#instinct-use", Button).press()
        await wait_for(pilot, lambda: services.backend == "llm")
        assert services.consented == {"llm"}
        view.query_one("#instinct-probe", Button).press()
        await wait_for(pilot, lambda: bool(view.query_one("#probe-card").display))
        assert view.query_one("#probe-table", DataTable).row_count == 1
        assert view.query_one("#decisions-table", DataTable).row_count == 1

    drive(make_app(services), scenario)


def test_settings_validate_and_save() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("8")
        await settle(app, pilot)
        view = app.query_one(SettingsView)
        await wait_for(pilot, lambda: view.query_one("#set-port", Input).value == "4318")
        assert view.query_one("#set-exclusions", Input).value == "dist"
        view.query_one("#set-port", Input).value = "80"
        view.query_one("#settings-save", Button).press()
        await pilot.pause()
        assert "between 1024" in render(view.query_one("#error-port", Static))
        assert services.saved == {}
        view.query_one("#set-port", Input).value = "4400"
        view.query_one("#set-exclusions", Input).value = "dist, build"
        view.query_one("#settings-save", Button).press()
        await wait_for(pilot, lambda: bool(services.saved))
        await settle(app, pilot)
        assert services.saved["listener.port"] == 4400
        assert services.saved["detect.exclude"] == ["dist", "build"]
        assert services.saved["budget.usd"] == 5.0
        assert "Settings saved to .cuanta/config.toml" in notes(app)

    drive(make_app(services), scenario)


def test_choosing_the_terminal_background_applies_it_at_once() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("8")
        await settle(app, pilot)
        view = app.query_one(SettingsView)
        await wait_for(pilot, lambda: view.query_one("#set-port", Input).value == "4318")
        assert not app.has_class("-transparent")
        view.query_one("#set-background", Select).value = "terminal"
        view.query_one("#settings-save", Button).press()
        await wait_for(pilot, lambda: app.has_class("-transparent"))
        assert services.saved["ui.background"] == "terminal"
        assert app.screen.styles.background.ansi == -1
        view.query_one("#set-background", Select).value = "solid"
        view.query_one("#settings-save", Button).press()
        await wait_for(pilot, lambda: not app.has_class("-transparent"))
        assert app.ansi_color is None

    drive(make_app(services), scenario)


def test_loop_explains_the_missing_sensor() -> None:
    services = FakeServices(gate_open=False)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await app.action_goto("loop")
        await settle(app, pilot)
        view = app.query_one(LoopView)
        await wait_for(pilot, lambda: view.state is not None)
        gate = render(view.query_one("#loop-gate", Static))
        assert "The loop is not available yet." in gate
        assert "VERIFY_TIER=moderate" in gate
        assert not view.query_one("#loop-run").display

    drive(make_app(services), scenario)


def test_loop_runs_when_sanctioned() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await app.action_goto("loop")
        await settle(app, pilot)
        view = app.query_one(LoopView)
        await wait_for(pilot, lambda: view.state is not None)
        assert view.query_one("#loop-run").display
        view.query_one("#loop-run", Button).press()
        await wait_for(pilot, lambda: services.loop_runs == 1 and not view.running)
        await settle(app, pilot)
        assert "Loop stopped: green" in render(view.query_one("#loop-summary", Static))

    drive(make_app(services), scenario)
