from __future__ import annotations

from contextlib import suppress

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from cuanta.application.results import RunFile
from cuanta.tui.i18n import Catalog
from cuanta.tui.services import Services

CONTEXT_LINES = 12
MAX_LINES = 400


def diff_content(diff: str) -> Content:
    lines: list[Content] = []
    for line in diff.splitlines()[:MAX_LINES]:
        if line.startswith(("+++", "---")):
            lines.append(Content.styled(line, "bold $text-muted"))
        elif line.startswith("@@"):
            lines.append(Content.styled(line, "$accent"))
        elif line.startswith("+"):
            lines.append(Content.styled(line, "$success"))
        elif line.startswith("-"):
            lines.append(Content.styled(line, "$error"))
        else:
            lines.append(Content(line))
    return Content("\n").join(lines)


def excerpt_content(text: str, line: int) -> Content:
    rows = text.splitlines()
    if line > 0:
        start = max(1, line - CONTEXT_LINES)
        end = min(len(rows), line + CONTEXT_LINES)
    else:
        start, end = 1, min(len(rows), MAX_LINES)
    width = len(str(end))
    lines: list[Content] = []
    for number in range(start, end + 1):
        body = rows[number - 1] if number - 1 < len(rows) else ""
        gutter = f"{number:>{width}} │ "
        if number == line:
            lines.append(Content.styled(f"{gutter}{body}", "bold reverse"))
        else:
            lines.append(Content.assemble((gutter, "$text-muted"), body))
    return Content("\n").join(lines)


class RunFileScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", show=False)]

    def __init__(
        self, services: Services, catalog: Catalog, run_id: str, path: str, line: int
    ) -> None:
        super().__init__(classes="run-file-screen")
        self._services = services
        self._t = catalog
        self.run_id = run_id
        self.path = path
        self.line = line

    def compose(self) -> ComposeResult:
        t = self._t
        with Vertical(id="run-file-card", classes="card"):
            title = f"{self.path}:{self.line}" if self.line else self.path
            yield Static(title, classes="card-title", id="run-file-title")
            yield Static(Content.styled(t("result.loading"), "$text-muted"), id="run-file-mode")
            with VerticalScroll(id="run-file-scroll"):
                yield Static("", id="run-file-body")
            yield Button(t("result.close"), id="run-file-close", compact=True)

    def on_mount(self) -> None:
        with suppress(NoMatches):
            self.load()

    @work(thread=True, exit_on_error=False)
    def load(self) -> None:
        try:
            found = self._services.result_file(self.run_id, self.path)
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        self.app.call_from_thread(self.show, found)

    def show(self, found: RunFile) -> None:
        with suppress(NoMatches):
            self._show(found)

    def _show(self, found: RunFile) -> None:
        t = self._t
        mode = self.query_one("#run-file-mode", Static)
        body = self.query_one("#run-file-body", Static)
        if self.line == 0 and found.diff:
            mode.update(Content.styled(t("result.diff_mode"), "$text-muted"))
            body.update(diff_content(found.diff))
            return
        if found.after is None:
            mode.update(Content.styled(t("result.file_missing"), "$warning"))
            body.update("")
            return
        key = "result.file_mode" if self.line else "result.no_diff"
        mode.update(Content.styled(t(key), "$text-muted"))
        body.update(excerpt_content(found.after, self.line))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
