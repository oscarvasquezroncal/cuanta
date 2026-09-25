from __future__ import annotations

from contextlib import suppress

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Button, DataTable, Select, Static, TabbedContent, TabPane, Tree
from textual.widgets.tree import TreeNode

from cuanta.application.spectrum import SpectrumResult
from cuanta.domain.audit import AuditStatus
from cuanta.domain.errors import CuantaError
from cuanta.domain.ledger import Run
from cuanta.domain.messages import parse_english
from cuanta.domain.overhead import overhead_messages
from cuanta.domain.progress import Status
from cuanta.domain.spectrum import Branch, Leak, LeakKind, View, Window
from cuanta.tui.bars import bar_lines
from cuanta.tui.fmt import compact, glyph, money, status_style
from cuanta.tui.i18n import Catalog
from cuanta.tui.services import ALL_IMPORTED, Services
from cuanta.tui.widgets.flow import FlowRow

LATEST = ""
VIEWS = (View.AGENT, View.MODEL, View.TOOL, View.FILE)
LEAK_COLUMNS = ("col_kind", "col_subject", "col_agent", "col_tokens", "col_detail")
GATEWAY_LEAKS = frozenset({LeakKind.AMPLIFICATION, LeakKind.TEST_OUTPUT})


def run_option(run: Run, t: Catalog) -> str:
    started = run.started_at[5:16].replace("T", " ")
    return f"{started}  {t.keyed('run_kind', run.kind)}  {run.id[-6:]}".strip()


def window_rows(windows: list[Window]) -> list[tuple[str, int, float]]:
    whole = sum(window.totals.total for window in windows) or 1
    return [(window.label, window.totals.total, window.totals.total / whole) for window in windows]


def add_branches(node: TreeNode[None], item: Branch, t: Catalog) -> None:
    for child in item.children:
        name = t.message(child.said, child.label)
        label = Text(f"{name}  {compact(child.tokens)}  {child.share:.0%}")
        if child.children:
            add_branches(node.add(label, expand=True), child, t)
        else:
            node.add_leaf(label)


class SpectrumView(VerticalScroll):
    class LeakAction(Message):
        def __init__(self, action: str, leak: Leak) -> None:
            super().__init__()
            self.action = action
            self.leak = leak

    def __init__(self, services: Services, catalog: Catalog) -> None:
        super().__init__(id="spectrum", classes="view")
        self._services = services
        self._t = catalog
        self.result: SpectrumResult | None = None
        self.selected_leak: Leak | None = None
        self._loaded_runs = False
        self._options_ready = False
        self._pending = LATEST
        self._known: set[str] = {LATEST, ALL_IMPORTED}

    def compose(self) -> ComposeResult:
        t = self._t
        with Horizontal(id="spectrum-bar"):
            yield Select(
                [(t("spectrum.latest"), LATEST), (t("spectrum.imported"), ALL_IMPORTED)],
                value=LATEST,
                allow_blank=False,
                id="spectrum-run",
            )
            yield Button(t("spectrum.import"), id="spectrum-import", compact=True)
        yield Static("", id="spectrum-label")
        yield Static("", id="spectrum-audit")
        yield Static("", id="spectrum-overhead")
        yield Static(Content.styled(t("spectrum.empty"), "$text-muted"), id="spectrum-empty")
        with Horizontal(id="metrics"):
            for key in ("tokens", "cache", "cost", "utilization"):
                with Vertical(classes="metric card", id=f"metric-{key}"):
                    yield Static(t(f"spectrum.{key}"), classes="metric-title")
                    yield Static("", classes="metric-value")
                    yield Static("", classes="metric-note")
        with TabbedContent(id="spectrum-tabs"):
            for view in VIEWS:
                with TabPane(t(f"spectrum.tab_{view.value}"), id=f"tab-{view.value}"):
                    yield Static("", id=f"bars-{view.value}", classes="bars")
            with TabPane(t("spectrum.tab_tree"), id="tab-tree"):
                yield Tree("", id="spectrum-tree")
            with TabPane(t("spectrum.tab_leaks"), id="tab-leaks"):
                yield DataTable(id="leaks", cursor_type="row", zebra_stripes=True)
                yield Static("", id="leak-suggestion")
                with FlowRow(id="leak-actions", classes="button-row"):
                    yield Button(t("spectrum.use_gateway"), id="leak-gateway", compact=True)
                    yield Button(t("spectrum.reindex"), id="leak-reindex", compact=True)
                    yield Button(t("spectrum.split"), id="leak-split", compact=True)
            with TabPane(t("spectrum.tab_plan"), id="tab-plan"):
                yield Static(t("spectrum.windows"), classes="card-title")
                yield Static("", id="plan-windows", classes="bars")
                yield Static(t("spectrum.weeks"), classes="card-title")
                yield Static("", id="plan-weeks", classes="bars")

    def on_mount(self) -> None:
        with suppress(NoMatches):
            self._setup()

    def _setup(self) -> None:
        table = self.query_one("#leaks", DataTable)
        for key in LEAK_COLUMNS:
            table.add_column(self._t(f"spectrum.{key}"), key=key)
        self._show_actions(None)
        self._set_loaded(False)

    def activate(self, run_id: str | None = None) -> None:
        if run_id is not None:
            self._pending = run_id
        if not self._loaded_runs:
            self._loaded_runs = True
            self.load_runs()
        elif self._options_ready and run_id is not None:
            self.choose(run_id)

    @work(thread=True, exclusive=True, group="spectrum-runs", exit_on_error=False)
    def load_runs(self) -> None:
        try:
            runs = self._services.recent_runs()
        except Exception:
            runs = ()
        self.app.call_from_thread(self._apply_runs, runs)

    def _apply_runs(self, runs: tuple[Run, ...]) -> None:
        t = self._t
        options = [(t("spectrum.latest"), LATEST), (t("spectrum.imported"), ALL_IMPORTED)]
        options.extend((run_option(run, self._t), run.id) for run in runs[:50])
        picker = self.query_one("#spectrum-run", Select)
        picker.set_options(options)
        self._known = {value for _, value in options}
        self._options_ready = True
        self.choose(self._pending)

    def choose(self, run_id: str) -> None:
        picker = self.query_one("#spectrum-run", Select)
        if run_id not in self._known:
            run_id = LATEST
        if picker.value != run_id:
            picker.value = run_id
        else:
            self.load(run_id)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "spectrum-run":
            event.stop()
            value = event.value
            self.load(value if isinstance(value, str) else LATEST)

    @work(thread=True, exclusive=True, group="spectrum", exit_on_error=False)
    def load(self, run_id: str) -> None:
        try:
            result = self._services.spectrum(run_id)
        except CuantaError as error:
            self.app.call_from_thread(self._failed, str(error), error.hint)
            return
        except Exception as error:
            self.app.call_from_thread(self._failed, str(error), "")
            return
        self.app.call_from_thread(self.show, result)

    def _set_loaded(self, loaded: bool) -> None:
        self.query_one("#spectrum-empty").display = not loaded
        self.query_one("#metrics").display = loaded
        self.query_one("#spectrum-tabs").display = loaded

    def _failed(self, error: str, hint: str) -> None:
        self.result = None
        self._set_loaded(False)
        if "no runs recorded" not in error:
            message = self._t("spectrum.failed", error=error, hint=hint)
            self.app.notify(message, severity="error")

    def _metric(self, key: str, value: str, note: str) -> None:
        card = self.query_one(f"#metric-{key}")
        card.query_one(".metric-value", Static).update(Content.styled(value, "bold"))
        card.query_one(".metric-note", Static).update(Content.styled(note, "$text-muted"))

    def show(self, result: SpectrumResult) -> None:
        self.result = result
        t = self._t
        report = result.report
        totals = report.totals
        self._set_loaded(True)
        self.query_one("#spectrum-label", Static).update(self._label(result))
        audit = self.query_one("#spectrum-audit", Static)
        audit.update(self._audit(result))
        audit.display = bool(result.audits)
        overhead = self.query_one("#spectrum-overhead", Static)
        lines = overhead_messages(result.overhead)
        overhead.update(
            Content("\n").join(
                [
                    Content.styled(t("result.overhead_title"), "$text-muted"),
                    *(Content(t.message(line)) for line in lines),
                ]
            )
            if lines
            else ""
        )
        overhead.display = bool(lines)
        self._metric(
            "tokens", compact(totals.total), t("spectrum.requests", count=f"{totals.requests:,}")
        )
        self._metric("cache", f"{totals.cache_share:.0%}", "")
        cost = report.cost
        value = money(cost.value) if cost.value is not None else t("spectrum.na")
        self._metric("cost", value, t.message(cost.message, cost.source))
        use = report.utilization
        if use.value is None:
            self._metric("utilization", t("spectrum.na"), t.message(use.why))
        else:
            self._metric("utilization", f"{use.value:.0%}", t("spectrum.heuristic_label"))
        empty = t("spectrum.no_rows")
        for view in VIEWS:
            rows = [(row.key, row.tokens, row.share) for row in result.rows(view)[:12]]
            self.query_one(f"#bars-{view.value}", Static).update(bar_lines(rows, empty))
        self._show_tree(report.tree)
        self._show_leaks(report.leaks)
        windows, weeks = result.windows()
        none = t("spectrum.no_windows")
        self.query_one("#plan-windows", Static).update(bar_lines(window_rows(windows[-12:]), none))
        self.query_one("#plan-weeks", Static).update(bar_lines(window_rows(weeks[-8:]), none))

    def _audit(self, result: SpectrumResult) -> Content:
        t = self._t
        if not result.audits:
            return Content("")
        lines = [Content.styled(t("pipeline.audit"), "$text-muted")]
        for audit in result.audits:
            ok = audit.status == AuditStatus.MATCH.value
            state = Status.OK if ok else Status.FAIL
            if audit.status == AuditStatus.NOT_RUN.value:
                state = Status.SKIP
            cause = (
                "" if ok else f"  {t.message(parse_english(audit.cause, 'audit.'), audit.cause)}"
            )
            lines.append(
                Content.assemble(
                    (f"{glyph(state)} ", status_style(state)),
                    (audit.agent, "bold"),
                    f"  {audit.planned} → {audit.actual or '–'}",
                    (cause, "$text-muted"),
                )
            )
        return Content("\n").join(lines)

    def _label(self, result: SpectrumResult) -> Content:
        t = self._t
        pairs: list[tuple[str, str]]
        if len(result.runs) == 1:
            run = result.runs[0]
            pairs = [
                (t("ledger.field_id"), run.id),
                (t("ledger.field_kind"), t.keyed("run_kind", run.kind)),
            ]
            if run.hu_ref:
                pairs.append((t("ledger.field_story"), run.hu_ref))
        elif result.runs:
            pairs = [(t("ledger.field_story"), result.runs[0].hu_ref), ("", f"{len(result.runs)}")]
        else:
            pairs = [(t("spectrum.label"), t.message(result.report.title, result.report.label))]
        parts: list[str | tuple[str, str]] = []
        for label, value in pairs:
            if label:
                parts.append((f"{label} ", "$text-muted"))
            parts.append((value, "bold"))
            parts.append("    ")
        return Content.assemble(*parts)

    def _show_tree(self, branch: Branch) -> None:
        tree: Tree[None] = self.query_one("#spectrum-tree", Tree)
        tree.clear()
        tree.root.set_label(
            Text(f"{self._t.message(branch.said, branch.label)}  {compact(branch.tokens)}")
        )
        add_branches(tree.root, branch, self._t)
        tree.root.expand()

    def _show_leaks(self, leaks: tuple[Leak, ...]) -> None:
        table = self.query_one("#leaks", DataTable)
        table.clear()
        for index, leak in enumerate(leaks):
            table.add_row(
                Text(self._t.keyed("leak_kind", leak.kind.value)),
                Text(leak.subject),
                Text(leak.agent or "–"),
                Text(compact(leak.tokens), justify="right"),
                Text(self._t.message(leak.message)),
                key=str(index),
            )
        suggestion = self.query_one("#leak-suggestion", Static)
        if not leaks:
            suggestion.update(Content.styled(self._t("spectrum.leaks_empty"), "$text-muted"))
            self._show_actions(None)
        else:
            self._select_leak(leaks[0])

    def _select_leak(self, leak: Leak) -> None:
        self.selected_leak = leak
        suggestions = self.result.report.suggestions if self.result is not None else ()
        action = next(
            (self._t.message(item.message) for item in suggestions if item.leak is leak.kind), ""
        )
        self.query_one("#leak-suggestion", Static).update(Content.styled(action, "$accent"))
        self._show_actions(leak)

    def _show_actions(self, leak: Leak | None) -> None:
        kind = leak.kind if leak is not None else None
        self.query_one("#leak-gateway").display = kind in GATEWAY_LEAKS
        self.query_one("#leak-reindex").display = kind is LeakKind.REPEATED_READ
        self.query_one("#leak-split").display = kind is LeakKind.COMPACTION
        self.query_one("#leak-actions").display = (
            kind is not None and kind is not LeakKind.MODEL_SWITCH
        )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "leaks" or self.result is None:
            return
        event.stop()
        key = event.row_key.value
        if key is not None and key.isdigit() and int(key) < len(self.result.report.leaks):
            self._select_leak(self.result.report.leaks[int(key)])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id or ""
        if button == "spectrum-import":
            event.stop()
            self.import_sessions()
        elif button.startswith("leak-") and self.selected_leak is not None:
            event.stop()
            self.post_message(self.LeakAction(button.removeprefix("leak-"), self.selected_leak))

    @work(thread=True, exclusive=True, group="spectrum-import", exit_on_error=False)
    def import_sessions(self) -> None:
        self.app.call_from_thread(self._importing, True)
        try:
            added = self._services.import_sessions()
        except Exception as error:
            message = self._t("spectrum.import_failed", error=str(error))
            self.app.call_from_thread(self.app.notify, message, severity="error")
            self.app.call_from_thread(self._importing, False)
            return
        count = f"{sum(added.values()):,}"
        self.app.call_from_thread(self.app.notify, self._t("spectrum.imported_done", count=count))
        self.app.call_from_thread(self._importing, False)
        self.app.call_from_thread(self.choose, ALL_IMPORTED)

    def _importing(self, busy: bool) -> None:
        button = self.query_one("#spectrum-import", Button)
        button.disabled = busy
        button.label = self._t("spectrum.importing" if busy else "spectrum.import")
