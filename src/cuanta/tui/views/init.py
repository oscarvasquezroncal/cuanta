from __future__ import annotations

from contextlib import suppress

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Button, Checkbox, DataTable, Input, Label, Log, Static
from textual.widgets.data_table import RowDoesNotExist

from cuanta.application.init_project import (
    STAGES,
    InitContext,
    InitOptions,
    InitReport,
    timed,
)
from cuanta.domain.errors import CuantaError
from cuanta.domain.messages import Message as Said
from cuanta.domain.progress import Note, ProgressEvent, Status, StepFinished, StepStarted
from cuanta.domain.telemetry import WiringPlan
from cuanta.tui.fmt import glyph, status_style
from cuanta.tui.i18n import Catalog
from cuanta.tui.services import Services
from cuanta.tui.views.mandate import parse_budget

TELEMETRY_ENGINE = "claude"
TABLED = frozenset({"verify.suggested", "verify.suggested_note"})


def suggestion_rows(context: InitContext) -> list[tuple[str, str, str]]:
    found = {item.path: item for item in context.suggestions}
    rows: list[tuple[str, str, str]] = []
    for path in context.new_files:
        item = found.get(path)
        if item is None:
            rows.append((path, path, ""))
        else:
            rows.append((path, item.original, f"+{item.added}/-{item.removed}"))
    return rows


class InitView(VerticalScroll):
    class ConsentNeeded(Message):
        def __init__(self, plans: tuple[WiringPlan, ...]) -> None:
            super().__init__()
            self.plans = plans

    class OpenNewFile(Message):
        def __init__(self, path: str) -> None:
            super().__init__()
            self.path = path

    def __init__(self, services: Services, catalog: Catalog) -> None:
        super().__init__(id="init", classes="view")
        self._services = services
        self._t = catalog
        self.report: InitReport | None = None
        self.running = False
        self.refresh_mode = False
        self.stage_status: dict[str, tuple[Status | None, Said | None]] = dict.fromkeys(
            STAGES, (None, None)
        )

    def compose(self) -> ComposeResult:
        t = self._t
        with Vertical(id="init-card", classes="card"):
            yield Static(t("init.title"), classes="card-title", id="init-title")
            with Horizontal(id="init-form"):
                with Vertical(classes="field narrow"):
                    yield Label(t("init.budget"))
                    yield Input(placeholder=t("init.budget_placeholder"), id="init-budget")
                with Vertical(classes="field"):
                    yield Checkbox(t("init.dry_run"), id="init-dry", compact=True)
                    yield Checkbox(t("init.telemetry"), True, id="init-telemetry", compact=True)
            yield Static("", id="init-budget-error", classes="field-error")
            yield Button(t("init.start"), id="init-start", variant="primary", compact=True)
        with Horizontal(id="init-lower"):
            with Vertical(id="timeline", classes="card"):
                yield Static(t("init.feed"), classes="card-title")
                for stage in STAGES:
                    yield Static("", id=f"stage-{stage}", classes="stage-row")
                yield Log(id="init-log", max_lines=300)
            with Vertical(id="init-results", classes="card"):
                yield Static(t("init.results"), classes="card-title")
                yield Static("", id="init-summary")
                yield DataTable(id="init-new-files", cursor_type="row")
                yield Static("", id="init-new-hint")

    def on_mount(self) -> None:
        with suppress(NoMatches):
            table = self.query_one("#init-new-files", DataTable)
            table.add_column(self._t("init.new_files"), key="path")
            table.add_column(self._t("init.new_files_change"), key="change")
            table.display = False
            self._paint_stages()

    def _paint_stages(self) -> None:
        t = self._t
        for stage in STAGES:
            status, message = self.stage_status[stage]
            detail = t.message(message)
            name = t(f"init.stage_{stage}").ljust(12)
            if status is None:
                line = Content.assemble(
                    ("· ", "$text-muted"), name, (t("init.waiting"), "$text-muted")
                )
            else:
                line = Content.assemble(
                    (f"{glyph(status)} ", status_style(status)),
                    (name, "bold"),
                    (detail, "$text-muted"),
                )
            self.query_one(f"#stage-{stage}", Static).update(line)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "init-start":
            return
        event.stop()
        self.start()

    def start(self) -> None:
        if self.running:
            return
        budget = parse_budget(self.query_one("#init-budget", Input).value)
        error = self.query_one("#init-budget-error", Static)
        if budget is None:
            error.update(Content.styled(self._t("mandate.bad_budget"), "$error"))
            return
        error.update("")
        dry = self.query_one("#init-dry", Checkbox).value
        telemetry = self.query_one("#init-telemetry", Checkbox).value
        if telemetry and not dry:
            self.ask_consent()
            return
        self.begin(consent=dry and telemetry)

    @work(thread=True, exit_on_error=False)
    def ask_consent(self) -> None:
        try:
            wired = self._services.telemetry_wired(TELEMETRY_ENGINE)
            plans = () if wired else self._services.telemetry_plan(TELEMETRY_ENGINE)
        except Exception as error:
            self.app.call_from_thread(self._failed, str(error), "")
            return
        if wired:
            self.app.call_from_thread(self.begin, True)
            return
        self.app.call_from_thread(self.post_message, self.ConsentNeeded(plans))

    def consent_answer(self, answer: str | None) -> None:
        if answer is None:
            return
        self.begin(consent=answer == "allow")

    def _budget(self) -> float | None:
        value = parse_budget(self.query_one("#init-budget", Input).value)
        return value if value else None

    def begin(self, consent: bool) -> None:
        self.running = True
        self.stage_status = dict.fromkeys(STAGES, (None, None))
        self._paint_stages()
        self.query_one("#init-log", Log).clear()
        button = self.query_one("#init-start", Button)
        button.label = self._t("init.running")
        button.disabled = True
        options = InitOptions(
            dry_run=self.query_one("#init-dry", Checkbox).value, refresh_forge=self.refresh_mode
        )
        self.execute(options, consent, self._budget())

    @work(thread=True, exclusive=True, group="init", exit_on_error=False)
    def execute(self, options: InitOptions, consent: bool, budget: float | None) -> None:
        try:
            report = self._services.run_init(options, consent, budget, self._progress)
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
        if isinstance(event, StepStarted) and event.key in self.stage_status:
            self.stage_status[event.key] = (Status.RESUME, event.message)
            self._paint_stages()
        elif isinstance(event, StepFinished) and event.key in self.stage_status:
            self.stage_status[event.key] = (event.status, event.message)
            self._paint_stages()
        elif isinstance(event, Note):
            line = self._t.message(event.message, event.text)
            self.query_one("#init-log", Log).write_line(f"{glyph(event.status)} {line}")

    def set_refresh(self, refresh: bool) -> None:
        self.refresh_mode = refresh
        with suppress(NoMatches):
            title = "init.refresh_title" if refresh else "init.title"
            self.query_one("#init-title", Static).update(self._t(title))
            if not self.running:
                self.query_one("#init-start", Button).label = self._start_label()

    def _start_label(self) -> str:
        return self._t("init.refresh_start" if self.refresh_mode else "init.start")

    def _reset(self) -> None:
        self.running = False
        button = self.query_one("#init-start", Button)
        button.label = self._start_label()
        button.disabled = False

    def _failed(self, error: str, hint: str) -> None:
        self._reset()
        self.app.notify(
            self._t("init.failed", error=error, hint=hint), severity="error", markup=False
        )

    def _finished(self, report: InitReport) -> None:
        self._reset()
        self.report = report
        t = self._t
        context = report.context
        for key, result in report.stages:
            if key in self.stage_status:
                said = (
                    result.message
                    if result.seconds is None
                    else timed(result.message, result.seconds)
                )
                self.stage_status[key] = (result.status, said)
        self._paint_stages()
        lines: list[Content] = []
        if context.dry_run:
            lines.append(Content.styled(t("init.planned"), "bold"))
            planned = [t.message(change) for change in context.planned]
            lines.extend(
                Content(f"  {change}") for change in planned or [t("init.nothing_planned")]
            )
        registered = context.registration_message
        if registered is not None:
            status = Status.WARN if registered.key == "registration.deferred" else Status.OK
            lines.append(
                Content.assemble(
                    (f"{glyph(status)} ", status_style(status)),
                    (f"{t('init.registration')}  ", "$text-muted"),
                    t.message(registered),
                )
            )
        if context.verify_lines:
            lines.append(Content.styled(t("init.verify"), "bold"))
            for finding in context.verify_lines:
                if finding.message.key in TABLED:
                    continue
                lines.append(
                    Content.assemble(
                        (f"{glyph(finding.status)} ", status_style(finding.status)),
                        t.message(finding.message),
                    )
                )
                if finding.fix is not None:
                    fix = t("init.fix", fix=t.message(finding.fix))
                    lines.append(Content.styled(f"  {fix}", "$text-muted"))
        self.query_one("#init-summary", Static).update(Content("\n").join(lines))
        table = self.query_one("#init-new-files", DataTable)
        table.clear()
        for path, original, change in suggestion_rows(context):
            table.add_row(Text(original), Text(change), key=path)
        has_new = bool(context.new_files)
        table.display = has_new
        self.query_one("#init-new-hint", Static).update(
            Content.styled(t("init.new_files_hint"), "$text-muted") if has_new else ""
        )
        warned = any(finding.status is Status.WARN for finding in context.verify_lines)
        if context.dry_run:
            self.app.notify(t("init.finished_dry"))
        elif report.ok and warned:
            self.app.notify(t("init.finished_warnings"))
        elif report.ok:
            self.app.notify(t("init.finished_ok"))
        else:
            self.app.notify(t("init.finished_problems"), severity="warning")

    def resolved(self, path: str) -> None:
        if self.report is None:
            return
        context = self.report.context
        remaining = [item for item in context.new_files if item != path]
        context.new_files[:] = remaining
        context.suggestions[:] = [item for item in context.suggestions if item.path != path]
        table = self.query_one("#init-new-files", DataTable)
        with suppress(RowDoesNotExist):
            table.remove_row(path)
        table.display = bool(remaining)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "init-new-files":
            return
        event.stop()
        self.post_message(self.OpenNewFile(str(event.row_key.value or "")))
