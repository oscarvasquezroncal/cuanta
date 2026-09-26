from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.message import Message
from textual.widgets import Button, DataTable, Sparkline, Static

from cuanta.application.home import HomeSnapshot
from cuanta.domain.fixes import FixKind, classify
from cuanta.domain.progress import Status
from cuanta.domain.voice import Mood
from cuanta.tui.cache_text import prefix_content
from cuanta.tui.commands import HEALTH, INIT, MANDATES, SPECTRUM, TESTS
from cuanta.tui.fmt import compact, glyph, grouped, run_money, status_style
from cuanta.tui.i18n import Catalog
from cuanta.tui.widgets.facts import Facts
from cuanta.tui.widgets.flow import FlowRow
from cuanta.tui.widgets.header import mood_for
from cuanta.tui.widgets.michi import Michi

QUICK_ACTIONS = (
    (INIT, "action.init"),
    (TESTS, "action.tests"),
    (MANDATES, "action.mandate"),
    (SPECTRUM, "action.spectrum"),
    (HEALTH, "action.health"),
)
FIX_LABELS = {
    FixKind.RUN: "action.run_fix",
    FixKind.OPEN: "health.open",
    FixKind.COPY: "health.copy",
}
RUN_COLUMNS = ("col_kind", "col_engine", "col_status", "col_cost", "col_started")


class HomeView(VerticalScroll):
    class Go(Message):
        def __init__(self, section: str, run_id: str = "") -> None:
            super().__init__()
            self.section = section
            self.run_id = run_id

    def __init__(self, catalog: Catalog, motion: bool = True) -> None:
        super().__init__(id="home", classes="view")
        self._t = catalog
        self._motion = motion

    def compose(self) -> ComposeResult:
        t = self._t
        with Horizontal(id="hero"):
            yield Michi(Mood.WATCHING, scale=2, motion=self._motion, id="home-michi")
            with Vertical(id="project-card", classes="card"):
                yield Static(t("home.project"), classes="card-title")
                yield Facts(Content.styled(t("app.loading"), "$text-muted"), id="project-facts")
        with Horizontal(id="next-card", classes="card"):
            yield Static(t("home.next_step"), classes="card-title", id="next-title")
            yield Static("", id="next-text")
            yield Static("", id="next-fix", classes="command")
            yield Button(t("action.run_fix"), id="next-run", compact=True)
        with FlowRow(id="actions"):
            for section, key in QUICK_ACTIONS:
                yield Button(
                    t(key),
                    id=f"action-{section}",
                    variant="primary" if section == INIT else "default",
                    compact=True,
                )
        with Horizontal(id="activity"):
            with Vertical(id="runs-card", classes="card"):
                yield Static(t("home.recent_runs"), classes="card-title")
                yield DataTable(id="runs", cursor_type="row", zebra_stripes=True)
                yield Static(t("app.skeleton"), id="no-runs", classes="skeleton")
            with Vertical(id="week-card", classes="card"):
                yield Static(t("home.week"), classes="card-title")
                yield Sparkline([0] * 7, id="week")
                yield Static("", id="week-total")
                yield Static("", id="home-prefix")

    def on_mount(self) -> None:
        table = self.query_one("#runs", DataTable)
        for key in RUN_COLUMNS:
            table.add_column(self._t(f"home.{key}"), key=key)
        self.query_one("#next-run", Button).display = False

    def show(self, snapshot: HomeSnapshot) -> None:
        t = self._t
        detection = snapshot.report.detection
        stack = detection.stack
        language = " ".join(part for part in (stack.language, stack.language_version) if part)
        facts = [
            (t("home.name"), detection.project_name),
            (t("home.stack"), language or t("header.unknown")),
            (t("home.tier"), detection.verify_tier.value),
            (t("home.evidence"), detection.verify.evidence),
            (t("home.graph"), detection.graph_mode.value),
            (
                t("home.forge"),
                t("header.forge_installed" if snapshot.initialized else "header.forge_missing"),
            ),
            (t("home.files"), grouped(detection.file_count)),
        ]
        self.query_one("#project-facts", Facts).show(facts)
        michi = self.query_one("#home-michi", Michi)
        michi.mood = mood_for(snapshot)
        michi.display = not (snapshot.initialized and snapshot.runs)
        self._show_actions(snapshot.initialized)
        self._show_next(snapshot)
        self._show_runs(snapshot)
        week = self.query_one("#week", Sparkline)
        week.data = [float(value) for value in snapshot.daily]
        total = compact(sum(snapshot.daily))
        self.query_one("#week-total", Static).update(
            Content.styled(t("home.week_total", total=total), "$text-muted")
        )
        self.query_one("#home-prefix", Static).update(prefix_content(t, snapshot.prefix))

    def _show_actions(self, initialized: bool) -> None:
        init = self.query_one(f"#action-{INIT}", Button)
        mandate = self.query_one(f"#action-{MANDATES}", Button)
        init.label = self._t("action.refresh" if initialized else "action.init")
        init.variant = "default" if initialized else "primary"
        mandate.variant = "primary" if initialized else "default"
        actions = self.query_one("#actions", FlowRow)
        first = mandate if initialized else init
        if actions.children and actions.children[0] is not first:
            actions.move_child(first, before=0)

    def _show_next(self, snapshot: HomeSnapshot) -> None:
        t = self._t
        step = snapshot.next_step
        text = self.query_one("#next-text", Static)
        command = self.query_one("#next-fix", Static)
        command.update("")
        button = self.query_one("#next-run", Button)
        title = self.query_one("#next-title", Static)
        if not snapshot.initialized:
            title.update(t("home.fresh_title"))
            text.update(Content(t("home.fresh_body")))
            button.label = t("action.init")
            button.display = True
            return
        title.update(t("home.next_step"))
        if step is None:
            text.update(Content.assemble((f"{glyph(Status.OK)} ", "$success"), t("home.all_clear")))
            button.display = False
            return
        text.update(
            Content.assemble(
                (f"{glyph(step.status)} ", status_style(step.status)),
                (t.check_name(step.name), "bold"),
                f"  {t.message(step.message, step.detail)}",
            )
        )
        command.update(Content.styled(step.fix, "$accent"))
        button.label = t(FIX_LABELS[classify(step.fix).kind])
        button.display = True

    def _show_runs(self, snapshot: HomeSnapshot) -> None:
        t = self._t
        table = self.query_one("#runs", DataTable)
        table.clear()
        for run in snapshot.runs:
            table.add_row(
                Text(t.keyed("run_kind", run.kind)),
                Text(run.engine or "–"),
                Text(f"{glyph(_run_status(run.status))} {t.keyed('run_status', run.status)}"),
                Text(run_money(run, t) if run.engine else "–", justify="right"),
                Text(run.started_at[5:16].replace("T", " ")),
                key=run.id,
            )
        empty = not snapshot.runs
        table.display = not empty
        placeholder = self.query_one("#no-runs", Static)
        placeholder.remove_class("skeleton")
        placeholder.update(Content.styled(t("home.no_runs"), "$text-muted"))
        placeholder.display = empty

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.post_message(self.Go(SPECTRUM, str(event.row_key.value or "")))


def _run_status(status: str) -> Status:
    if status in {"ok", "passed", "done", "completed", "green"}:
        return Status.OK
    if status in {"failed", "fail", "error", "red"}:
        return Status.FAIL
    if status == "running":
        return Status.RESUME
    return Status.INFO
