from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, Footer, Static

from cuanta.application.new_files import NewFilePair
from cuanta.domain.errors import CuantaError
from cuanta.domain.new_files import Change, DiffRow
from cuanta.tui.i18n import Catalog
from cuanta.tui.services import Services

LEFT_STYLE = {Change.CHANGED: "$warning", Change.REMOVED: "$error"}
RIGHT_STYLE = {Change.CHANGED: "$warning", Change.ADDED: "$success"}
MARK = {Change.SAME: " ", Change.CHANGED: "~", Change.REMOVED: "-", Change.ADDED: "+"}


def side(rows: tuple[DiffRow, ...], left: bool) -> Content:
    width = len(str(max((row.left_number or row.right_number or 0) for row in rows))) if rows else 1
    lines: list[Content] = []
    for row in rows:
        number = row.left_number if left else row.right_number
        text = row.left if left else row.right
        style = (LEFT_STYLE if left else RIGHT_STYLE).get(row.change, "")
        label = f"{number:>{width}}" if number is not None else " " * width
        lines.append(
            Content.assemble(
                (f"{label} {MARK[row.change]} ", "$text-muted"),
                (text, style) if style else text,
            )
        )
    return Content("\n").join(lines)


class DiffScreen(Screen[str | None]):
    BINDINGS = [Binding("escape", "back", show=False)]

    class Resolved(Message):
        def __init__(self, new_path: str) -> None:
            super().__init__()
            self.new_path = new_path

    def __init__(self, services: Services, catalog: Catalog, new_path: str) -> None:
        super().__init__(id="diff-screen")
        self._services = services
        self._t = catalog
        self.new_path = new_path
        self.pair: NewFilePair | None = None

    def compose(self) -> ComposeResult:
        t = self._t
        with Horizontal(id="diff-bar"):
            yield Static(t("diff.title", path=self.new_path), classes="card-title", id="diff-title")
            yield Button(t("diff.keep"), id="diff-keep", compact=True)
            yield Button(t("diff.use_new"), id="diff-use", variant="primary", compact=True)
            yield Button(t("diff.back"), id="diff-back", compact=True)
        with Horizontal(id="diff-panes"):
            with Vertical(classes="card diff-pane"):
                yield Static(t("diff.mine"), classes="card-title")
                with VerticalScroll():
                    yield Static("", id="diff-left")
            with Vertical(classes="card diff-pane"):
                yield Static(t("diff.new"), classes="card-title")
                with VerticalScroll():
                    yield Static("", id="diff-right")
        yield Footer()

    def on_mount(self) -> None:
        self.load()

    @work(thread=True, exit_on_error=False)
    def load(self) -> None:
        try:
            pair = self._services.load_new_file(self.new_path)
        except CuantaError as error:
            self.app.call_from_thread(self._failed, str(error))
            return
        self.app.call_from_thread(self._show, pair)

    def _show(self, pair: NewFilePair) -> None:
        self.pair = pair
        self.query_one("#diff-left", Static).update(side(pair.rows, True))
        self.query_one("#diff-right", Static).update(side(pair.rows, False))

    def _failed(self, error: str) -> None:
        self.app.notify(self._t("diff.failed", error=error), severity="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button = event.button.id
        if button == "diff-back":
            self.action_back()
        elif button in {"diff-keep", "diff-use"}:
            self.resolve(button == "diff-keep")

    @work(thread=True, exit_on_error=False)
    def resolve(self, keep_mine: bool) -> None:
        try:
            original = self._services.resolve_new_file(self.new_path, keep_mine)
        except CuantaError as error:
            self.app.call_from_thread(self._failed, str(error))
            return
        key = "diff.kept" if keep_mine else "diff.replaced"
        path = self.new_path if keep_mine else original
        self.app.call_from_thread(self.app.notify, self._t(key, path=path))
        self.app.call_from_thread(self.app.post_message, self.Resolved(self.new_path))
        self.app.call_from_thread(self.dismiss, self.new_path)

    def action_back(self) -> None:
        self.dismiss(None)
