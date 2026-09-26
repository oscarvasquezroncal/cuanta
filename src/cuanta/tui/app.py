from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Callable, Iterable
from contextlib import suppress
from functools import partial
from pathlib import Path

from textual import events, work
from textual.app import App, ComposeResult, SystemCommand
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Button, ContentSwitcher, Footer

from cuanta.application.home import HomeSnapshot
from cuanta.application.tests_view import Hairball
from cuanta.domain.fixes import Fix, FixAction, FixKind
from cuanta.domain.fixes import classify as classify_fix
from cuanta.domain.progress import Status
from cuanta.domain.routing import Preset
from cuanta.domain.terminal import TerminalProbe, TerminalReport, classify
from cuanta.tui import theme as themes
from cuanta.tui.commands import (
    CLI_EQUIVALENT,
    COMMANDS,
    HEALTH,
    HOME,
    INIT,
    INSTINCT,
    LEDGER,
    LOOP,
    MANDATES,
    MODELS,
    SECTIONS,
    SETTINGS,
    SIDEBAR,
    SPECTRUM,
    TESTS,
)
from cuanta.tui.i18n import Catalog
from cuanta.tui.screens.attach import AttachScreen
from cuanta.tui.screens.bench import BenchScreen
from cuanta.tui.screens.capsule import CapsuleScreen
from cuanta.tui.screens.consent import ConsentScreen
from cuanta.tui.screens.diff import DiffScreen
from cuanta.tui.screens.export import ExportScreen
from cuanta.tui.screens.onboarding import ENGINES as ONBOARDING_ENGINES
from cuanta.tui.screens.onboarding import HelpScreen, OnboardingScreen
from cuanta.tui.screens.pipeline import PipelineScreen
from cuanta.tui.screens.result import ResultScreen
from cuanta.tui.services import ContainerServices, Services
from cuanta.tui.views.health import FixRequested, HealthView
from cuanta.tui.views.home import HomeView
from cuanta.tui.views.init import InitView
from cuanta.tui.views.instinct import InstinctView
from cuanta.tui.views.ledger import LedgerView
from cuanta.tui.views.loop import LoopView
from cuanta.tui.views.mandate import MandateView
from cuanta.tui.views.models import ModelsView
from cuanta.tui.views.pending import PendingView
from cuanta.tui.views.settings import SettingsView
from cuanta.tui.views.spectrum import SpectrumView
from cuanta.tui.views.tests import TestsView
from cuanta.tui.widgets.header import AppHeader, EngineChips, ListenerChip
from cuanta.tui.widgets.sidebar import NavItem, Sidebar
from cuanta.tui.widgets.tip import LegacyTip

NAV_KEYS = dict(zip(SIDEBAR, ("1", "2", "3", "4", "5", "6", "7", "8", "9"), strict=True))


LISTENER_AUTO = "auto"


class CuantaApp(App[None]):
    CSS_PATH = "cuanta.tcss"
    TITLE = "cuanta"
    HORIZONTAL_BREAKPOINTS = [
        (0, "-single"),
        (80, "-icons"),
        (100, "-full"),
    ]
    VERTICAL_BREAKPOINTS = [(0, "-short"), (30, "-tall")]

    def __init__(
        self,
        services: Services,
        language: str = "en",
        theme_choice: str = themes.AUTO,
        motion: bool = True,
        environ: dict[str, str] | None = None,
        platform: str = "",
        clock: Callable[[], float] = time.monotonic,
        terminal: TerminalReport | None = None,
        open_run: str = "",
    ) -> None:
        super().__init__()
        self.open_run = open_run
        self.clock = clock
        self.services = services
        self.catalog = Catalog(language)
        self.motion = motion
        self.section = HOME
        self.snapshot: HomeSnapshot | None = None
        self.listener_owned = False
        self._listener_checked = False
        self._onboarding_checked = False
        self.mandate_prefill: Hairball | None = None
        self.home_loaded = False
        self._mounting = asyncio.Lock()
        self.closing = False
        self._environ = dict(os.environ) if environ is None else environ
        self._platform = platform or sys.platform
        self.terminal = terminal or classify(TerminalProbe(self._platform), self._environ)
        self.legacy = self.terminal.legacy
        self.settings = services.settings()
        self.transparent = False
        self.apply_background(self.settings.background)
        for calico in themes.calico_themes():
            self.register_theme(calico)
        self.theme = themes.resolve(theme_choice, self.legacy, self._environ)
        t = self.catalog
        self.bind("ctrl+p", "command_palette", description=t("keys.palette"))
        for section, key in NAV_KEYS.items():
            self.bind(key, f"goto('{section}')", description=t(f"nav.{section}"), show=False)
        for command in COMMANDS:
            if command.key:
                self.bind(command.key, command.action, description=t(command.title_key), show=False)
        self.bind("ctrl+q", "quit", description=t("keys.quit"))
        self.bind("question_mark", "help", description=t("keys.help"), show=False)

    def get_default_screen(self) -> Screen[object]:
        screen = super().get_default_screen()
        for dimension, breakpoints in (
            (self.size.width, self.HORIZONTAL_BREAKPOINTS),
            (self.size.height, self.VERTICAL_BREAKPOINTS),
        ):
            for boundary, name in sorted(breakpoints, reverse=True):
                if dimension >= boundary:
                    screen.add_class(name, update=False)
                    break
        return screen

    def compose(self) -> ComposeResult:
        yield AppHeader(self.catalog, self.motion)
        if self.legacy and not self.settings.terminal_tip_dismissed:
            yield LegacyTip(self.catalog)
        with Horizontal(id="body"):
            yield Sidebar(self.catalog)
            with ContentSwitcher(initial=HOME, id="main"):
                yield HomeView(self.catalog, self.motion)
        yield Footer(show_command_palette=False)

    def build_view(self, section: str) -> Widget:
        factories: dict[str, Callable[[], Widget]] = {
            TESTS: lambda: TestsView(self.services, self.catalog),
            HEALTH: lambda: HealthView(self.services, self.catalog),
            MANDATES: lambda: MandateView(self.services, self.catalog),
            SPECTRUM: lambda: SpectrumView(self.services, self.catalog),
            LEDGER: lambda: LedgerView(self.services, self.catalog),
            INIT: lambda: InitView(self.services, self.catalog),
            INSTINCT: lambda: InstinctView(self.services, self.catalog),
            SETTINGS: lambda: SettingsView(self.services, self.catalog),
            MODELS: lambda: ModelsView(self.services, self.catalog),
            LOOP: lambda: LoopView(self.services, self.catalog),
        }
        factory = factories.get(section)
        return factory() if factory is not None else PendingView(section, self.catalog)

    @property
    def base(self) -> Screen[object]:
        if not self.screen_stack:
            raise NoMatches("the app has no screen")
        return self.screen_stack[0]

    async def ensure_view(self, section: str) -> None:
        async with self._mounting:
            if self.closing:
                return
            switcher = self.base.query_one("#main", ContentSwitcher)
            if switcher.query(f"#{section}"):
                return
            view = self.build_view(section)
            await switcher.mount(view)
            if isinstance(view, HealthView) and self.snapshot is not None:
                view.show(self.snapshot.report)
            if isinstance(view, InitView) and self.snapshot is not None:
                view.set_refresh(self.snapshot.initialized)

    async def _shutdown(self) -> None:
        self.closing = True
        async with self._mounting:
            pass
        self.workers.cancel_all()
        await super()._shutdown()

    async def on_event(self, event: events.Event) -> None:
        if self.closing and isinstance(event, events.InputEvent):
            return
        await super().on_event(event)

    def _auto_listener(self, snapshot: HomeSnapshot) -> None:
        if self._listener_checked:
            return
        self._listener_checked = True
        if self.settings.listener_mode != LISTENER_AUTO:
            return
        listener = snapshot.check("listener")
        wired = any(
            check.name.startswith("telemetry ") and check.status is Status.OK
            for check in snapshot.report.checks
        )
        if wired and listener is not None and listener.status is not Status.OK:
            self.toggle_listener(False, owned=True)

    async def on_engine_chips_opened(self, message: EngineChips.Opened) -> None:
        await self.action_goto(MODELS)

    def on_listener_chip_toggled(self, message: ListenerChip.Toggled) -> None:
        self.toggle_listener(message.running)

    @work(thread=True, exclusive=True, group="listener", exit_on_error=False)
    def toggle_listener(self, running: bool, owned: bool = False) -> None:
        try:
            if running:
                self.services.listener_stop()
                self.listener_owned = False
            else:
                self.services.listener_start()
                self.listener_owned = owned or self.listener_owned
        except Exception as error:
            self.call_from_thread(self.notify, str(error), severity="error")
            return
        self.call_from_thread(self.load_home)

    def apply_background(self, choice: object) -> None:
        self.transparent = choice == themes.BACKGROUND_TERMINAL
        self.ansi_color = True if self.transparent else None
        self.set_class(self.transparent, "-transparent")

    def on_settings_view_saved(self, message: SettingsView.Saved) -> None:
        self.apply_background(message.values.get("ui.background"))

    def on_mount(self) -> None:
        self.base.query_one(Sidebar).mark(HOME)
        self.load_home()

    @work(thread=True, exclusive=True, group="home", exit_on_error=False)
    def load_home(self) -> None:
        try:
            snapshot = self.services.home()
        except Exception as error:
            self.call_from_thread(self._home_failed, str(error))
            return
        self.call_from_thread(self._show_home, snapshot)

    def _show_home(self, snapshot: HomeSnapshot) -> None:
        self.snapshot = snapshot
        if not self.screen_stack:
            return
        if not self._onboarding_checked:
            self._onboarding_checked = True
            if not self.settings.onboarded:
                self.action_onboarding()
        self._auto_listener(snapshot)
        if self.open_run:
            run_id, self.open_run = self.open_run, ""
            self.open_result(run_id)
        with suppress(NoMatches):
            self.base.query_one(AppHeader).show(snapshot)
            self.base.query_one(HomeView).show(snapshot)
            for health in self.base.query(HealthView):
                health.show(snapshot.report)
            for init in self.base.query(InitView):
                init.set_refresh(snapshot.initialized)
        self.home_loaded = True

    def _home_failed(self, error: str) -> None:
        self.home_loaded = True
        self.notify(self.catalog("app.load_failed", error=error), severity="error", timeout=10)

    async def action_goto(self, section: str) -> None:
        if section not in SECTIONS:
            return
        with suppress(NoMatches):
            await self.ensure_view(section)
            if not self.closing:
                self._switch(section)

    def _switch(self, section: str) -> None:
        self.section = section
        self.base.query_one("#main", ContentSwitcher).current = section
        self.base.query_one(Sidebar).mark(section)
        if isinstance(self.focused, NavItem) and section in SIDEBAR:
            self.base.query_one(f"#nav-{section}", NavItem).focus()
        if section == SPECTRUM:
            self.base.query_one(SpectrumView).activate()
        elif section == LEDGER:
            self.base.query_one(LedgerView).activate()
        if section == MANDATES and self.mandate_prefill is not None:
            self.base.query_one(MandateView).prefill(self.mandate_prefill)
            self.mandate_prefill = None

    def action_onboarding(self) -> None:
        engines = {}
        if self.snapshot is not None:
            for name in ONBOARDING_ENGINES:
                check = self.snapshot.check(f"engine {name}")
                engines[name] = check is not None and check.status is Status.OK
        screen = OnboardingScreen(self.catalog, engines, Preset.BALANCED.value)
        self.push_screen(screen, self._onboarded)

    def _onboarded(self, preset: str | None) -> None:
        self.finish_onboarding(preset)

    @work(thread=True, exit_on_error=False)
    def finish_onboarding(self, preset: str | None) -> None:
        try:
            self.services.save_setting("ui.onboarded", True)
            if preset:
                self.services.save_routing({"preset": preset})
        except Exception as error:
            self.call_from_thread(self.notify, str(error), severity="error")

    def action_help(self) -> None:
        if isinstance(self.screen, HelpScreen | OnboardingScreen):
            return
        command = CLI_EQUIVALENT.get(self.section, "cuanta --help")
        self.push_screen(HelpScreen(self.catalog, self.section, command))

    def action_bench(self) -> None:
        self.load_bench()

    @work(thread=True, exit_on_error=False)
    def load_bench(self) -> None:
        try:
            result = self.services.latest_bench()
        except Exception as error:
            self.call_from_thread(self.notify, str(error), severity="error")
            return
        self.call_from_thread(self.push_screen, BenchScreen(self.catalog, result))

    def action_reload(self) -> None:
        self.load_home()

    async def on_nav_item_chosen(self, message: NavItem.Chosen) -> None:
        await self.action_goto(message.section)

    async def on_home_view_go(self, message: HomeView.Go) -> None:
        if message.section == SPECTRUM and message.run_id:
            await self.open_spectrum(message.run_id)
        else:
            await self.action_goto(message.section)

    async def open_spectrum(self, run_id: str) -> None:
        await self.action_goto(SPECTRUM)
        self.base.query_one(SpectrumView).activate(run_id)

    async def action_import_sessions(self) -> None:
        await self.action_goto(SPECTRUM)
        self.base.query_one(SpectrumView).import_sessions()

    async def on_spectrum_view_leak_action(self, message: SpectrumView.LeakAction) -> None:
        if message.action == "gateway":
            await self.action_run_tests()
        elif message.action == "split":
            await self.action_goto(MANDATES)
        elif message.action == "reindex":
            self._reindex()

    @work(thread=True, group="graph", exit_on_error=False)
    def _reindex(self) -> None:
        try:
            ok, detail = self.services.reindex_graph()
        except Exception as error:
            ok, detail = False, str(error)
        key = "spectrum.reindexed" if ok else "spectrum.reindex_failed"
        severity = "information" if ok else "error"
        self.call_from_thread(self.notify, self.catalog(key, detail=detail), severity=severity)

    def on_ledger_view_export_requested(self, message: LedgerView.ExportRequested) -> None:
        fmt = message.fmt

        def chosen(result: tuple[str, str, bool] | None) -> None:
            if result is not None:
                self._export(fmt, result[0], result[1], result[2])

        self.push_screen(ExportScreen(self.catalog, fmt), chosen)

    @work(thread=True, group="export", exit_on_error=False)
    def _export(self, fmt: str, table: str, path: str, include_raw: bool) -> None:
        try:
            target, size = self.services.export_ledger(fmt, table, path, include_raw)
        except Exception as error:
            message = self.catalog("ledger.export_failed", error=str(error))
            self.call_from_thread(self.notify, message, severity="error")
            return
        message = self.catalog("ledger.exported", size=f"{size:,}", path=target)
        self.call_from_thread(self.notify, message)

    async def action_run_tests(self) -> None:
        await self.action_goto(TESTS)
        self.base.query_one(TestsView).run_tests()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id or ""
        if button == "action-tests":
            await self.action_run_tests()
        elif button.startswith("action-"):
            await self.action_goto(button.removeprefix("action-"))
        elif button == "next-run" and self.snapshot is not None:
            step = self.snapshot.next_step
            if not self.snapshot.initialized or step is None:
                await self.action_goto(INIT)
            else:
                await self.handle_fix(step.name, classify_fix(step.fix))

    def on_tests_view_open_capsule(self, message: TestsView.OpenCapsule) -> None:
        self.push_screen(
            CapsuleScreen(self.services, self.catalog, message.summary, message.hairball)
        )

    async def on_capsule_screen_fix_requested(self, message: CapsuleScreen.FixRequested) -> None:
        self.mandate_prefill = message.hairball
        await self.action_goto(MANDATES)

    def on_mandate_view_run_requested(self, message: MandateView.RunRequested) -> None:
        self.push_screen(
            PipelineScreen(
                self.services,
                self.catalog,
                message.request,
                message.signatures,
                message.options,
                self.clock,
            )
        )

    async def on_mandate_view_init_requested(self, message: MandateView.InitRequested) -> None:
        await self.action_goto(INIT)

    def on_mandate_view_attach_requested(self, message: MandateView.AttachRequested) -> None:
        def chosen(path: str | None) -> None:
            if path:
                self.base.query_one(MandateView).attach(path)

        self.push_screen(AttachScreen(self.catalog), chosen)

    async def on_pipeline_screen_open_spectrum(self, message: PipelineScreen.OpenSpectrum) -> None:
        await self.open_spectrum(message.run_id)

    def _to_base(self) -> None:
        while len(self.screen_stack) > 1:
            self.pop_screen()

    async def on_result_screen_open_spectrum(self, message: ResultScreen.OpenSpectrum) -> None:
        self._to_base()
        await self.open_spectrum(message.run_id)

    async def on_result_screen_continue_requested(
        self, message: ResultScreen.ContinueRequested
    ) -> None:
        self._to_base()
        await self.action_goto(MANDATES)
        self.base.query_one(MandateView).continue_with(message.request)

    def on_ledger_view_result_requested(self, message: LedgerView.ResultRequested) -> None:
        self.open_result(message.run_id)

    @work(thread=True, exclusive=True, group="open-result", exit_on_error=False)
    def open_result(self, run_id: str) -> None:
        try:
            view = self.services.result_view(run_id)
        except Exception as error:
            self.call_from_thread(self.notify, str(error), severity="error")
            return
        if view is None:
            self.call_from_thread(self.notify, self.catalog("result.none"), severity="warning")
            return
        self.call_from_thread(self.push_screen, ResultScreen(self.services, self.catalog, view))

    def on_init_view_consent_needed(self, message: InitView.ConsentNeeded) -> None:
        view = self.base.query_one(InitView)
        self.push_screen(ConsentScreen(self.catalog, message.plans), view.consent_answer)

    def on_init_view_open_new_file(self, message: InitView.OpenNewFile) -> None:
        self.push_screen(DiffScreen(self.services, self.catalog, message.path))

    def on_diff_screen_resolved(self, message: DiffScreen.Resolved) -> None:
        for view in self.base.query(InitView):
            view.resolved(message.new_path)

    def on_legacy_tip_dismissed(self, message: LegacyTip.Dismissed) -> None:
        if message.forever:
            self._remember_tip()

    @work(thread=True, group="settings", exit_on_error=False)
    def _remember_tip(self) -> None:
        self.services.save_setting("terminal.tip_dismissed", True)

    async def on_fix_requested(self, message: FixRequested) -> None:
        await self.handle_fix(message.name, message.fix)

    async def handle_fix(self, name: str, fix: Fix) -> None:
        if fix.kind is FixKind.OPEN:
            await self.action_goto(INIT)
        elif fix.action is FixAction.TESTS:
            await self.action_run_tests()
        elif fix.kind is FixKind.RUN:
            self._apply_fix(name, fix)
        else:
            self.copy_command(fix.command)

    def copy_command(self, command: str) -> None:
        self.copy_to_clipboard(command)
        native = self.services.copy(command)
        if self.legacy and not native:
            self.notify(self.catalog("health.copy_failed"), severity="warning")
        else:
            self.notify(self.catalog("health.copied", command=command))

    @work(thread=True, group="fix", exit_on_error=False)
    def _apply_fix(self, name: str, fix: Fix) -> None:
        try:
            result = self.services.apply_fix(fix)
        except Exception as error:
            self.call_from_thread(
                self.notify,
                self.catalog("health.fix_failed", name=name, error=str(error)),
                severity="error",
            )
            return
        self.call_from_thread(self.notify, self.catalog("health.fixed", name=name, result=result))
        self.call_from_thread(self._refresh_after_fix)

    def _refresh_after_fix(self) -> None:
        for health in self.base.query(HealthView):
            health.refresh_checks()
        self.load_home()

    def get_system_commands(self, screen: Screen[object]) -> Iterable[SystemCommand]:
        t = self.catalog
        for command in COMMANDS:
            yield SystemCommand(
                t(command.title_key), t(command.help_key), partial(self.run_command, command.action)
            )
        yield SystemCommand(t("palette.theme"), t("palette.theme_help"), self.action_change_theme)
        yield SystemCommand(t("keys.quit"), t("palette.quit_help"), self.action_quit)

    def run_command(self, action: str) -> None:
        self.call_later(self.run_action, action)


def run(
    project: Path,
    language: str,
    theme_choice: str = themes.AUTO,
    motion: bool = True,
    open_run: str = "",
) -> None:
    services = ContainerServices(project)
    app = CuantaApp(
        services,
        language,
        theme_choice,
        motion,
        terminal=services.terminal_report(),
        open_run=open_run,
    )
    try:
        app.run()
    finally:
        if app.listener_owned:
            with suppress(Exception):
                services.listener_stop()
