from __future__ import annotations

from contextlib import suppress

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.content import Content
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static

from cuanta.application.bench import BenchResult
from cuanta.domain.bench import summarize
from cuanta.domain.costs import sum_costs
from cuanta.tui.commands import BENCH_COMMAND
from cuanta.tui.fmt import money
from cuanta.tui.i18n import Catalog

SUMMARY_COLUMNS = ("condition", "accepted", "per_accepted", "median_cost", "spent")
RUN_COLUMNS = ("task", "condition", "rep", "verdict", "tokens", "cost", "retries")


class BenchScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", show=False)]

    def __init__(self, catalog: Catalog, result: BenchResult | None) -> None:
        super().__init__()
        self._t = catalog
        self.result = result

    def compose(self) -> ComposeResult:
        t = self._t
        with Vertical(id="bench-card", classes="card"):
            yield Static(t("bench.title"), classes="card-title")
            if self.result is None:
                yield Static(t("bench.empty"), id="bench-meta")
                yield Static(Content.styled(BENCH_COMMAND, "$accent"))
            else:
                yield Static(self._meta(self.result), id="bench-meta")
                yield DataTable(id="bench-summary", cursor_type="none", zebra_stripes=True)
                yield DataTable(id="bench-runs", cursor_type="row", zebra_stripes=True)
                yield Static(
                    Content.styled(f"{self.result.folder}/report.md", "$text-muted"),
                    id="bench-report",
                )
            yield Button(t("app.tip_close"), id="bench-close", compact=True)

    def _meta(self, result: BenchResult) -> str:
        meta = result.meta
        spent = sum_costs(item.cost_usd for item in result.metrics)
        return self._t(
            "bench.meta",
            suite=meta.suite,
            tasks=len(meta.tasks),
            reps=meta.reps,
            engine=f"{meta.engine} {meta.engine_version}",
            model=meta.model,
            spent=money(spent, self._t("spectrum.na")),
            started=meta.started_at[:16].replace("T", " "),
        )

    def on_mount(self) -> None:
        with suppress(NoMatches):
            self._fill()

    def _fill(self) -> None:
        if self.result is None:
            return
        t = self._t
        summary = self.query_one("#bench-summary", DataTable)
        for key in SUMMARY_COLUMNS:
            summary.add_column(t(f"bench.col_{key}"), key=key)
        for row in summarize(self.result.metrics):
            median = row.tokens_per_accepted.median
            summary.add_row(
                row.condition.value,
                f"{row.accepted}/{row.runs}",
                "–" if median is None else f"{median:,.0f}",
                money(row.cost.median, t("spectrum.na")),
                money(row.spent_usd, t("spectrum.na")),
            )
        runs = self.query_one("#bench-runs", DataTable)
        for key in RUN_COLUMNS:
            runs.add_column(t(f"bench.col_{key}"), key=key)
        ordered = sorted(
            self.result.metrics, key=lambda item: (item.task, item.condition.value, item.rep)
        )
        for item in ordered:
            verdict = "accepted" if item.accepted else ("capped" if item.capped else "rejected")
            runs.add_row(
                item.task,
                item.condition.value,
                str(item.rep),
                t(f"bench.{verdict}"),
                f"{item.total_tokens:,}",
                money(item.cost_usd, t("spectrum.na")),
                str(item.retries),
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
