from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.mandate import LABELS, MandateRequest

SECTION_TITLES: dict[str, tuple[str, ...]] = {
    "done": ("WHAT WAS DONE",),
    "recommendations": ("RECOMMENDATIONS",),
    "docs": ("DOCS UPDATED",),
    "commit": ("COMMIT PROPOSAL",),
    "summary": ("SUMMARY",),
    "findings": ("FINDINGS",),
    "risks": ("RISKS",),
    "questions": ("OPEN QUESTIONS",),
    "next": ("SUGGESTED NEXT STEP", "NEXT STEP", "NEXT"),
}
PREAMBLE = "preamble"
MIN_SECTIONS = 2
CHARS_PER_TOKEN = 4
FIRST_REQUEST_KINDS = frozenset({"api_request"})
REPORTS_DIR = "docs/investigations"
RUN_REPORTS_DIR = "docs/runs"
SLUG_LIMIT = 48

_TITLE_PATTERN = "|".join(
    re.escape(title)
    for title in sorted(
        (title for titles in SECTION_TITLES.values() for title in titles), key=len, reverse=True
    )
)
_HEADING = re.compile(
    rf"^\s*(?:#{{1,6}}\s*)?(?:\d+[.)]\s*)?(?:\*\*|__)?\s*({_TITLE_PATTERN})\b\s*(?:\*\*|__)?"
    r"\s*(?:[—:–-]\s*)?(.*)$",
    re.IGNORECASE,
)
_REQUEST_LABEL = re.compile(
    r"^(?:TYPE|WHAT|WHY / EVIDENCE|WHERE|CONSTRAINTS|EXPECTED TESTS|OUT OF SCOPE):"
)
_HEADING_SHAPE = re.compile(r"^\s*(?:#{1,6}\s|\d+[.)]\s|\*\*|__)")
_ATX_HEADING = re.compile(r"^ {0,3}#{1,6}\s+\S")
_FILE_REF = re.compile(
    r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.[A-Za-z][\w]{0,7}):(\d{1,6})(?::\d{1,4})?(?!\d)"
)


@dataclass(frozen=True, slots=True)
class ReportSection:
    key: str
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class FileRef:
    path: str
    line: int

    @property
    def label(self) -> str:
        return f"{self.path}:{self.line}"


@dataclass(frozen=True, slots=True)
class ContextSplit:
    first_request: int
    request: int

    @property
    def fixed(self) -> int:
        return max(0, self.first_request - self.request)

    @property
    def fixed_share(self) -> float:
        return self.fixed / self.first_request if self.first_request else 0.0


def _key_of(title: str) -> str:
    upper = title.upper()
    for key, titles in SECTION_TITLES.items():
        if upper in titles:
            return key
    return PREAMBLE


def _section_heading(line: str) -> re.Match[str] | None:
    return _HEADING.match(line) if _HEADING_SHAPE.match(line) else None


def parse_sections(text: str) -> tuple[ReportSection, ...]:
    sections: list[tuple[str, str, list[str]]] = [(PREAMBLE, "", [])]
    for line in text.splitlines():
        match = _section_heading(line)
        if match is not None:
            title = match.group(1).upper()
            rest = match.group(2).strip().strip("*").strip()
            sections.append((_key_of(title), title, [rest] if rest else []))
            continue
        sections[-1][2].append(line)
    found = [ReportSection(key, title, "\n".join(lines).strip()) for key, title, lines in sections]
    named = [section for section in found if section.key != PREAMBLE]
    if len(named) < MIN_SECTIONS:
        return ()
    return tuple(section for section in found if section.key != PREAMBLE or section.body)


def strip_preamble(text: str) -> str:
    lines = text.splitlines(keepends=True)
    fenced = False
    for index, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        if _ATX_HEADING.match(line) or _section_heading(line) is not None:
            return "".join(lines[index:]) if index else text
    return text


def section(sections: Sequence[ReportSection], key: str) -> ReportSection | None:
    for item in sections:
        if item.key == key:
            return item
    return None


def next_request(text: str) -> MandateRequest | None:
    sections = parse_sections(text)
    found = section(sections, "next")
    if found is None or not found.body.strip():
        return None
    by_label = {label.rstrip(":").upper(): name for name, label in LABELS.items()}
    values: dict[str, list[str]] = {}
    current = ""
    for raw in found.body.splitlines():
        line = raw.strip().strip("`")
        label, colon, rest = line.partition(":")
        name = by_label.get(label.strip().upper()) if colon else None
        if name is not None:
            current = name
            values[current] = [rest.strip()]
        elif current and line and not line.startswith("==="):
            values[current].append(line)
    if "what" not in values:
        first = next((line.strip() for line in found.body.splitlines() if line.strip()), "")
        return MandateRequest(what=first.lstrip("-* ").strip()) if first else None
    fields = {name: "\n".join(lines).strip() for name, lines in values.items()}
    kind = fields.get("type", "").split("|")[0].strip().lower()
    return MandateRequest(
        type=kind,
        what=fields.get("what", ""),
        why=fields.get("why", ""),
        where=fields.get("where", ""),
        constraints=fields.get("constraints", ""),
        tests=fields.get("tests", ""),
        out_of_scope=fields.get("out_of_scope", ""),
    )


def file_refs(text: str) -> tuple[FileRef, ...]:
    seen: dict[str, FileRef] = {}
    for match in _FILE_REF.finditer(text):
        ref = FileRef(match.group(1), int(match.group(2)))
        seen.setdefault(ref.label, ref)
    return tuple(seen.values())


def _link_line(line: str, scheme: str) -> str:
    def replace(match: re.Match[str]) -> str:
        label = match.group(0)
        return f"[{label}]({scheme}{match.group(1)}:{match.group(2)})"

    parts = line.split("`")
    for index, part in enumerate(parts):
        if index % 2 == 0:
            parts[index] = _FILE_REF.sub(replace, part)
        elif _FILE_REF.fullmatch(part.strip()):
            parts[index] = _FILE_REF.sub(replace, part.strip())
        else:
            parts[index] = f"`{part}`"
    return "".join(parts) if len(parts) % 2 == 1 else line


def _request_line(line: str) -> bool:
    return bool(_REQUEST_LABEL.match(line.strip()))


def link_file_refs(text: str, scheme: str) -> str:
    lines: list[str] = []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            lines.append(line)
            continue
        if fenced:
            lines.append(line)
            continue
        linked = _link_line(line, scheme)
        lines.append(f"{linked}  " if _request_line(line) else linked)
    return "\n".join(lines)


def slug(text: str, limit: int = SLUG_LIMIT) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    words = re.findall(r"[a-z0-9]+", ascii_text.lower())
    joined = "-".join(words)[:limit].strip("-")
    return joined or "report"


def docs_path(task_type: str, title: str, day: date) -> str:
    folder = REPORTS_DIR if task_type == "investigation" else RUN_REPORTS_DIR
    return f"{folder}/{day.isoformat()}-{slug(title)}.md"


def first_request_event(events: Sequence[LedgerEvent]) -> LedgerEvent | None:
    requests = (event for event in events if event.kind in FIRST_REQUEST_KINDS)
    return min(requests, key=lambda event: (event.ts, event.id), default=None)


def context_split(events: Sequence[LedgerEvent], prompt_chars: int) -> ContextSplit | None:
    first = first_request_event(events)
    if first is None:
        return None
    total = first.input_tokens + first.cache_read_tokens + first.cache_write_tokens
    if total <= 0:
        return None
    return ContextSplit(total, min(total, max(0, prompt_chars) // CHARS_PER_TOKEN))


def unified_diff(before: str, after: str, path: str) -> str:
    import difflib

    lines = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
    )
    return "\n".join(lines)
