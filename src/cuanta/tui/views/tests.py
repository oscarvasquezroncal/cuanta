from __future__ import annotations

from contextlib import suppress

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Button, DataTable, Static

from cuanta.application.tests_view import Hairball, TestsSummary
from cuanta.domain.errors import CuantaError
from cuanta.domain.progress import Status
from cuanta.tui.cells import labeled
from cuanta.tui.fmt import glyph, grouped
from cuanta.tui.i18n import Catalog
from cuanta.tui.services import Services

COLUMNS = ("col_signature", "col_tests", "col_location", "col_triage", "col_error")
ERROR_WIDTH = 72


def duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}m {rest:02d}s"


class TestsView(Vertical):
    class OpenCapsule(Message):
        def __init__(self, summary: TestsSummary, hairball: Hairball | None) -> None:
            super().__init__()
            self.summary = summary
            self.hairball = hairball

    def __init__(self, services: Services, catalog: Catalog) -> None:
        super().__init__(id="tests", classes="view")
        self._services = services
        self._t = catalog
        self.summary: TestsSummary | None = None
        self.running = False

    def compose(self) -> ComposeResult:
        t = self._t
        with Horizontal(id="tests-bar"):
            yield Button(t("tests.run"), id="run-tests", variant="primary", compact=True)
            yield Static("", id="tests-summary")
        with Vertical(id="tests-card", classes="card"):
            yield Static(t("tests.hairballs"), classes="card-title")
            yield Static(Content.styled(t("tests.empty"), "$text-muted"), id="tests-empty")
            yield DataTable(id="hairballs", cursor_type="row", zebra_stripes=True)
            yield Static(Content.styled(t("tests.hint"), "$text-muted"), id="tests-hint")

    def on_mount(self) -> None:
        with suppress(NoMatches):
            self._setup()

    def _setup(self) -> None:
        table = self.query_one("#hairballs", DataTable)
        for key in COLUMNS:
            table.add_column(self._t(f"tests.{key}"), key=key)
        self.show(None)
        self.load_latest()

    @work(thread=True, exclusive=True, group="tests-latest", exit_on_error=False)
    def load_latest(self) -> None:
        try:
            summary = self._services.latest_tests()
        except Exception:
            summary = None
        self.app.call_from_thread(self._loaded, summary)

    def _loaded(self, summary: TestsSummary | None) -> None:
        if not self.running and summary is not None:
            self.show(summary)

    def run_tests(self) -> None:
        if self.running:
            return
        self.running = True
        button = self.query_one("#run-tests", Button)
        button.label = self._t("tests.running")
        button.disabled = True
        self._run()

    @work(thread=True, exclusive=True, group="tests-run", exit_on_error=False)
    def _run(self) -> None:
        try:
            summary = self._services.run_tests()
        except CuantaError as error:
            self.app.call_from_thread(self._failed, str(error), error.hint)
            return
        except Exception as error:
            self.app.call_from_thread(self._failed, str(error), "")
            return
        self.app.call_from_thread(self._finished, summary)

    def _reset_button(self) -> None:
        self.running = False
        button = self.query_one("#run-tests", Button)
        button.label = self._t("tests.run")
        button.disabled = False

    def _failed(self, error: str, hint: str) -> None:
        self._reset_button()
        self.app.notify(self._t("tests.failed", error=error, hint=hint), severity="error")

    def _finished(self, summary: TestsSummary) -> None:
        self._reset_button()
        self.show(summary)
        t = self._t
        if summary.green:
            self.app.notify(t("tests.finished_green", passed=grouped(summary.passed)))
        else:
            failed = grouped(summary.failed + summary.errored)
            self.app.notify(
                t("tests.finished_red", failed=failed, hairballs=len(summary.hairballs)),
                severity="warning",
            )

    def show(self, summary: TestsSummary | None) -> None:
        self.summary = summary
        table = self.query_one("#hairballs", DataTable)
        table.clear()
        empty = summary is None
        self.query_one("#tests-empty", Static).display = empty
        self.query_one("#tests-summary", Static).update(self._summary_text(summary))
        if summary is None:
            table.display = False
            self.query_one("#tests-hint", Static).display = False
            return
        for hairball in summary.hairballs:
            table.add_row(
                Text(hairball.id),
                Text(grouped(hairball.tests), justify="right"),
                Text(hairball.location or "–"),
                Text(hairball.triage or "–"),
                Text(hairball.verbatim[:ERROR_WIDTH]),
                key=hairball.id,
            )
        has_rows = bool(summary.hairballs)
        table.display = has_rows
        self.query_one("#tests-hint", Static).display = has_rows

    def _summary_text(self, summary: TestsSummary | None) -> Content:
        if summary is None:
            return Content("")
        t = self._t
        spent = duration(summary.duration_s)
        if summary.green:
            status = Status.OK
            result = t("tests.green", passed=grouped(summary.passed), duration=spent)
        else:
            status = Status.FAIL
            result = t(
                "tests.red",
                failed=grouped(summary.failed + summary.errored),
                passed=grouped(summary.passed),
                duration=spent,
            )
        rows = [
            (t("tests.result"), f"{glyph(status)} {result}"),
            (t("tests.runner"), summary.runner),
            (t("tests.last_run"), summary.started_at[:16].replace("T", " ") or "–"),
        ]
        return labeled(rows)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-tests":
            event.stop()
            self.run_tests()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        if self.summary is None:
            return
        key = str(event.row_key.value or "")
        chosen = next((item for item in self.summary.hairballs if item.id == key), None)
        self.post_message(self.OpenCapsule(self.summary, chosen))
