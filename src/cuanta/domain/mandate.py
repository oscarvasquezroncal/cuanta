from __future__ import annotations

import hashlib
import re
from bisect import bisect_right
from dataclasses import dataclass, fields
from enum import StrEnum

REQUEST_MARKER = "=== REQUEST ==="
FENCE = "```"


class MandateType(StrEnum):
    FEATURE = "feature"
    BUG = "bug"
    REFACTOR = "refactor"
    INVESTIGATION = "investigation"


@dataclass(frozen=True, slots=True)
class MandateRequest:
    type: str = ""
    what: str = ""
    why: str = ""
    where: str = ""
    constraints: str = ""
    tests: str = ""
    out_of_scope: str = ""


class PartKind(StrEnum):
    PHASE = "phase"
    STEP = "step"


@dataclass(frozen=True, slots=True)
class RequestParts:
    kind: PartKind = PartKind.PHASE
    count: int = 0


MIN_PARTS = 2
NUMBERED_PART = re.compile(
    r"^[ \t>]*+(?:#{1,6}[ \t]*+)?(?:(?:[-*+]|\d{1,2}[.)])[ \t]++)?(?:\*\*|__|\*|_)?[ \t]*"
    r"(?P<word>fase|etapa|hito|phase|stage|milestone|paso|step)s?[ \t]+"
    r"(?P<number>\d{1,2}|[ivx]{1,4})\b",
    re.IGNORECASE | re.MULTILINE,
)
STEP_WORDS = frozenset({"paso", "step"})
LINE_LEAD = r"(?:[ ]{0,3}>[ ]?)*+[ ]{0,3}+"
HEADING_LINE = re.compile(
    LINE_LEAD + r"(?:(?P<atx>#{1,6})(?:[ \t\r]|$)"
    r"|(?:[-*+][ \t]++)?(?:\*\*|__)[^*_\n]++(?:\*\*|__)[ \t\r]*+:?[ \t\r]*+$)"
)
FENCE_LINE = re.compile(LINE_LEAD + r"(?P<fence>`{3,}+(?=[^`]*+$)|~{3,}+)(?P<info>.*+)")
LABEL_LEVEL = 7

Span = tuple[int, int]


def fence_after(fence: str, line: str) -> str:
    found = FENCE_LINE.match(line)
    if found is None:
        return fence
    mark = found["fence"]
    if not fence:
        return mark
    if mark[0] == fence[0] and len(mark) >= len(fence) and not found["info"].strip():
        return ""
    return fence


def request_text(request: MandateRequest) -> str:
    values = (getattr(request, item.name).strip() for item in fields(MandateRequest))
    return "\n".join(value for value in values if value)


def numbered_parts(text: str) -> RequestParts:
    found: dict[PartKind, set[str]] = {PartKind.PHASE: set(), PartKind.STEP: set()}
    for match in NUMBERED_PART.finditer(text):
        kind = PartKind.STEP if match["word"].lower() in STEP_WORDS else PartKind.PHASE
        found[kind].add(match["number"].lower())
    phases, steps = len(found[PartKind.PHASE]), len(found[PartKind.STEP])
    if phases >= MIN_PARTS or phases >= steps:
        return RequestParts(PartKind.PHASE, phases)
    return RequestParts(PartKind.STEP, steps)


def first_part_offset(text: str) -> int | None:
    found = NUMBERED_PART.search(text)
    return found.start() if found is not None else None


def _line_level(line: str, part: bool) -> int | None:
    found = HEADING_LINE.match(line)
    if found is None:
        return LABEL_LEVEL if part else None
    return len(found["atx"]) if found["atx"] else LABEL_LEVEL


def _merged(spans: list[Span]) -> tuple[Span, ...]:
    merged: list[Span] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def part_spans(text: str) -> tuple[Span, ...]:
    spans: list[Span] = []
    open_parts: list[tuple[int, int]] = []
    fence = ""
    offset = 0
    for line in text.split("\n"):
        start, offset = offset, offset + len(line) + 1
        after = fence_after(fence, line)
        if fence or after:
            fence = after
            continue
        part = NUMBERED_PART.match(line) is not None
        level = _line_level(line, part)
        if level is None:
            continue
        while open_parts and open_parts[-1][0] >= level:
            spans.append((open_parts.pop()[1], start))
        if part:
            open_parts.append((level, start))
    spans.extend((begin, len(text)) for _, begin in open_parts)
    return _merged(spans)


def in_parts(position: int, spans: tuple[Span, ...]) -> bool:
    index = bisect_right(spans, position, key=lambda span: span[0]) - 1
    return index >= 0 and position < spans[index][1]


LABELS: dict[str, str] = {
    "type": "TYPE:",
    "what": "WHAT:",
    "why": "WHY / EVIDENCE:",
    "where": "WHERE:",
    "constraints": "CONSTRAINTS:",
    "tests": "EXPECTED TESTS:",
    "out_of_scope": "OUT OF SCOPE:",
}
REQUIRED = ("type", "what", "why", "out_of_scope")
ESSENTIAL_FIELDS = ("type", "what")
NONE_STATED = "none stated"
PASTED_ERROR_TESTS = "regression fixture for the pasted error"
DEFAULTS = {
    "why": NONE_STATED,
    "where": "unknown",
    "constraints": NONE_STATED,
    "tests": NONE_STATED,
    "out_of_scope": NONE_STATED,
}
TYPE_DEFAULTS: dict[str, dict[str, str]] = {
    "investigation": {"tests": "Deliverable: a written report"},
}


class TemplateError(ValueError):
    pass


def missing_fields(
    request: MandateRequest, required: tuple[str, ...] | None = None
) -> tuple[str, ...]:
    names = REQUIRED_BY_TYPE.get(request.type, REQUIRED) if required is None else required
    return tuple(name for name in names if not getattr(request, name).strip())


def _defaults(request: MandateRequest) -> dict[str, str]:
    chosen = {**DEFAULTS, **TYPE_DEFAULTS.get(request.type, {})}
    if request.type == MandateType.BUG and request.why.strip():
        chosen["tests"] = PASTED_ERROR_TESTS
    return chosen


def with_defaults(request: MandateRequest) -> MandateRequest:
    values = {item.name: getattr(request, item.name) for item in fields(MandateRequest)}
    for name, value in _defaults(request).items():
        if not values[name].strip():
            values[name] = value
    return MandateRequest(**values)


def extract_block(template: str) -> str:
    marker = template.find(REQUEST_MARKER)
    if marker == -1:
        raise TemplateError("MANDATE_TEMPLATE.md has no === REQUEST === block")
    opening = template.rfind(FENCE, 0, marker)
    closing = template.find(FENCE, marker)
    if opening == -1 or closing == -1:
        raise TemplateError("the REQUEST block is not inside a fenced mandate block")
    start = template.find("\n", opening) + 1
    return template[start:closing]


def _value_column(block_tail: str) -> int:
    for line in block_tail.splitlines():
        match = re.match(r"^([A-Z][A-Z /]+:)(\s+)\S", line)
        if match:
            return len(match.group(1)) + len(match.group(2))
    return 17


def _format_value(label: str, value: str, column: int) -> list[str]:
    lines = value.strip().splitlines() or [""]
    head = f"{label.ljust(column - 1)} {lines[0]}".rstrip()
    rest = [f"{' ' * column}{line}".rstrip() for line in lines[1:]]
    return [head, *rest]


def fill_request(
    block: str,
    request: MandateRequest,
    hint: str = "",
    context: str = "",
    *,
    report_language: str = "",
) -> str:
    marker = block.find(REQUEST_MARKER)
    if marker == -1:
        raise TemplateError("block has no === REQUEST === marker")
    above = block[:marker]
    if report_language == "es":
        instruction = (
            "Write the report in Spanish. Do not translate paths, code names or quotations. "
            "Keep the report headings (SUMMARY, FINDINGS, RISKS, OPEN QUESTIONS, NEXT STEP), "
            "REQUEST labels and TYPE values in English, exactly as written."
        )
        above = (
            above.replace(REPORT_LANGUAGE, instruction)
            if REPORT_LANGUAGE in above
            else (f"{above}{instruction}\n\n")
        )
    column = _value_column(block[marker:])
    filled = with_defaults(request)
    body: list[str] = [REQUEST_MARKER, ""]
    for name, label in LABELS.items():
        body.extend(_format_value(label, getattr(filled, name), column))
    hint_line = f"{hint}\n\n" if hint else ""
    context_line = f"{context}\n\n" if context else ""
    return f"{above}{context_line}{hint_line}" + "\n".join(body) + "\n"


def evidence_from_failure(
    signatures: list[tuple[str, str, int, str, str]],
    command: str,
    capsule: str,
) -> str:
    lines = [f"cuanta test ({command}) is red:"]
    for identifier, error, tests, first, location in signatures:
        where = f" at {location}" if location else ""
        lines.append(f"[{identifier}] x{tests} {error}{where} (first: {first})")
    lines.append(f"full log: cuanta cat {capsule} --level L2")
    return "\n".join(lines)


INLINE_EVIDENCE_LIMIT = 8_000
EVIDENCE_HEAD = 2_000


def clip_evidence(text: str, capsule: str, limit: int = INLINE_EVIDENCE_LIMIT) -> str:
    if len(text) <= limit:
        return text
    footer = (
        f"\n[... {len(text):,} characters in total; the full text is capsule {capsule}. "
        f"Read it with: cuanta cat {capsule} --level L2]\n"
    )
    tail = max(0, limit - EVIDENCE_HEAD - len(footer) - 8)
    return f"{text[:EVIDENCE_HEAD]}\n[...]\n{text[len(text) - tail :]}{footer}"


SIMPLE_BLOCK = """# MANDATE — simple mode

This repository has no project-specific agents yet, so you work alone, directly, in one pass.

- Finish the whole request in ONE pass. Never pause to ask for approval or confirmation.
- ABSOLUTELY NO GIT OPERATIONS. Propose a commit message at most; never run git.
- Read only what the request needs, keep every change inside its scope, and verify what you
  changed with the project's own checks when they exist.

End with a FINAL REPORT in this fixed order:

1. **WHAT WAS DONE** — the files you changed and what changed in each, or what you found.
2. **RECOMMENDATIONS** — only real findings, one line each with `file:line`; otherwise "None."
3. **NEXT** — the single most sensible next request as a filled REQUEST block. Propose only.

=== REQUEST ===
"""


def decision_key(request: MandateRequest) -> str:
    parts = (request.type.strip(), request.what.strip(), request.where.strip())
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


REQUIRED_BY_TYPE: dict[str, tuple[str, ...]] = {
    "bug": ("type", "what", "why", "out_of_scope"),
    "feature": ("type", "what", "tests", "out_of_scope"),
    "refactor": ("type", "what", "constraints", "out_of_scope"),
    "investigation": ("type", "what", "why", "out_of_scope"),
}
INVESTIGATION = "investigation"
INVESTIGATION_TOOLS = ("Read", "Grep", "Glob", "Bash(graphify *)")
DELEGATION_TOOLS = ("Agent", "Task")
READ_ONLY_DENIED = ("Write", "Edit", "MultiEdit", "NotebookEdit", "Bash(git *)")
DELIVERABLES = ("summary", "report", "diagram", "risks")
DELIVERABLE_TEXT = {
    "summary": "a short summary",
    "report": "a written report",
    "diagram": "a diagram of the flow (Mermaid) with a short explanation",
    "risks": "a list of risks, most serious first",
}


def deliverable_line(key: str) -> str:
    return f"Deliverable: {DELIVERABLE_TEXT.get(key, DELIVERABLE_TEXT['report'])}"


def deliverable_of(tests: str) -> str:
    for key, text in DELIVERABLE_TEXT.items():
        if text in tests:
            return key
    return "report"


def second_field(kind: str) -> str:
    return {"feature": "tests", "refactor": "constraints"}.get(kind, "why")


REPORT_LANGUAGE = (
    "Write the report in the same language as the request below: the language its WHAT and "
    "WHY / EVIDENCE are written in (quoted logs, code and paths do not count). Keep the "
    "headings above, the REQUEST labels and the TYPE value in English, exactly as written."
)

REPORT_FORMAT = (
    """End with this report, in this order, using these exact headings:

## SUMMARY
Two to five sentences that answer the request.

## FINDINGS
One line per finding. Every finding cites its evidence as `path/to/file:line`.

## RISKS
What could break or mislead, one line each; "None." when there are none.

## OPEN QUESTIONS
What the code could not answer; "None." when there are none.

## NEXT STEP
The single most sensible next request, as a filled REQUEST block. Propose only; never start it.

"""
    + REPORT_LANGUAGE
    + "\n"
)


class Shape(StrEnum):
    SINGLE = "single"
    PIPELINE = "pipeline"
    SCOUT = "scout"


def parse_shape(text: str) -> Shape:
    try:
        return Shape(text.strip().lower())
    except ValueError:
        return Shape.SINGLE


INVESTIGATION_BLOCK = (
    """# MANDATE — investigation (read-only)

This is an investigation. It changes nothing.

- READ-ONLY: never create, edit or delete a file, and run no command except `graphify` queries.
  Read, Grep and Glob are your tools.
- Invoke the architecture-analyst agent ONCE for the analysis. Do not invoke the senior, the
  tester or the docs-updater: there is nothing to implement, test or document.
- Answer every question listed under WHY / EVIDENCE. Stay inside WHERE when it is given.
- ABSOLUTELY NO GIT OPERATIONS.

"""
    + REPORT_FORMAT
    + """
=== REQUEST ===
"""
)

SINGLE_INVESTIGATION_BLOCK = (
    """# MANDATE — investigation (read-only, one context)

This is an investigation. It changes nothing. You are the analyst: answer directly, in this
session, with no subagents.

- READ-ONLY: never create, edit or delete a file, and run no command except `graphify` queries.
  Read, Grep and Glob are your tools.
- Answer every question listed under WHY / EVIDENCE. Stay inside WHERE when it is given.
- Every finding cites the code it rests on as `path/to/file:line`.
- ABSOLUTELY NO GIT OPERATIONS.

"""
    + REPORT_FORMAT
    + """
=== REQUEST ===
"""
)

ANALYST_FALLBACK = """You are the codebase analyst for this repository. You never write code.
Map what the request touches before you conclude: prefer graph queries and search over reading
whole files, read only the files the answer needs, and cite `path/to/file:line` for every claim."""
REPORT_OVERRIDE = (
    "For this session the report format given in the user message replaces any output "
    "contract above: answer in that format, not in JSON."
)


def analyst_system_prompt(body: str, budget_line: str, graph_available: bool = True) -> str:
    fallback = (
        ANALYST_FALLBACK
        if graph_available
        else ANALYST_FALLBACK.replace("graph queries and search", "search")
    )
    parts = [body.strip() if body.strip() and graph_available else fallback, REPORT_OVERRIDE]
    if budget_line:
        parts.append(budget_line)
    return "\n\n".join(parts)


SIMPLE_INVESTIGATION_BLOCK = (
    """# MANDATE — investigation (read-only, simple mode)

This repository has no project-specific agents yet, so you work alone, in one pass. This is an
investigation: it changes nothing.

- READ-ONLY: never create, edit or delete a file, and run no command except `graphify` queries.
  Read, Grep and Glob are your tools.
- Answer every question listed under WHY / EVIDENCE. Stay inside WHERE when it is given.
- ABSOLUTELY NO GIT OPERATIONS.

"""
    + REPORT_FORMAT
    + """
=== REQUEST ===
"""
)


def required_fields(kind: str) -> tuple[str, ...]:
    return REQUIRED_BY_TYPE.get(kind, REQUIRED)


def builtin_block(
    kind: str, simple: bool, shape: Shape = Shape.PIPELINE, graph_available: bool = True
) -> str | None:
    if kind == INVESTIGATION:
        if simple:
            block = SIMPLE_INVESTIGATION_BLOCK
        else:
            block = SINGLE_INVESTIGATION_BLOCK if shape is Shape.SINGLE else INVESTIGATION_BLOCK
        if not graph_available:
            block = block.replace(
                "and run no command except `graphify` queries.", "and run no shell commands."
            )
        return block
    return SIMPLE_BLOCK if simple else None


def single_context(kind: str, simple: bool, shape: Shape) -> bool:
    return kind == INVESTIGATION and (simple or shape is Shape.SINGLE)


def investigation_tools(
    simple: bool, shape: Shape = Shape.PIPELINE, graph_available: bool = True
) -> tuple[str, ...]:
    base = INVESTIGATION_TOOLS if graph_available else INVESTIGATION_TOOLS[:-1]
    if single_context(INVESTIGATION, simple, shape):
        return base
    return (*base, *DELEGATION_TOOLS)


def investigation_denied(simple: bool, shape: Shape) -> tuple[str, ...]:
    if single_context(INVESTIGATION, simple, shape):
        return (*READ_ONLY_DENIED, *DELEGATION_TOOLS)
    return READ_ONLY_DENIED


def investigation_builtin_tools(
    simple: bool, shape: Shape, graph_available: bool = True
) -> tuple[str, ...] | None:
    if not single_context(INVESTIGATION, simple, shape):
        return None
    allowed = investigation_tools(simple, shape, graph_available)
    return tuple(dict.fromkeys(item.split("(", 1)[0] for item in allowed))
