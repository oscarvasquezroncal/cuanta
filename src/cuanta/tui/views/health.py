from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual.content import Content
from textual.message import Message
from textual.widgets import Button, Static

from cuanta.application.doctor import CheckResult, DoctorReport
from cuanta.domain.fixes import Fix, FixKind, classify
from cuanta.domain.progress import Status
from cuanta.tui.fmt import glyph, status_style
from cuanta.tui.i18n import Catalog
from cuanta.tui.services import Services
from cuanta.tui.widgets.telemetry import TelemetryCard

FIX_LABELS = {FixKind.RUN: "health.fix", FixKind.OPEN: "health.open", FixKind.COPY: "health.copy"}


def check_line(check: CheckResult, t: Catalog) -> Content:
    return Content.assemble(
        (f"{glyph(check.status)} ", status_style(check.status)),
        (t.check_name(check.name), "bold"),
        ("  ", ""),
        t.message(check.message, check.detail),
    )


class FixRequested(Message):
    def __init__(self, name: str, fix: Fix) -> None:
        super().__init__()
        self.name = name
        self.fix = fix


class CheckRow(Vertical):
    def __init__(self, index: int, check: CheckResult, catalog: Catalog) -> None:
        super().__init__(id=f"check-{index}", classes="check-row")
        self.check = check
        self.fix = classify(check.fix) if check.fix else None
        self._t = catalog

    def compose(self) -> ComposeResult:
        yield Static(check_line(self.check, self._t), classes="check-line")
        if self.fix is not None:
            with Horizontal(classes="check-fix"):
                with HorizontalScroll(classes="command-scroll"):
                    yield Static(Content.styled(self.fix.command, "$accent"), classes="command")
                yield Button(self._t(FIX_LABELS[self.fix.kind]), classes="fix-button", compact=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if self.fix is not None:
            self.post_message(FixRequested(self.check.name, self.fix))


class HealthView(VerticalScroll):
    def __init__(self, services: Services, catalog: Catalog) -> None:
        super().__init__(id="health", classes="view")
        self._services = services
        self._t = catalog
        self.report: DoctorReport | None = None
        self._generation = 0

    def compose(self) -> ComposeResult:
        t = self._t
        with Horizontal(id="health-bar"):
            yield Button(t("health.run"), id="health-run", variant="primary", compact=True)
            yield Static(Content.styled(t("health.loading"), "$text-muted"), id="health-summary")
        yield Vertical(id="checks", classes="card")
        yield TelemetryCard(self._services, self._t)

    def refresh_checks(self) -> None:
        self.query_one("#health-summary", Static).update(
            Content.styled(self._t("health.loading"), "$text-muted")
        )
        self.query_one("#health-run", Button).disabled = True
        self._load()

    @work(thread=True, exclusive=True, group="health", exit_on_error=False)
    def _load(self) -> None:
        try:
            report = self._services.doctor()
        except Exception as error:
            self.app.call_from_thread(self._failed, str(error))
            return
        self.app.call_from_thread(self.show, report)

    def _failed(self, error: str) -> None:
        self.query_one("#health-run", Button).disabled = False
        self.query_one("#health-summary", Static).update(Content.styled(error, "$error"))

    def show(self, report: DoctorReport) -> None:
        self.report = report
        self._generation += 1
        self.query_one("#health-run", Button).disabled = False
        summary = self._t(
            "health.summary",
            ok=report.count(Status.OK),
            warn=report.count(Status.WARN),
            fail=report.count(Status.FAIL),
        )
        style = "$error" if not report.healthy else "$success"
        self.query_one("#health-summary", Static).update(Content.styled(summary, style))
        container = self.query_one("#checks", Vertical)
        container.remove_children()
        container.mount_all(
            CheckRow(self._generation * 1000 + index, check, self._t)
            for index, check in enumerate(report.checks)
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "health-run":
            event.stop()
            self.refresh_checks()
