from __future__ import annotations

import re
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from enum import StrEnum

from cuanta.domain.detection import EXCLUDED_DIRS
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import Message, msg

ANCHOR_FIELDS = ("what", "why", "where", "constraints", "tests")
ANCHOR_LIMIT = 64
ANCHOR_CONTEXT = 3
NOTE_LABELS = 8
DEPENDENCY_DIRS = frozenset({*EXCLUDED_DIRS, "site-packages", "dist-packages"})
ANCHOR_AT = re.compile(
    r"(?<![\w@+.:\\/-])"
    r"(?P<path>(?:[A-Za-z]:[\\/]|/)?(?:[\w@+.-]+[\\/])*[\w@+-][\w@+.-]*\.[A-Za-z]\w{0,7})"
    r"(?::L?|#L)(?P<start>\d{1,6})"
    r"(?:[ \t]*[-–—][ \t]*L?(?P<end>\d{1,6}))?(?!\d)"
)
KNOWN_EXTENSIONS = frozenset(
    {
        "c",
        "cc",
        "cfg",
        "cjs",
        "cpp",
        "cs",
        "css",
        "go",
        "h",
        "hpp",
        "html",
        "ini",
        "java",
        "js",
        "json",
        "jsx",
        "kt",
        "kts",
        "less",
        "md",
        "mdx",
        "mjs",
        "php",
        "ps1",
        "py",
        "pyi",
        "rb",
        "rs",
        "rst",
        "sass",
        "scala",
        "scss",
        "sh",
        "sql",
        "svelte",
        "swift",
        "toml",
        "ts",
        "tsx",
        "txt",
        "vue",
        "xml",
        "yaml",
        "yml",
    }
)


@dataclass(frozen=True, slots=True)
class RequestAnchor:
    path: str
    start: int
    end: int
    field: str = ""

    @property
    def label(self) -> str:
        if self.end == self.start:
            return f"{self.path}:{self.start}"
        return f"{self.path}:{self.start}-{self.end}"


class AnchorState(StrEnum):
    PACKED = "packed"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    OUT_OF_RANGE = "out_of_range"
    STALE = "stale"
    PROTECTED = "protected"
    OMITTED = "omitted"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class AnchorCheck:
    anchor: RequestAnchor
    path: str
    state: AnchorState
    first: int = 0
    last: int = 0

    @property
    def label(self) -> str:
        if not self.path:
            return self.anchor.label
        return RequestAnchor(self.path, self.anchor.start, self.anchor.end).label

    @property
    def shown(self) -> str:
        if self.state is AnchorState.PARTIAL:
            return f"{self.label} → {self.first}-{self.last}"
        return self.label


@dataclass(frozen=True, slots=True)
class AnchorWindow:
    anchor: RequestAnchor
    first: int
    last: int
    members: tuple[RequestAnchor, ...]


NOTE_ORDER = (
    AnchorState.MISSING,
    AnchorState.AMBIGUOUS,
    AnchorState.OUT_OF_RANGE,
    AnchorState.STALE,
    AnchorState.PARTIAL,
    AnchorState.OMITTED,
    AnchorState.PROTECTED,
)


def extract_anchors(text: str, field: str = "") -> tuple[RequestAnchor, ...]:
    found: dict[tuple[str, int, int], RequestAnchor] = {}
    for matched in ANCHOR_AT.finditer(text):
        path = matched["path"].replace("\\", "/").removeprefix("./")
        start = int(matched["start"])
        end = max(start, int(matched["end"])) if matched["end"] else start
        if start < 1:
            continue
        found.setdefault((path, start, end), RequestAnchor(path, start, end, field))
        if len(found) >= ANCHOR_LIMIT:
            break
    return tuple(found.values())


def _fields(request: MandateRequest) -> tuple[tuple[str, str], ...]:
    values = {
        "what": request.what,
        "why": request.why,
        "where": request.where,
        "constraints": request.constraints,
        "tests": request.tests,
    }
    return tuple((name, values[name]) for name in ANCHOR_FIELDS)


def request_anchors(request: MandateRequest) -> tuple[RequestAnchor, ...]:
    found: dict[tuple[str, int, int], RequestAnchor] = {}
    for name, text in _fields(request):
        for anchor in extract_anchors(text, name):
            found.setdefault((anchor.path, anchor.start, anchor.end), anchor)
            if len(found) >= ANCHOR_LIMIT:
                return tuple(found.values())
    return tuple(found.values())


def _same(text: str) -> str:
    return text


def _exact(target: str, paths: Collection[str], fold: Callable[[str], str]) -> tuple[str, ...]:
    return tuple(sorted(path for path in paths if fold(path) == target))


def _deeper(target: str, paths: Collection[str], fold: Callable[[str], str]) -> tuple[str, ...]:
    return tuple(sorted(path for path in paths if fold(path).endswith("/" + target)))


def _within(target: str, paths: Collection[str], fold: Callable[[str], str]) -> tuple[str, ...]:
    found = [path for path in paths if target.endswith("/" + fold(path))]
    longest = max((len(path) for path in found), default=0)
    return tuple(sorted(path for path in found if len(path) == longest))


MATCH_RULES = (_exact, _deeper, _within)
FOLDS: tuple[Callable[[str], str], ...] = (_same, str.casefold)


def anchor_candidates(path: str, paths: Collection[str]) -> tuple[str, ...]:
    for rule in MATCH_RULES:
        for fold in FOLDS:
            found = rule(fold(path), paths, fold)
            if found:
                return found
    return ()


def anchor_windows(
    path: str,
    anchors: Sequence[RequestAnchor],
    line_count: int,
    window: int,
    context: int = ANCHOR_CONTEXT,
) -> tuple[tuple[AnchorWindow, ...], tuple[RequestAnchor, ...]]:
    size = max(1, window)
    beyond = tuple(anchor for anchor in anchors if anchor.start > line_count)
    spans: dict[tuple[int, int], list[RequestAnchor]] = {}
    for anchor in anchors:
        if anchor.start <= line_count:
            spans.setdefault((anchor.start, anchor.end), []).append(anchor)
    merged: list[AnchorWindow] = []
    for (start, end), members in sorted(spans.items()):
        first = max(1, start - context)
        last = min(line_count, end + context)
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and first <= previous.last + 1
            and max(last, previous.last) - previous.first < size
        ):
            merged[-1] = AnchorWindow(
                RequestAnchor(path, previous.anchor.start, max(previous.anchor.end, end)),
                previous.first,
                max(previous.last, last),
                (*previous.members, *members),
            )
            continue
        merged.append(
            AnchorWindow(
                RequestAnchor(path, start, end), first, min(last, first + size - 1), tuple(members)
            )
        )
    return tuple(merged), beyond


def packed_state(
    anchor: RequestAnchor, window: AnchorWindow, line_count: int
) -> tuple[AnchorState, int, int]:
    first = max(anchor.start, window.first)
    last = min(anchor.end, line_count, window.last)
    whole = (first, last) == (anchor.start, min(anchor.end, line_count))
    return (AnchorState.PACKED if whole else AnchorState.PARTIAL), first, last


def reportable(path: str) -> bool:
    folders = path.split("/")[:-1]
    if DEPENDENCY_DIRS.intersection(folders):
        return False
    extension = path.rsplit(".", 1)[-1].casefold()
    return bool(folders) or extension in KNOWN_EXTENSIONS


def anchor_notes(checks: Sequence[AnchorCheck], budget: int) -> tuple[Message, ...]:
    notes: list[Message] = []
    for state in NOTE_ORDER:
        labels = tuple(
            dict.fromkeys(
                check.shown
                for check in checks
                if check.state is state
                and (state is not AnchorState.MISSING or reportable(check.anchor.path))
            )
        )
        if not labels:
            continue
        shown = ", ".join(labels[:NOTE_LABELS])
        if len(labels) > NOTE_LABELS:
            shown += f" +{len(labels) - NOTE_LABELS}"
        key = f"pack.anchors_{state.value}"
        if state is AnchorState.OMITTED:
            notes.append(msg(key, anchors=shown, budget=f"{budget:,}"))
        else:
            notes.append(msg(key, anchors=shown))
    return tuple(notes)
