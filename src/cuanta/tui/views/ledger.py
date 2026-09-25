from __future__ import annotations

from contextlib import suppress

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Button, DataTable, Input, Label, Select, Static

from cuanta.application.ledger_view import RunFilter, facets, filter_runs
from cuanta.domain.ledger import Run
from cuanta.tui.fmt import money
from cuanta.tui.i18n import Catalog
from cuanta.tui.services import Services
from cuanta.tui.widgets.facts import Facts

ANY = ""
COLUMNS = ("col_started", "col_kind", "col_engine", "col_status", "col_cost")


class LedgerView(Vertical):
    class ResultRequested(Message):
        def __init__(self, run_id: str) -> None:
            super().__init__()
            self.run_id = run_id

    class ExportRequested(Message):
        def __init__(self, fmt: str) -> None:
            super().__init__()
            self.fmt = fmt

    def __init__(self, services: Services, catalog: Catalog) -> None:
        super().__init__(id="ledger", classes="view")
        self._services = services
        self._t = catalog
        self.runs: tuple[Run, ...] = ()
        self.shown: tuple[Run, ...] = ()
        self.selected = ""
        self._loaded = False

    def compose(self) -> ComposeResult:
        t = self._t
        with Horizontal(id="ledger-filters"):
            with Vertical(classes="field"):
                yield Label(t("ledger.kind"))
                yield Select(
                    [(t("ledger.all"), ANY)], value=ANY, allow_blank=False, id="filter-kind"
                )
            with Vertical(classes="field"):
                yield Label(t("ledger.engine"))
                yield Select(
                    [(t("ledger.all"), ANY)], value=ANY, allow_blank=False, id="filter-engine"
                )
            with Vertical(classes="field"):
                yield Label(t("ledger.since"))
                yield Input(placeholder=t("ledger.since_placeholder"), id="filter-since")
        with Horizontal(id="ledger-meta"):
            yield Static("", id="ledger-count")
            yield Button(t("ledger.export_json"), id="export-json", compact=True)
            yield Button(t("ledger.export_csv"), id="export-csv", compact=True)
        with Horizontal(id="ledger-body"):
            with Vertical(id="ledger-table-card", classes="card"):
                yield DataTable(id="ledger-runs", cursor_type="row", zebra_stripes=True)
                yield Static("", id="ledger-empty")
            with Vertical(id="ledger-detail", classes="card"):
                yield Static(t("ledger.details"), classes="card-title")
                yield Facts(
                    Content.styled(t("ledger.select_hint"), "$text-muted"), id="ledger-fields"
                )
                yield Button(t("result.view"), id="ledger-result", variant="primary", compact=True)

    def on_mount(self) -> None:
        with suppress(NoMatches):
            self._setup()

    def _setup(self) -> None:
        self.query_one("#ledger-result", Button).display = False
        table = self.query_one("#ledger-runs", DataTable)
        for key in COLUMNS:
            table.add_column(self._t(f"ledger.{key}"), key=key)

    def activate(self) -> None:
        if not self._loaded:
            self._loaded = True
            self.reload()

    @work(thread=True, exclusive=True, group="ledger", exit_on_error=False)
    def reload(self) -> None:
        try:
            runs = self._services.recent_runs()
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            runs = ()
        self.app.call_from_thread(self.show, runs)

    def show(self, runs: tuple[Run, ...]) -> None:
        self.runs = runs
        kinds, engines = facets(runs)
        all_label = self._t("ledger.all")
        for selector, values in (("#filter-kind", kinds), ("#filter-engine", engines)):
            field = self.query_one(selector, Select)
            current = field.value
            field.set_options([(all_label, ANY), *((value, value) for value in values)])
            field.value = current if isinstance(current, str) and current in values else ANY
        self.apply_filters()

    def selection(self) -> RunFilter:
        kind = self.query_one("#filter-kind", Select).value
        engine = self.query_one("#filter-engine", Select).value
        since = self.query_one("#filter-since", Input).value.strip()
        return RunFilter(
            kind if isinstance(kind, str) else ANY,
            engine if isinstance(engine, str) else ANY,
            since,
        )

    def apply_filters(self) -> None:
        t = self._t
        self.shown = filter_runs(self.runs, self.selection())
        table = self.query_one("#ledger-runs", DataTable)
        table.clear()
        for run in self.shown:
            table.add_row(
                Text(run.started_at[5:16].replace("T", " ") or "–"),
                Text(t.keyed("run_kind", run.kind)),
                Text(run.engine or "–"),
                Text(t.keyed("run_status", run.status)),
                Text(money(run.cost_usd, t("spectrum.na")) if run.engine else "–", justify="right"),
                key=run.id,
            )
        t = self._t
        empty = self.query_one("#ledger-empty", Static)
        if not self.runs:
            empty.update(Content.styled(t("ledger.no_ledger"), "$text-muted"))
        elif not self.shown:
            empty.update(Content.styled(t("ledger.empty"), "$text-muted"))
        else:
            empty.update("")
        empty.display = not self.shown
        table.display = bool(self.shown)
        self.query_one("#ledger-count", Static).update(
            Content.styled(
                t("ledger.count", shown=f"{len(self.shown):,}", total=f"{len(self.runs):,}"),
                "$text-muted",
            )
        )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id in {"filter-kind", "filter-engine"}:
            event.stop()
            self.apply_filters()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-since":
            event.stop()
            self.apply_filters()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "ledger-runs":
            return
        event.stop()
        run = next((item for item in self.shown if item.id == event.row_key.value), None)
        if run is not None:
            self.show_details(run)

    def show_details(self, run: Run) -> None:
        self.selected = run.id
        self.query_one("#ledger-result", Button).display = True
        t = self._t
        rows = [
            (t("ledger.field_id"), run.id),
            (t("ledger.field_kind"), t.keyed("run_kind", run.kind)),
            (t("ledger.field_engine"), run.engine or "–"),
            (t("ledger.field_model"), run.model or "–"),
            (t("ledger.field_status"), t.keyed("run_status", run.status)),
            (t("ledger.field_started"), run.started_at or "–"),
            (t("ledger.field_ended"), run.ended_at or "–"),
            (t("ledger.field_cost"), money(run.cost_usd, t("spectrum.na"))),
            (t("ledger.field_story"), run.hu_ref or "–"),
            (t("ledger.field_scope"), run.scope or "–"),
        ]
        self.query_one("#ledger-fields", Facts).show(rows)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id or ""
        if button in {"export-json", "export-csv"}:
            event.stop()
            self.post_message(self.ExportRequested(button.removeprefix("export-")))
        elif button == "ledger-result" and self.selected:
            event.stop()
            self.post_message(self.ResultRequested(self.selected))
