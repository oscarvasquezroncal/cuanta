from __future__ import annotations

import sys
from typing import TextIO

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
from cuanta.cli.mascot import michi
from cuanta.cli.theme import PALETTE, WORDMARK
from cuanta.domain.errors import CuantaError
from cuanta.domain.progress import Metric, Note, ProgressEvent, Status, StepFinished, StepStarted
from cuanta.domain.voice import TAGLINE, glyph


def _table_lines(table: Table) -> list[str]:
    headers = [column.name for column in table.columns]
    widths = [len(header) for header in headers]
    for row in table.rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def fit(cells: tuple[str, ...] | list[str]) -> str:
        parts = []
        for index, cell in enumerate(cells):
            numeric = table.columns[index].numeric
            parts.append(cell.rjust(widths[index]) if numeric else cell.ljust(widths[index]))
        return "  ".join(parts).rstrip()

    lines = [table.title] if table.title else []
    lines.append(fit(headers))
    lines.append("  ".join("-" * width for width in widths))
    lines.extend(fit(row) for row in table.rows)
    return lines


def _tree_lines(node: TreeNode, depth: int = 0) -> list[str]:
    lines = [("  " * depth) + ("- " if depth else "") + node.label]
    for child in node.children:
        lines.extend(_tree_lines(child, depth + 1))
    return lines


def block_lines(block: Block) -> list[str]:
    match block:
        case Heading(text=text):
            return [text]
        case Line(text=text, status=status):
            return [f"{glyph(status, False)} {text}" if status else text]
        case KeyValues(rows=rows):
            width = max((len(key) for key, _ in rows), default=0)
            return [f"{key.ljust(width)}  {value}".rstrip() for key, value in rows]
        case Table():
            return _table_lines(block)
        case TreeNode():
            return _tree_lines(block)
        case MascotBlock(mood=mood, caption=caption):
            art = [line.rstrip() for line in michi(mood)]
            return [*art, caption] if caption else art
        case Wordmark(tagline=tagline):
            return [WORDMARK, TAGLINE] if tagline else [WORDMARK]
        case Swatches():
            return [f"{swatch.semantic}: {swatch.token}" for swatch in PALETTE]
        case Hint(text=text):
            return [f"> {text}"]
        case Verbatim(text=text):
            return text.splitlines()
        case MarkdownText(text=text):
            return text.splitlines()
        case Commands(title=title, items=items):
            lines = [title] if title else []
            for label, command in items:
                lines.extend((f"  {label}", f"    {command}"))
            return lines
        case Panel(title=title, blocks=blocks):
            lines = [f"== {title} =="] if title else []
            for inner in blocks:
                lines.extend(block_lines(inner))
            return lines


class PlainPresenter:
    def __init__(self, out: TextIO | None = None, err: TextIO | None = None) -> None:
        self._out = out if out is not None else sys.stdout
        self._err = err if err is not None else sys.stderr

    def publish(self, event: ProgressEvent) -> None:
        match event:
            case StepStarted():
                return
            case StepFinished(key=key, status=status, detail=detail):
                suffix = f" {detail}" if detail else ""
                self._write(f"{glyph(status, False)} {key}{suffix}")
            case Note(status=status, text=text):
                self._write(f"{glyph(status, False)} {text}")
            case Metric(label=label, value=value):
                self._write(f"{glyph(Status.INFO, False)} {label}: {value}")

    def render(self, document: Document) -> None:
        for index, block in enumerate(document.blocks):
            if index and isinstance(block, (Table, Panel, TreeNode, MascotBlock)):
                self._write("")
            for line in block_lines(block):
                self._write(line)

    def fail(self, error: CuantaError) -> None:
        self._err.write(f"{glyph(Status.FAIL, False)} {error.message}\n")
        if error.hint:
            self._err.write(f"> {error.hint}\n")
        self._err.flush()

    def close(self) -> None:
        self._out.flush()

    def _write(self, line: str) -> None:
        self._out.write(line + "\n")
