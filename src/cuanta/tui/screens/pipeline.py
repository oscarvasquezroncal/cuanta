from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import suppress

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, Footer, Log, Static

from cuanta.application.mandate import MandateReport
from cuanta.application.mandate_flow import MandateOptions
from cuanta.domain.audit import AuditRow, AuditStatus
from cuanta.domain.engine import EngineEvent
from cuanta.domain.errors import CuantaError
from cuanta.domain.mandate import MandateRequest, parse_shape, single_context
from cuanta.domain.pipeline import STAGES, AgentCard, CardState, Pipeline
from cuanta.domain.progress import Note, ProgressEvent, Status
from cuanta.tui.cells import labeled
from cuanta.tui.fmt import compact, glyph, money, status_style
from cuanta.tui.i18n import Catalog
from cuanta.tui.screens.result import ResultScreen
from cuanta.tui.services import Services

STATE_STATUS = {
    CardState.WAITING: Status.SKIP,
    CardState.ACTIVE: Status.RESUME,
    CardState.DONE: Status.OK,
    CardState.FAILED: Status.FAIL,
}


def elapsed_text(seconds: float) -> str:
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes:d}:{rest:02d}"


def audit_line(row: AuditRow, t: Catalog) -> Content:
    state = Status.OK if row.ok else Status.FAIL
    if row.status is AuditStatus.NOT_RUN:
        state = Status.SKIP
    cause = "" if row.ok else f"  {t.message(row.cause)}"
    return Content.assemble(
        (f"{glyph(state)} ", status_style(state)),
        (row.agent, "bold"),
        f"  {row.planned}",
        (cause, "$text-muted"),
    )


class CardView(Vertical):
    def __init__(self, index: int, stage: str, title: str, catalog: Catalog) -> None:
        super().__init__(id=f"card-{stage}", classes="agent-card card")
        self.index = index
        self.title = title
        self._t = catalog

    def compose(self) -> ComposeResult:
        yield Static(self._t(self.title), classes="card-title")
        yield Static("", classes="agent-body")

    def show(self, card: AgentCard) -> None:
        t = self._t
        status = STATE_STATUS[card.state]
        for state in CardState:
            self.set_class(card.state is state, f"-{state.value}")
        rows = [
            (t("pipeline.tokens"), compact(card.tokens)),
            (t("pipeline.tools"), f"{card.tools:,}"),
        ]
        body = Content("\n").join(
            [
                Content.assemble(
                    (f"{glyph(status)} ", status_style(status)),
                    t(f"pipeline.{card.state.value}"),
                ),
                Content.styled(
                    t("pipeline.main") if card.agent == "main" else card.agent or "–", "bold"
                ),
                labeled(rows, width=12),
            ]
        )
        self.query_one(".agent-body", Static).update(body)


class PipelineScreen(Screen[None]):
    BINDINGS = [Binding("escape", "back", show=False)]

    class OpenSpectrum(Message):
        def __init__(self, run_id: str) -> None:
            super().__init__()
            self.run_id = run_id

    def __init__(
        self,
        services: Services,
        catalog: Catalog,
        request: MandateRequest,
        signatures: int,
        options: MandateOptions,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(id="pipeline-screen")
        self._clock = clock
        self._services = services
        self._t = catalog
        self.request = request
        self.signatures = signatures
        self.options = options
        self._single_context = single_context(
            request.type, options.simple, parse_shape(options.shape)
        )
        stages = ("analyst",) if request.type == "investigation" else STAGES
        if options.simple and request.type != "investigation":
            stages = ("main",)
        self.pipeline = Pipeline(stages=stages, main_context=self._single_context or options.simple)
        self.report: MandateReport | None = None
        self.running = True
        self._started = clock()

    def compose(self) -> ComposeResult:
        t = self._t
        with Horizontal(id="pipeline-bar"):
            yield Static(t("pipeline.title"), classes="card-title", id="pipeline-title")
            yield Static("", id="pipeline-elapsed")
            yield Button(t("pipeline.stop"), id="pipeline-stop", variant="error", compact=True)
            yield Button(t("result.view"), id="pipeline-result", variant="primary", compact=True)
            yield Button(t("pipeline.back"), id="pipeline-back", compact=True)
        with VerticalScroll(id="pipeline-body"):
            with Horizontal(id="agent-cards"):
                for index, stage in enumerate(self.pipeline.stages):
                    title = f"pipeline.stage_{stage}"
                    if self._single_context:
                        title = "pipeline.stage_analyst_single"
                    yield CardView(index, stage, title, t)
            with Horizontal(id="pipeline-lower"):
                with Vertical(id="feed-card", classes="card"):
                    yield Static(t("pipeline.feed"), classes="card-title")
                    yield Log(id="pipeline-feed", max_lines=500)
                with Vertical(id="summary-card", classes="card"):
                    yield Static(t("pipeline.summary"), classes="card-title")
                    yield Static("", id="pipeline-summary")
                    yield Button(t("pipeline.open_spectrum"), id="pipeline-spectrum", compact=True)
        yield Footer()

    def on_mount(self) -> None:
        with suppress(NoMatches):
            self._start()

    def _start(self) -> None:
        self.query_one("#pipeline-back", Button).display = False
        self.query_one("#pipeline-result", Button).display = False
        self.query_one("#pipeline-spectrum", Button).display = False
        self._paint()
        self.set_interval(1.0, self._tick)
        self._tick()
        self.execute()

    def _tick(self) -> None:
        if not self.running:
            return
        seconds = self._clock() - self._started
        self.query_one("#pipeline-elapsed", Static).update(
            Content.assemble(
                (f"{self._t('pipeline.elapsed')}  ", "$text-muted"), elapsed_text(seconds)
            )
        )

    @work(thread=True, exclusive=True, group="mandate-run", exit_on_error=False)
    def execute(self) -> None:
        try:
            report = self._services.run_mandate(
                self.request,
                self.signatures,
                self.options,
                self._from_engine,
                self._from_progress,
            )
        except CuantaError as error:
            self.app.call_from_thread(self._failed, str(error), error.hint)
            return
        except Exception as error:
            self.app.call_from_thread(self._failed, str(error), "")
            return
        self.app.call_from_thread(self._finished, report)

    def _from_engine(self, event: EngineEvent) -> None:
        self.app.call_from_thread(self.apply, event)

    def _from_progress(self, event: ProgressEvent) -> None:
        if isinstance(event, Note):
            self.app.call_from_thread(self._log, self._t.message(event.message, event.text))

    def _log(self, line: str) -> None:
        self.query_one("#pipeline-feed", Log).write_line(line)

    def apply(self, event: EngineEvent) -> None:
        before = len(self.pipeline.feed)
        self.pipeline.apply(event)
        for line in self.pipeline.feed[before:]:
            self._log(line)
        self._paint()

    def _paint(self) -> None:
        for view in self.query(CardView):
            view.show(self.pipeline.cards[view.index])

    def _done(self) -> None:
        self.running = False
        self.query_one("#pipeline-stop", Button).display = False
        self.query_one("#pipeline-back", Button).display = True

    def _failed(self, error: str, hint: str) -> None:
        with suppress(NoMatches):
            self._show_failure(error, hint)

    def _show_failure(self, error: str, hint: str) -> None:
        self._done()
        message = self._t("pipeline.run_failed", error=error, hint=hint)
        self.query_one("#pipeline-summary", Static).update(Content.styled(message, "$error"))
        self.app.notify(message, severity="error")

    def _finished(self, report: MandateReport) -> None:
        with suppress(NoMatches):
            self._show_report(report)

    def _show_report(self, report: MandateReport) -> None:
        self._done()
        self.report = report
        t = self._t
        status = Status.OK if report.ok else Status.FAIL
        headline = t("pipeline.ok") if report.ok else t("pipeline.not_ok")
        files = "\n".join(report.changed_files[:12]) or t("pipeline.no_changes")
        rows = [
            (t("pipeline.tests_status"), report.tests),
            (t("pipeline.cost"), money(report.run.cost_usd, t("spectrum.na"))),
            (t("pipeline.tools"), f"{report.tool_calls:,}"),
        ]
        parts = [
            Content.assemble((f"{glyph(status)} {headline}", "bold")),
            labeled(rows, width=14),
        ]
        if report.audit:
            parts.append(Content.styled(t("pipeline.audit"), "$text-muted"))
            parts.extend(audit_line(row, t) for row in report.audit)
        parts.extend([Content.styled(t("pipeline.changed"), "$text-muted"), Content(files)])
        body = Content("\n").join(parts)
        self.query_one("#pipeline-summary", Static).update(body)
        self.query_one("#pipeline-spectrum", Button).display = True
        result_button = self.query_one("#pipeline-result", Button)
        result_button.display = True
        result_button.focus()
        self.app.notify(
            f"{headline} · {t('result.ready')}",
            severity="information" if report.ok else "warning",
        )
        self.open_result()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button = event.button.id
        if button == "pipeline-stop":
            self.stop()
        elif button == "pipeline-back":
            self.action_back()
        elif button == "pipeline-result":
            self.open_result()
        elif button == "pipeline-spectrum" and self.report is not None:
            self.app.post_message(self.OpenSpectrum(self.report.run.id))
            self.dismiss(None)

    @work(thread=True, exclusive=True, group="pipeline-result", exit_on_error=False)
    def open_result(self) -> None:
        if self.report is None:
            return
        try:
            view = self._services.result_view(self.report.run.id)
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        if view is None:
            self.app.call_from_thread(self.app.notify, self._t("result.none"), severity="warning")
            return
        self.app.call_from_thread(self.app.push_screen, ResultScreen(self._services, self._t, view))

    def stop(self) -> None:
        button = self.query_one("#pipeline-stop", Button)
        button.label = self._t("pipeline.stopping")
        button.disabled = True
        self.stop_engine()

    @work(thread=True, exit_on_error=False)
    def stop_engine(self) -> None:
        if self._services.stop_mandate():
            self.app.call_from_thread(
                self.app.notify, self._t("pipeline.stopped"), severity="warning"
            )

    def action_back(self) -> None:
        if not self.running:
            self.dismiss(None)
