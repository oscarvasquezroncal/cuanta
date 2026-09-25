from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cuanta.domain.progress import Status
from cuanta.domain.voice import Mood

JsonValue = Any


@dataclass(frozen=True, slots=True)
class Heading:
    text: str


@dataclass(frozen=True, slots=True)
class Line:
    text: str
    status: Status | None = None
    style: str = ""


@dataclass(frozen=True, slots=True)
class KeyValues:
    rows: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    numeric: bool = False


@dataclass(frozen=True, slots=True)
class Table:
    title: str
    columns: tuple[Column, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class TreeNode:
    label: str
    children: tuple[TreeNode, ...] = ()


@dataclass(frozen=True, slots=True)
class MascotBlock:
    mood: Mood
    caption: str = ""


@dataclass(frozen=True, slots=True)
class Wordmark:
    tagline: bool = True


@dataclass(frozen=True, slots=True)
class Swatches:
    pass


@dataclass(frozen=True, slots=True)
class Hint:
    text: str


@dataclass(frozen=True, slots=True)
class Verbatim:
    text: str


@dataclass(frozen=True, slots=True)
class MarkdownText:
    text: str


@dataclass(frozen=True, slots=True)
class Commands:
    title: str
    items: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class Panel:
    title: str
    blocks: tuple[Block, ...]


Block = (
    Heading
    | Line
    | KeyValues
    | Table
    | TreeNode
    | MascotBlock
    | Wordmark
    | Swatches
    | Hint
    | Verbatim
    | MarkdownText
    | Commands
    | Panel
)


@dataclass(frozen=True, slots=True)
class Document:
    blocks: tuple[Block, ...]
    payload: dict[str, JsonValue] = field(default_factory=dict)
    exit_code: int = 0
