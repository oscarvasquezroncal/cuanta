from __future__ import annotations

from contextlib import suppress

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.css.query import NoMatches
from textual.widgets import Button, Static

from cuanta.domain.progress import Status
from cuanta.domain.telemetry import WiringPlan, WiringReport, WiringState
from cuanta.tui.cells import labeled
from cuanta.tui.fmt import glyph, status_style
from cuanta.tui.i18n import Catalog
from cuanta.tui.services import Services, TelemetryPanel

POLL_S = 2.0
STATE_STATUS = {
    WiringState.ON: Status.OK,
    WiringState.OFF: Status.INFO,
    WiringState.OTHER: Status.WARN,
    WiringState.UNAVAILABLE: Status.SKIP,
}


def engine_block(report: WiringReport, plan: WiringPlan, catalog: Catalog) -> Content:
    status = STATE_STATUS[report.state]
    backup = plan.backup or catalog("telemetry.no_backup")
    head = Content.assemble(
        (f"{glyph(status)} ", status_style(status)),
        (report.engine, "bold"),
        (f"  {report.state.value}", "$text-muted"),
    )
    rows = [(catalog("telemetry.file"), plan.target), (catalog("telemetry.backup"), backup)]
    return Content("\n").join([head, labeled(rows, width=10)])


def states(panel: TelemetryPanel) -> tuple[tuple[str, WiringState], ...]:
    return tuple((report.engine, report.state) for report, _ in panel.engines)


class TelemetryCard(Vertical):
    def __init__(self, services: Services, catalog: Catalog) -> None:
        super().__init__(id="telemetry-card", classes="card")
        self._services = services
        self._t = catalog
        self.panel: TelemetryPanel | None = None

    def compose(self) -> ComposeResult:
        t = self._t
        yield Static(t("telemetry.title"), classes="card-title")
        yield Vertical(id="telemetry-engines")
        with Horizontal(id="listener-row"):
            yield Static("", id="listener-status")
            yield Button(t("telemetry.start"), id="listener-toggle", compact=True)

    def on_mount(self) -> None:
        self.refresh_panel()
        self.set_interval(POLL_S, self._poll)

    def _poll(self) -> None:
        if self.is_on_screen:
            self.refresh_panel()

    @work(thread=True, exclusive=True, group="telemetry-panel", exit_on_error=False)
    def refresh_panel(self) -> None:
        try:
            panel = self._services.telemetry_panel()
        except Exception:
            return
        self.app.call_from_thread(self.show, panel)

    def show(self, panel: TelemetryPanel) -> None:
        with suppress(NoMatches):
            self._show(panel)

    def _show(self, panel: TelemetryPanel) -> None:
        t = self._t
        before = self.panel
        changed = before is None or states(before) != states(panel)
        self.panel = panel
        if changed:
            box = self.query_one("#telemetry-engines", Vertical)
            box.remove_children()
            rows = []
            for report, plan in panel.engines:
                on = report.state is WiringState.ON
                row = Horizontal(
                    Static(engine_block(report, plan, t), classes="telemetry-info"),
                    Button(
                        t("telemetry.off" if on else "telemetry.on"),
                        name=report.engine,
                        classes="telemetry-toggle",
                        compact=True,
                        disabled=report.state is WiringState.UNAVAILABLE,
                    ),
                    classes="telemetry-row",
                )
                rows.append(row)
            box.mount_all(rows)
        state = (
            t("telemetry.listener_on", port=panel.port)
            if panel.listener_running
            else t("telemetry.listener_off")
        )
        status = Status.OK if panel.listener_running else Status.WARN
        self.query_one("#listener-status", Static).update(
            Content.assemble(
                (f"{glyph(status)} ", status_style(status)),
                (f"{t('telemetry.listener')}  ", "bold"),
                state,
                (f"   {t('telemetry.events', count=f'{panel.written:,}')}", "$text-muted"),
            )
        )
        toggle = self.query_one("#listener-toggle", Button)
        toggle.label = t("telemetry.stop" if panel.listener_running else "telemetry.start")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id or ""
        if button == "listener-toggle":
            event.stop()
            running = self.panel is not None and self.panel.listener_running
            self.toggle_listener(not running)
        elif event.button.has_class("telemetry-toggle") and self.panel is not None:
            event.stop()
            engine = event.button.name or ""
            current = next((r for r, _ in self.panel.engines if r.engine == engine), None)
            self.toggle_engine(engine, current is None or current.state is not WiringState.ON)

    @work(thread=True, exit_on_error=False)
    def toggle_engine(self, engine: str, on: bool) -> None:
        try:
            reports = self._services.set_telemetry(engine, on)
        except Exception as error:
            message = self._t("telemetry.failed", error=str(error))
            self.app.call_from_thread(self.app.notify, message, severity="error")
            return
        for report in reports:
            message = self._t("telemetry.changed", engine=report.engine, state=report.state.value)
            self.app.call_from_thread(self.app.notify, message)
        self.app.call_from_thread(self._reload)

    @work(thread=True, exit_on_error=False)
    def toggle_listener(self, start: bool) -> None:
        try:
            if start:
                port = self._services.listener_start()
                message = self._t("telemetry.started", port=port)
            else:
                self._services.listener_stop()
                message = self._t("telemetry.stopped")
        except Exception as error:
            message = self._t("telemetry.failed", error=str(error))
            self.app.call_from_thread(self.app.notify, message, severity="error")
            return
        self.app.call_from_thread(self.app.notify, message)
        self.app.call_from_thread(self._reload)

    def _reload(self) -> None:
        self.panel = None
        self.refresh_panel()
