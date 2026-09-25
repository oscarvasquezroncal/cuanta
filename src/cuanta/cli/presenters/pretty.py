from __future__ import annotations

from dataclasses import dataclass, field

from rich import box
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel as RichPanel
from rich.spinner import Spinner
from rich.table import Table as RichTable
from rich.text import Text
from rich.theme import Theme
from rich.tree import Tree

from cuanta.cli.document import (
    Block,
    Commands,
    Document,
    Heading,
    Hint,
    KeyValues,
    Line,
    MarkdownText,
    MascotBlock,
    Panel,
    Swatches,
    Table,
    TreeNode,
    Verbatim,
    Wordmark,
)
from cuanta.cli.mascot import michi, pixel_michi
from cuanta.cli.output import OutputSettings
from cuanta.cli.presenters.base import STATUS_STYLE
from cuanta.cli.theme import PALETTE, WORDMARK, semantic_colors, wordmark_colors
from cuanta.domain.errors import CuantaError
from cuanta.domain.progress import Metric, Note, ProgressEvent, Status, StepFinished, StepStarted
from cuanta.domain.voice import TAGLINE, Mood, glyph

PAW_FRAMES = ("🐾    ", " 🐾   ", "  🐾  ", "   🐾 ", "    🐾")
ASCII_FRAMES = ("o    ", " o   ", "  o  ", "   o ", "    o")


def build_theme(settings: OutputSettings) -> Theme:
    return Theme(semantic_colors(settings.theme))


def build_console(settings: OutputSettings, force_terminal: bool | None = None) -> Console:
    return Console(
        theme=build_theme(settings),
        emoji=settings.emoji,
        highlight=False,
        force_terminal=force_terminal,
    )


def paw_spinner(settings: OutputSettings) -> Spinner:
    spinner = Spinner("dots", style="brand")
    spinner.frames = list(PAW_FRAMES if settings.emoji else ASCII_FRAMES)
    spinner.interval = 120.0
    return spinner


@dataclass(slots=True)
class _Step:
    label: str
    status: Status | None = None
    detail: str = ""


@dataclass(slots=True)
class _Checklist:
    settings: OutputSettings
    steps: dict[str, _Step] = field(default_factory=dict)
    spinner: Spinner | None = None

    def renderable(self) -> RenderableType:
        rows: list[RenderableType] = []
        for step in self.steps.values():
            if step.status is None:
                spinner = self.spinner or paw_spinner(self.settings)
                self.spinner = spinner
                grid = RichTable.grid(padding=(0, 1))
                grid.add_row(spinner, Text(step.label, style="title"))
                rows.append(grid)
                continue
            rows.append(_status_line(step.status, step.label, step.detail, self.settings))
        return Group(*rows)


def _status_line(status: Status, text: str, detail: str, settings: OutputSettings) -> Text:
    line = Text()
    line.append(glyph(status, settings.unicode), style=STATUS_STYLE[status])
    line.append(" ")
    line.append(text, style="title")
    if detail:
        line.append(f"  {detail}", style="muted")
    return line


class PrettyPresenter:
    def __init__(self, settings: OutputSettings, console: Console | None = None) -> None:
        self._settings = settings
        self._console = console if console is not None else build_console(settings)
        self._checklist = _Checklist(settings)
        self._live: Live | None = None

    @property
    def console(self) -> Console:
        return self._console

    def publish(self, event: ProgressEvent) -> None:
        match event:
            case StepStarted(key=key, label=label):
                self._checklist.steps[key] = _Step(label)
                self._refresh()
            case StepFinished(key=key, status=status, detail=detail):
                step = self._checklist.steps.setdefault(key, _Step(key))
                step.status = status
                step.detail = detail
                self._refresh()
                if all(item.status is not None for item in self._checklist.steps.values()):
                    self._stop_live()
            case Note(status=status, text=text):
                self._print(_status_line(status, text, "", self._settings))
            case Metric(label=label, value=value):
                self._print(_status_line(Status.INFO, f"{label}: {value}", "", self._settings))

    def render(self, document: Document) -> None:
        self._stop_live()
        for index, block in enumerate(document.blocks):
            if index and isinstance(
                block, Table | Panel | TreeNode | MascotBlock | Swatches | Commands
            ):
                self._console.print()
            if isinstance(block, Commands):
                self._commands(block)
                continue
            self._console.print(self._block(block))

    def _commands(self, block: Commands) -> None:
        if block.title:
            self._console.print(Text(block.title, style="bold title"))
        for label, command in block.items:
            self._console.print(Text(f"  {label}", style="muted"))
            self._console.print(
                Text(f"    {command}", style="info", no_wrap=True, overflow="ignore"),
                soft_wrap=True,
            )

    def fail(self, error: CuantaError) -> None:
        self._stop_live()
        lines: list[RenderableType] = [self._mascot(Mood.ALARMED, "")]
        lines.append(_status_line(Status.FAIL, error.message, "", self._settings))
        if error.hint:
            lines.append(Text(f"→ {error.hint}", style="muted"))
        self._console.print(
            RichPanel(
                Group(*lines),
                box=box.ROUNDED,
                border_style="err",
                padding=(1, 2),
                title=Text("hiss", style="err"),
                title_align="left",
                expand=False,
            )
        )

    def close(self) -> None:
        self._stop_live()

    def _print(self, renderable: RenderableType) -> None:
        if self._live is not None:
            self._live.console.print(renderable)
            return
        self._console.print(renderable)

    def _refresh(self) -> None:
        if not self._console.is_terminal:
            return
        if self._live is None:
            self._live = Live(
                self._checklist.renderable(),
                console=self._console,
                refresh_per_second=8,
                transient=False,
            )
            self._live.start()
            return
        self._live.update(self._checklist.renderable())

    def _stop_live(self) -> None:
        if self._live is not None:
            self._live.update(self._checklist.renderable())
            self._live.stop()
            self._live = None
            self._checklist.steps.clear()
        elif self._checklist.steps and not self._console.is_terminal:
            self._console.print(self._checklist.renderable())
            self._checklist.steps.clear()

    def _pixel_michi(self) -> bool:
        settings = self._settings
        return settings.unicode and settings.emoji and self._console.color_system == "truecolor"

    def _mascot(self, mood: Mood, caption: str) -> Text:
        if self._pixel_michi():
            text = pixel_michi(mood, self._settings.theme)
            if caption:
                text.append("\n")
                text.append(caption, style="muted")
            return text
        text = Text()
        for index, row in enumerate(michi(mood)):
            if index:
                text.append("\n")
            text.append(row, style="brand" if mood is not Mood.ALARMED else "err")
        if caption:
            text.append("\n")
            text.append(caption, style="muted")
        return text

    def _block(self, block: Block) -> RenderableType:
        match block:
            case Heading(text=text):
                return Text(text, style="bold title")
            case Line(text=text, status=status, style=style):
                if status is None:
                    return Text(text, style=style or "title")
                return _status_line(status, text, "", self._settings)
            case KeyValues(rows=rows):
                grid = RichTable.grid(padding=(0, 2))
                grid.add_column(style="muted", no_wrap=True)
                grid.add_column(style="title")
                for key, value in rows:
                    grid.add_row(Text(key), Text(value))
                return grid
            case Table():
                return self._table(block)
            case TreeNode():
                tree = Tree(Text(block.label, style="bold title"), guide_style="muted")
                self._grow(tree, block.children)
                return tree
            case MascotBlock(mood=mood, caption=caption):
                return self._mascot(mood, caption)
            case Wordmark(tagline=tagline):
                return self._wordmark(tagline)
            case Swatches():
                return self._swatches()
            case Hint(text=text):
                return Text(f"→ {text}" if self._settings.unicode else f"> {text}", style="muted")
            case Verbatim(text=text):
                return Text(text)
            case MarkdownText(text=text):
                return Markdown(text)
            case Commands(title=title, items=items):
                lines = [Text(title, style="bold title")] if title else []
                for label, command in items:
                    lines.append(Text(f"  {label}", style="muted"))
                    lines.append(Text(f"    {command}", style="info", no_wrap=True))
                return Group(*lines)
            case Panel(title=title, blocks=blocks):
                return RichPanel(
                    Group(*(self._block(inner) for inner in blocks)),
                    box=box.ROUNDED,
                    border_style="muted",
                    padding=(1, 2),
                    title=Text(title, style="brand") if title else None,
                    title_align="left",
                    expand=False,
                )

    def _grow(self, tree: Tree, children: tuple[TreeNode, ...]) -> None:
        for child in children:
            branch = tree.add(Text(child.label, style="title"))
            self._grow(branch, child.children)

    def _table(self, table: Table) -> RichTable:
        rich_table = RichTable(
            title=Text(table.title, style="bold title") if table.title else None,
            title_justify="left",
            box=box.SIMPLE_HEAD,
            header_style="muted",
            border_style="muted",
        )
        for column in table.columns:
            rich_table.add_column(Text(column.name), justify="right" if column.numeric else "left")
        for row in table.rows:
            rich_table.add_row(*(Text(cell) for cell in row))
        return rich_table

    def _wordmark(self, tagline: bool) -> RenderableType:
        text = Text()
        for letter, color in zip(WORDMARK, wordmark_colors(self._settings.theme), strict=True):
            text.append(letter, style=f"bold {color}")
        if not tagline:
            return text
        return Group(text, Text(TAGLINE, style="muted"))

    def _swatches(self) -> RenderableType:
        colors = semantic_colors(self._settings.theme)
        row = Text()
        block_char = "██" if self._settings.unicode else "##"
        for swatch in PALETTE:
            row.append(block_char, style=colors[swatch.semantic])
            row.append(f" {swatch.token}  ", style="muted")
        return Padding(row, (0, 0))
