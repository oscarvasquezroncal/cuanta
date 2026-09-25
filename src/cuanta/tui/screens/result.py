from __future__ import annotations

from contextlib import suppress

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.events import Resize
from textual.message import Message
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Markdown,
    Static,
    TabbedContent,
    TabPane,
)

from cuanta.application.results import ResultView
from cuanta.domain.mandate import MandateRequest, MandateType
from cuanta.domain.messages import msg
from cuanta.domain.overhead import overhead_messages
from cuanta.domain.report import link_file_refs
from cuanta.tui.fmt import money
from cuanta.tui.i18n import Catalog
from cuanta.tui.screens.run_file import RunFileScreen
from cuanta.tui.services import Services
from cuanta.tui.widgets.flow import FlowRow

FILE_SCHEME = "cuanta-file:"
NARROW_WIDTH = 110
INVESTIGATION = MandateType.INVESTIGATION.value


def parse_file_link(href: str) -> tuple[str, int] | None:
    if not href.startswith(FILE_SCHEME):
        return None
    path, _, line = href.removeprefix(FILE_SCHEME).rpartition(":")
    if not path or not line.isdigit():
        return None
    return path, int(line)


class ResultScreen(Screen[None]):
    BINDINGS = [Binding("escape", "close", show=False)]

    class ContinueRequested(Message):
        def __init__(self, request: MandateRequest) -> None:
            super().__init__()
            self.request = request

    class OpenSpectrum(Message):
        def __init__(self, run_id: str) -> None:
            super().__init__()
            self.run_id = run_id

    def __init__(self, services: Services, catalog: Catalog, view: ResultView) -> None:
        super().__init__(classes="result-screen")
        self._services = services
        self._t = catalog
        self.view = view

    @property
    def investigation(self) -> bool:
        return self.view.task_type == INVESTIGATION

    def compose(self) -> ComposeResult:
        t = self._t
        view = self.view
        with Horizontal(id="result-bar"):
            yield Static(t("result.title"), classes="card-title", id="result-title")
            yield Static(self._status(), id="result-status")
            yield Button(t("result.close"), id="result-close", compact=True)
        yield Static(self._facts(), id="result-facts")
        if view.fallback_error:
            yield Static(
                Content.styled(
                    t.message(
                        msg(
                            "instinct.fallback",
                            backend=view.fallback_from.capitalize(),
                            error=view.fallback_error,
                        )
                    ),
                    "$warning",
                ),
                id="result-instinct-fallback",
            )
        if view.simple:
            yield Static(Content.styled(t("result.simple_note"), "$warning"), id="result-simple")
        yield Static(self._split(), id="result-split")
        with FlowRow(id="result-actions", classes="button-row"):
            yield Button(
                t("result.save_docs"),
                id="result-save",
                variant="primary" if self.investigation else "default",
                compact=True,
            )
            yield Button(t("result.copy"), id="result-copy", compact=True)
            yield Button(
                t("result.continue"),
                id="result-continue",
                variant="default" if self.investigation else "primary",
                compact=True,
            )
            yield Button(t("result.spectrum"), id="result-spectrum", compact=True)
            yield Button(t("result.export"), id="result-export", compact=True)
        with TabbedContent(initial="tab-report", id="result-tabs"):
            with (
                TabPane(t("result.tab_report"), id="tab-report"),
                VerticalScroll(id="result-report-scroll"),
            ):
                if view.text.strip():
                    yield Markdown(
                        link_file_refs(view.text, FILE_SCHEME),
                        id="result-report",
                        open_links=False,
                    )
                else:
                    yield Static(
                        Content.styled(t("result.no_report"), "$text-muted"),
                        id="result-report-empty",
                    )
            with (
                TabPane(t("result.tab_files"), id="tab-files"),
                Vertical(id="result-files-pane"),
            ):
                yield DataTable(id="result-files", cursor_type="row", zebra_stripes=True)
                yield Static(
                    Content.styled(t("result.files_hint"), "$text-muted"),
                    id="result-files-hint",
                )
            with (
                TabPane(t("result.tab_consumption"), id="tab-consumption"),
                VerticalScroll(id="result-consumption-scroll"),
            ):
                yield DataTable(id="result-agents", cursor_type="none", zebra_stripes=True)
                yield Static(self._consumption(), id="result-consumption")
        yield Footer()

    def _status(self) -> Content:
        t = self._t
        run = self.view.run
        label = t.keyed("run_status", run.status)
        style = "$success" if run.status in {"ok", "completed"} else "$error"
        return Content.styled(f" {label} ", f"bold {style}")

    def _facts(self) -> Content:
        t = self._t
        view = self.view
        kind = view.task_type
        kind_label = t(f"wizard.intent_{kind}") if kind else t.keyed("run_kind", view.run.kind)
        duration = (
            t("result.seconds", seconds=f"{view.duration_s:,.0f}")
            if view.duration_s is not None
            else t("spectrum.na")
        )
        mode = t("result.mode_simple") if view.simple else t("result.mode_pipeline")
        parts = [
            kind_label,
            mode,
            duration,
            money(view.run.cost_usd, t("spectrum.na")),
            view.run.model or t("wizard.engine_default"),
        ]
        return Content.styled("  ·  ".join(parts), "$text-muted")

    def _split(self) -> Content:
        t = self._t
        split = self.view.split
        if split is None:
            return Content.styled(t("result.split_unknown"), "$text-muted")
        return Content.assemble(
            (f"{t('result.split_title')}  ", "$text-muted"),
            (
                t(
                    "result.split",
                    fixed=f"{split.fixed:,}",
                    share=f"{split.fixed_share:.0%}",
                    request=f"{split.request:,}",
                    total=f"{split.first_request:,}",
                ),
                "",
            ),
        )

    def _consumption(self) -> Content:
        t = self._t
        run = self.view.run
        rows = [
            t("result.cost_line", cost=money(run.cost_usd, t("spectrum.na"))),
            t("result.tests_line", tests=self.view.tests or t("spectrum.na")),
        ]
        overhead = self.view.overhead
        if overhead is not None:
            rows.append("")
            rows.append(t("result.overhead_title"))
            rows.extend(t.message(line) for line in overhead_messages(overhead))
        return Content.styled("\n".join(rows), "$text-muted")

    def on_mount(self) -> None:
        with suppress(NoMatches):
            self._fill()

    def on_resize(self, event: Resize) -> None:
        self.set_class(event.size.width < NARROW_WIDTH, "-narrow")

    def _fill(self) -> None:
        t = self._t
        files = self.query_one("#result-files", DataTable)
        files.add_column(t("result.col_file"), key="file")
        for path in self.view.changed_files:
            files.add_row(path, key=path)
        self.query_one("#result-files-hint").display = not self.view.changed_files
        agents = self.query_one("#result-agents", DataTable)
        agents.add_column(t("result.col_agent"), key="agent")
        agents.add_column(t("result.col_tokens"), key="tokens")
        for agent, tokens in self.view.tokens_by_agent.items():
            agents.add_row(agent, f"{tokens:,}")
        self.query_one("#result-continue", Button).disabled = self.view.follow_up is None

    def on_markdown_link_clicked(self, event: Markdown.LinkClicked) -> None:
        event.stop()
        target = parse_file_link(event.href)
        if target is not None:
            self.open_file(target[0], target[1])

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "result-files":
            return
        event.stop()
        path = event.row_key.value
        if path:
            self.open_file(path, 0)

    def open_file(self, path: str, line: int) -> None:
        self.app.push_screen(RunFileScreen(self._services, self._t, self.view.run.id, path, line))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button = event.button.id
        if button == "result-close":
            self.action_close()
        elif button == "result-save":
            self.save()
        elif button == "result-copy":
            self.copy_report()
        elif button == "result-continue" and self.view.follow_up is not None:
            self.app.post_message(self.ContinueRequested(self.view.follow_up))
            self.dismiss(None)
        elif button == "result-spectrum":
            self.app.post_message(self.OpenSpectrum(self.view.run.id))
            self.dismiss(None)
        elif button == "result-export":
            self.export()

    @work(thread=True, exit_on_error=False)
    def save(self) -> None:
        try:
            path = self._services.save_result(self.view.run.id)
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        self.app.call_from_thread(self.app.notify, self._t("result.saved", path=path))

    @work(thread=True, exit_on_error=False)
    def export(self) -> None:
        try:
            path = self._services.export_result(self.view.run.id)
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        self.app.call_from_thread(self.app.notify, self._t("result.exported", path=path))

    @work(thread=True, exit_on_error=False)
    def copy_report(self) -> None:
        copied = self._services.copy(self.view.text)
        key = "result.copied" if copied else "result.copy_failed"
        self.app.call_from_thread(self.app.notify, self._t(key))

    def action_close(self) -> None:
        self.dismiss(None)
