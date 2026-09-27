from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

IndexTable = Literal["symbols", "edges", "notes", "rules", "test_links", "history"]
INDEX_TABLES: tuple[IndexTable, ...] = (
    "symbols",
    "edges",
    "notes",
    "rules",
    "test_links",
    "history",
)
INDEX_VERSION = 1


def index_path(path: str) -> str:
    value = path.replace("\\", "/")
    if not value or ":" in value or "\x00" in value:
        raise ValueError("Index paths must be relative to the project")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or ".." in parsed.parts or not parsed.parts:
        raise ValueError("Index paths must stay inside the project")
    return parsed.as_posix()


@dataclass(frozen=True, slots=True)
class IndexedFile:
    path: str
    content_hash: str
    language: str
    size_bytes: int
    provenance: str = "inventory"
    coverage: str = "inventory"


@dataclass(frozen=True, slots=True)
class IndexRow:
    id: str
    path: str
    source_hash: str
    provenance: str
    text: str = ""
    line: int = 0
    end_line: int = 0
    target: str = ""
    relation: str = ""
    confidence: float = 1.0
    stale: bool = False


@dataclass(frozen=True, slots=True)
class IndexStatus:
    files: int
    changed: int = 0
    removed: int = 0
    counts: tuple[tuple[str, int], ...] = ()
    coverage: float = 0.0
    updated_at: str = ""
    elapsed_s: float = 0.0
    recovered: bool = False
    content_hash: str = ""
    coverage_by_kind: tuple[tuple[str, int], ...] = ()
    history_status: str = ""


@dataclass(frozen=True, slots=True)
class IndexStructure:
    symbols: tuple[IndexRow, ...] = ()
    edges: tuple[IndexRow, ...] = ()
    coverage: str = "unsupported"


@dataclass(frozen=True, slots=True)
class IndexReport:
    provenance: str
    text: str
    sources: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class IndexHistory:
    path: str
    run_id: str
    task_type: str
    action: str
    at: str
    outcome: str = ""
    retries: int = 0
    signature: str = ""


@dataclass(frozen=True, slots=True)
class HandlingCard:
    path: str
    text: str
    estimated_tokens: int
    role: str
    purpose: str


@dataclass(frozen=True, slots=True)
class SearchHit:
    path: str
    score: float
    matched_terms: tuple[str, ...] = ()
    edges: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    prior: float = 0.0
    reasons: tuple[str, ...] = ()
