from __future__ import annotations

from contextlib import suppress

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.widgets import Button, Input, Label, Log, Static

from cuanta.application.loop import LoopReport
from cuanta.domain.errors import CuantaError
from cuanta.domain.progress import Note, ProgressEvent, Status, StepFinished, StepStarted
from cuanta.tui.cells import labeled
from cuanta.tui.fmt import glyph, money, status_style
from cuanta.tui.i18n import Catalog
from cuanta.tui.services import LoopState, Services
from cuanta.tui.views.mandate import parse_budget
from cuanta.tui.widgets.flow import FlowRow


def parse_iterations(text: str) -> int | None:
    cleaned = text.strip()
    if not cleaned.isdigit():
        return None
    value = int(cleaned)
    return value if 1 <= value <= 20 else None


class LoopView(VerticalScroll):
    def __init__(self, services: Services, catalog: Catalog) -> None:
        super().__init__(id="loop", classes="view")
        self._services = services
        self._t = catalog
        self.state: LoopState | None = None
        self.running = False

    def compose(self) -> ComposeResult:
        t = self._t
        with Vertical(id="loop-card", classes="card"):
            yield Static(t("loop.title"), classes="card-title")
            yield Static("", id="loop-gate")
            with Horizontal(id="loop-form"):
                with Vertical(classes="field narrow"):
                    yield Label(t("loop.iterations"))
                    yield Input(id="loop-iterations")
                with Vertical(classes="field narrow"):
                    yield Label(t("loop.budget"))
                    yield Input(id="loop-budget")
            with FlowRow(classes="button-row"):
                yield Button(t("loop.run"), id="loop-run", variant="primary", compact=True)
                yield Button(t("loop.stop"), id="loop-stop", variant="error", compact=True)
        with Vertical(id="loop-feed-card", classes="card"):
            yield Log(id="loop-log", max_lines=300)
            yield Static("", id="loop-summary")

    def on_mount(self) -> None:
        with suppress(NoMatches):
            self.query_one("#loop-stop").display = False
            self.query_one("#loop-form").display = False
            self.query_one("#loop-run").display = False
            self.load()

    @work(thread=True, exit_on_error=False)
    def load(self) -> None:
        try:
            state = self._services.loop_state()
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        self.app.call_from_thread(self.show, state)

    def show(self, state: LoopState) -> None:
        self.state = state
        t = self._t
        gate = self.query_one("#loop-gate", Static)
        allowed = state.gate.allowed
        if allowed:
            gate.update(Content.assemble((f"{glyph(Status.OK)} ", "$success"), t("loop.allowed")))
        else:
            gate.update(
                Content("\n").join(
                    [
                        Content.assemble((f"{glyph(Status.WARN)} ", "$warning"), t("loop.blocked")),
                        labeled([(t("loop.missing"), state.gate.missing)], width=16),
                        Content.styled(t("loop.how"), "$text-muted"),
                    ]
                )
            )
        self.query_one("#loop-form").display = allowed
        self.query_one("#loop-run").display = allowed
        self.query_one("#loop-feed-card").display = allowed
        self.query_one("#loop-iterations", Input).value = str(state.max_iterations)
        self.query_one("#loop-budget", Input).value = f"{state.budget_usd:g}"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id
        if button == "loop-run":
            event.stop()
            self.start()
        elif button == "loop-stop":
            event.stop()
            self.stop()

    def start(self) -> None:
        if self.running or self.state is None or not self.state.gate.allowed:
            return
        iterations = parse_iterations(self.query_one("#loop-iterations", Input).value)
        budget = parse_budget(self.query_one("#loop-budget", Input).value)
        if iterations is None or budget is None:
            self.app.notify(self._t("mandate.invalid"), severity="warning")
            return
        self.running = True
        self.query_one("#loop-run", Button).disabled = True
        self.query_one("#loop-stop").display = True
        self.query_one("#loop-log", Log).clear()
        self.execute(iterations, budget)

    @work(thread=True, exclusive=True, group="loop", exit_on_error=False)
    def execute(self, iterations: int, budget: float) -> None:
        try:
            report = self._services.run_loop(iterations, budget, self._progress)
        except CuantaError as error:
            self.app.call_from_thread(self._failed, str(error), error.hint)
            return
        except Exception as error:
            self.app.call_from_thread(self._failed, str(error), "")
            return
        self.app.call_from_thread(self._finished, report)

    def _progress(self, event: ProgressEvent) -> None:
        self.app.call_from_thread(self.apply, event)

    def apply(self, event: ProgressEvent) -> None:
        log = self.query_one("#loop-log", Log)
        if isinstance(event, StepStarted):
            log.write_line(f"{glyph(Status.RESUME)} {self._t.message(event.message, event.label)}")
        elif isinstance(event, StepFinished):
            detail = self._t.message(event.message, event.detail)
            log.write_line(f"{glyph(event.status)} {event.key}  {detail}")
        elif isinstance(event, Note):
            log.write_line(f"{glyph(event.status)} {self._t.message(event.message, event.text)}")

    def stop(self) -> None:
        self.query_one("#loop-stop", Button).disabled = True
        self.stop_engine()

    @work(thread=True, exit_on_error=False)
    def stop_engine(self) -> None:
        self._services.stop_mandate()

    def _reset(self) -> None:
        self.running = False
        self.query_one("#loop-run", Button).disabled = False
        stop = self.query_one("#loop-stop", Button)
        stop.display = False
        stop.disabled = False

    def _failed(self, error: str, hint: str) -> None:
        self._reset()
        self.app.notify(self._t("loop.failed", error=error, hint=hint), severity="error")

    def _finished(self, report: LoopReport) -> None:
        self._reset()
        t = self._t
        status = Status.OK if report.ok else Status.WARN
        reason = report.stop.value.replace("_", " ")
        self.query_one("#loop-summary", Static).update(
            Content("\n").join(
                [
                    Content.assemble(
                        (f"{glyph(status)} ", status_style(status)),
                        t("loop.finished", reason=reason),
                    ),
                    labeled([(t("loop.spent"), money(report.spent_usd))], width=10),
                ]
            )
        )
        self.app.notify(
            t("loop.finished", reason=reason), severity="information" if report.ok else "warning"
        )
