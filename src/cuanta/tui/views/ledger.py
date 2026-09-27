from __future__ import annotations

from contextlib import suppress

from rich.style import Style
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.color import Color, ColorParseError
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Button, DataTable, Input, Label, Select, Static

from cuanta.application.ledger_view import RunFilter, facets, filter_runs
from cuanta.domain.ledger import Run
from cuanta.domain.real_costs import CostReport, CostRow
from cuanta.tui.chips import MIX_KEYS, TYPE_KEYS, chip_variable
from cuanta.tui.fmt import cost_money, run_money
from cuanta.tui.i18n import Catalog
from cuanta.tui.services import Services
from cuanta.tui.widgets.facts import Facts

ANY = ""
COLUMNS = ("col_started", "col_kind", "col_engine", "col_status", "col_cost")
COST_COLUMNS = (
    "col_type",
    "col_runs",
    "col_accepted",
    "col_per_accepted",
    "col_spend",
    "col_missing",
    "col_median",
    "col_time",
    "col_error",
)
MINUTE = 60
HOUR = 3600


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
        self.costs: CostReport | None = None

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
            yield Button(t("ledger.show_costs"), id="ledger-costs-toggle", compact=True)
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
        with Vertical(id="ledger-costs", classes="card"):
            yield Static(t("costs.title"), classes="card-title")
            yield DataTable(id="ledger-costs-table", cursor_type="none", zebra_stripes=True)
            yield Static("", id="ledger-costs-note")

    def on_mount(self) -> None:
        with suppress(NoMatches):
            self._setup()
        self.app.theme_changed_signal.subscribe(self, self._theme_changed)

    def _theme_changed(self, _: object) -> None:
        if self.costs is not None:
            self.show_costs(self.costs)

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
                Text(run_money(run, t) if run.engine else "–", justify="right"),
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
            (t("ledger.field_cost"), run_money(run, t)),
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
        elif button == "ledger-costs-toggle":
            event.stop()
            showing = not self.has_class("-costs")
            self.set_class(showing, "-costs")
            event.button.label = self._t("ledger.show_runs" if showing else "ledger.show_costs")
            if showing:
                self.load_costs()

    @work(thread=True, exclusive=True, group="ledger-costs", exit_on_error=False)
    def load_costs(self) -> None:
        try:
            report = self._services.real_costs()
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        self.app.call_from_thread(self.show_costs, report)

    def show_costs(self, report: CostReport) -> None:
        t = self._t
        self.costs = report
        table = self.query_one("#ledger-costs-table", DataTable)
        table.clear(columns=True)
        colors = self.app.theme_variables
        body: list[tuple[str, Text, tuple[str, ...]]] = [
            ("head-type", Text(t("costs.by_type"), style="bold"), ())
        ]
        for row in report.by_type:
            style = chip_style(colors.get(chip_variable(row.key), ""))
            label = Text(t(TYPE_KEYS.get(row.key, "costs.type_untyped")), style=style)
            body.append((f"type-{row.key}", label, cost_cells(t, row)))
        body.append(("head-mix", Text(t("costs.by_mix"), style="bold"), ()))
        for row in report.by_mix:
            label = Text(t(MIX_KEYS.get(row.key, "costs.mix_other")))
            body.append((f"mix-{row.key}", label, cost_cells(t, row)))
        headers = [t(f"costs.{key}") for key in COST_COLUMNS[1:]]
        widths = [
            max(len(header), *(len(cells[index]) for _, _, cells in body if cells))
            for index, header in enumerate(headers)
        ]
        table.add_column(Text(t("costs.col_type")), key=COST_COLUMNS[0])
        for key, header, width in zip(COST_COLUMNS[1:], headers, widths, strict=True):
            table.add_column(Text(header.rjust(width)), key=key)
        for key, label, cells in body:
            shown = cells or ("",) * len(widths)
            padded = (Text(cell.rjust(width)) for cell, width in zip(shown, widths, strict=True))
            table.add_row(label, *padded, key=key)
        note = self.query_one("#ledger-costs-note", Static)
        if report.empty:
            note.update(Content.styled(t("costs.empty"), "$text-muted"))
        else:
            note.update(Content.styled(t("costs.note", since=report.since[:10]), "$text-muted"))


def cost_cells(t: Catalog, row: CostRow) -> tuple[str, ...]:
    na = t("spectrum.na")
    if not row.runs:
        return ("0", "0", "–", "–", "–", "–", "–", "–")
    spend = row.spend
    per = cost_money(row.per_accepted, na, spend.lower_bound, row.estimated)
    time = compact_seconds(t, row.median_seconds) if row.median_seconds is not None else "–"
    error = row.median_error
    shown_error = (
        "–"
        if error is None
        else t("result.error_in_range")
        if row.error_in_range
        else f"{error:+.0%}"
    )
    return (
        str(row.runs),
        str(row.accepted),
        per,
        cost_money(spend.value, na, spend.lower_bound, row.estimated),
        str(spend.missing),
        cost_money(row.median_cost, na, estimated=row.estimated),
        time,
        shown_error,
    )


def compact_seconds(t: Catalog, seconds: float) -> str:
    if seconds < MINUTE:
        return t("costs.seconds", value=f"{seconds:.0f}")
    if seconds < HOUR:
        return t("costs.minutes", value=f"{seconds / MINUTE:.1f}")
    return t("costs.hours", value=f"{seconds / HOUR:.1f}")


def chip_style(value: str) -> Style:
    try:
        color = Color.parse(value).rich_color
    except ColorParseError:
        return Style(bold=True)
    return Style(bold=True, color=color)
