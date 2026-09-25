from __future__ import annotations

from rich.syntax import Syntax
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, Static, TabbedContent, TabPane

from cuanta.application.cat_capsule import CapsuleView
from cuanta.application.tests_view import Hairball, TestsSummary
from cuanta.domain.capsules import Level
from cuanta.tui.i18n import Catalog
from cuanta.tui.services import Services

LEVELS = (Level.L1, Level.L2, Level.L3)
MAX_LINES = 5000
PYTHON_RUNNERS = frozenset({"pytest"})


def lexer_for(runner: str) -> str:
    return "pytb" if runner in PYTHON_RUNNERS else "text"


def render_lines(
    lines: tuple[tuple[int, str], ...], lexer: str, dark: bool, query: str = ""
) -> tuple[Text, int]:
    shown = lines[:MAX_LINES]
    width = len(str(shown[-1][0])) if shown else 1
    syntax = Syntax("", lexer, theme="ansi_dark" if dark else "ansi_light")
    body = syntax.highlight("\n".join(text for _, text in shown))
    rows = body.split("\n", allow_blank=True)
    output = Text(no_wrap=True)
    matches = 0
    for index, (number, _) in enumerate(shown):
        row = rows[index] if index < len(rows) else Text()
        if query:
            matches += row.highlight_words([query], style="reverse", case_sensitive=False)
        output.append(f"{number:>{width}}  ", style="dim")
        output.append_text(row)
        if index < len(shown) - 1:
            output.append("\n")
    return output, matches


class CapsuleScreen(Screen[None]):
    BINDINGS = [
        Binding("escape", "back", show=False),
        Binding("ctrl+f", "search", show=False),
    ]

    class FixRequested(Message):
        def __init__(self, hairball: Hairball | None, capsule: str) -> None:
            super().__init__()
            self.hairball = hairball
            self.capsule = capsule

    def __init__(
        self,
        services: Services,
        catalog: Catalog,
        summary: TestsSummary,
        hairball: Hairball | None,
    ) -> None:
        super().__init__(id="capsule-screen")
        self._services = services
        self._t = catalog
        self.summary = summary
        self.hairball = hairball
        self.views: dict[Level, CapsuleView] = {}

    def compose(self) -> ComposeResult:
        t = self._t
        with Vertical(id="capsule-head", classes="card"):
            yield Static(t("capsule.title", id=self.summary.capsule), classes="card-title")
            if self.hairball is not None:
                yield Static(
                    Content.assemble(
                        (self.hairball.id, "bold $accent"),
                        f"  {self.hairball.location or '–'}\n",
                        (self.hairball.verbatim, "$text-muted"),
                    ),
                    id="capsule-hairball",
                )
        with Horizontal(id="capsule-bar"):
            yield Input(placeholder=t("capsule.search"), id="capsule-search")
            yield Static("", id="capsule-matches")
            yield Button(t("capsule.fix"), id="capsule-fix", variant="primary", compact=True)
            yield Button(t("capsule.back"), id="capsule-back", compact=True)
        with TabbedContent(initial=Level.L2.value, id="capsule-tabs"):
            for level in LEVELS:
                with (
                    TabPane(t(f"capsule.{level.value.lower()}"), id=level.value),
                    VerticalScroll(classes="capsule-scroll"),
                ):
                    yield Static(
                        Content.styled(t("capsule.loading"), "$text-muted"),
                        id=f"body-{level.value}",
                        classes="capsule-body",
                    )
                    yield Static("", id=f"note-{level.value}", classes="capsule-note")
        yield Footer()

    def on_mount(self) -> None:
        motion = getattr(self.app, "motion", True) is not False
        self.query_one("#capsule-search", Input).cursor_blink = motion
        for level in LEVELS:
            self.load(level)

    @work(thread=True, exit_on_error=False)
    def load(self, level: Level) -> None:
        try:
            view = self._services.capsule(self.summary.capsule, level)
        except Exception as error:
            self.app.call_from_thread(self._failed, level, str(error))
            return
        self.app.call_from_thread(self._loaded, level, view)

    def _failed(self, level: Level, error: str) -> None:
        body = self.query_one(f"#body-{level.value}", Static)
        body.update(Content.styled(self._t("capsule.failed", error=error), "$error"))

    def _loaded(self, level: Level, view: CapsuleView) -> None:
        self.views[level] = view
        self._paint(level)

    def _paint(self, level: Level) -> int:
        view = self.views.get(level)
        if view is None:
            return 0
        query = self.query_one("#capsule-search", Input).value.strip()
        text, matches = render_lines(
            view.lines, lexer_for(self.summary.runner), self.app.current_theme.dark, query
        )
        self.query_one(f"#body-{level.value}", Static).update(text)
        note = self.query_one(f"#note-{level.value}", Static)
        if len(view.lines) > MAX_LINES:
            note.update(
                Content.styled(
                    self._t(
                        "capsule.truncated",
                        shown=f"{MAX_LINES:,}",
                        total=f"{len(view.lines):,}",
                        id=self.summary.capsule,
                    ),
                    "$warning",
                )
            )
        return matches

    def on_input_changed(self, event: Input.Changed) -> None:
        total = sum(self._paint(level) for level in LEVELS)
        label = self._t("capsule.matches", count=f"{total:,}") if event.value.strip() else ""
        self.query_one("#capsule-matches", Static).update(label)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "capsule-back":
            self.action_back()
        elif event.button.id == "capsule-fix":
            self.app.post_message(self.FixRequested(self.hairball, self.summary.capsule))
            self.action_back()

    def action_back(self) -> None:
        self.dismiss(None)

    def action_search(self) -> None:
        self.query_one("#capsule-search", Input).focus()
