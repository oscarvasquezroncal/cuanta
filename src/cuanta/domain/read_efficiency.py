from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from cuanta.domain.index_metrics import _attributes, _object
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.report import file_refs

READ_TOOLS = frozenset({"read", "view", "notebookread"})
RESULT_KINDS = frozenset({"tool_result", "mcp_tool", "mcp_tool_call", "index_call"})
FAILED_STATES = frozenset({"failed", "failure", "error", "denied", "rejected", "cancelled"})
PENDING_STATES = frozenset({"started", "start", "running", "pending"})
INVESTIGATION_FORMULA = "cited files read / files read"
CODE_FORMULA = "(edited or cited) files read / files read"


@dataclass(frozen=True, slots=True)
class ReadEfficiency:
    value: float | None = None
    read_files: tuple[str, ...] = ()
    cited_files: tuple[str, ...] = ()
    edited_files: tuple[str, ...] = ()
    useful_files: tuple[str, ...] = ()
    formula: str = CODE_FORMULA
    label: str = "file utilization v2"
    available: bool = False
    why: str = "no_reads"

    @property
    def read_count(self) -> int:
        return len(self.read_files)

    @property
    def useful_count(self) -> int:
        return len(self.useful_files)


@dataclass(frozen=True, slots=True)
class _Path:
    anchor: str
    parts: tuple[str, ...]
    windows: bool

    @property
    def text(self) -> str:
        return self.anchor + "/".join(self.parts)


def _parsed_path(value: str) -> _Path | None:
    text = value.strip().replace("\\", "/")
    if not text or "\x00" in text or "://" in text or text.startswith(("//?/", "//./")):
        return None
    windows = bool(PureWindowsPath(text).drive)
    pure = PureWindowsPath(text) if windows else PurePosixPath(text)
    if windows and not pure.is_absolute():
        return None
    anchor = pure.anchor.replace("\\", "/")
    parts: list[str] = []
    for part in pure.parts[bool(anchor) :]:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        elif ":" in part:
            return None
        else:
            parts.append(part.casefold() if windows else part)
    return _Path(anchor.casefold() if windows else anchor, tuple(parts), windows)


def _roots(project_root: str, project_roots: Sequence[str]) -> tuple[_Path, ...]:
    found: list[_Path] = []
    for value in (project_root, *project_roots):
        path = _parsed_path(value)
        if path is not None and path.anchor and path not in found:
            found.append(path)
    return tuple(sorted(found, key=lambda root: len(root.parts), reverse=True))


def _canonical(value: str, roots: Sequence[_Path]) -> str:
    path = _parsed_path(value)
    if path is None or not path.parts:
        return ""
    if not path.anchor:
        relative = "/".join(path.parts)
        return relative.casefold() if any(root.windows for root in roots) else relative
    if not roots:
        return path.text
    for root in roots:
        if path.anchor == root.anchor and path.parts[: len(root.parts)] == root.parts:
            return "/".join(path.parts[len(root.parts) :])
    return ""


def _read_tool(event: LedgerEvent, values: dict[str, object]) -> bool:
    tool = event.tool_name.casefold()
    if tool in READ_TOOLS or tool in {"cuanta.page", "mcp__cuanta__page"}:
        return True
    if event.source == "cuanta_mcp" and tool == "page":
        return True
    return (
        tool == "mcp_tool"
        and values.get("mcp_server_name") == "cuanta"
        and values.get("mcp_tool_name") == "page"
    )


def _usable(event: LedgerEvent, values: dict[str, object]) -> bool:
    status = str(values.get("status", "")).casefold()
    if (
        event.success is False
        or values.get("success") is False
        or values.get("is_error") is True
        or values.get("isError") is True
        or values.get("denied") is True
        or status in FAILED_STATES | PENDING_STATES
    ):
        return False
    return (
        event.success is True
        or values.get("success") is True
        or event.tool_result_bytes > 0
        or status in {"succeeded", "success"}
    )


def _event_path(event: LedgerEvent, values: dict[str, object]) -> str:
    if event.file_path:
        return event.file_path
    parameters = dict(values)
    for key in ("tool_input", "tool.parameters", "arguments"):
        parameters.update(_object(values.get(key)))
    for key in ("file_path", "path", "notebook_path"):
        value = parameters.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _identity(event: LedgerEvent) -> tuple[str, str, str, str]:
    return event.run_id, event.source, event.session_id, event.tool_use_id


def _read_files(events: Sequence[LedgerEvent], roots: Sequence[_Path]) -> tuple[str, ...]:
    completed = {_identity(event) for event in events if event.kind in RESULT_KINDS}
    found: set[str] = set()
    for event in events:
        if event.kind not in RESULT_KINDS | {"tool_use"}:
            continue
        if event.kind == "tool_use" and event.tool_use_id and _identity(event) in completed:
            continue
        values = _attributes(event)
        if not _read_tool(event, values) or not _usable(event, values):
            continue
        path = _canonical(_event_path(event, values), roots)
        if path:
            found.add(path)
    return tuple(sorted(found))


def _cited_files(text: str, roots: Sequence[_Path]) -> tuple[str, ...]:
    normalized = text.replace("\\", "/")
    for root in sorted(roots, key=lambda path: len(path.text), reverse=True):
        prefix = root.text.rstrip("/") + "/"
        pattern = rf"(?<![\w./:-]){re.escape(prefix)}"
        normalized = re.sub(pattern, "", normalized, flags=re.IGNORECASE if root.windows else 0)
    return tuple(
        sorted({path for ref in file_refs(normalized) if (path := _canonical(ref.path, roots))})
    )


def read_efficiency(
    events: Sequence[LedgerEvent],
    report_text: str | None,
    changed_files: Sequence[str] = (),
    task_type: str = "",
    project_root: str = "",
    project_roots: Sequence[str] = (),
) -> ReadEfficiency:
    roots = _roots(project_root, project_roots)
    reads = _read_files(events, roots)
    cited = _cited_files(report_text, roots) if report_text is not None else ()
    edited = tuple(sorted({path for item in changed_files if (path := _canonical(item, roots))}))
    investigation = task_type.casefold() == "investigation"
    candidates = set(cited) if investigation else set(cited) | set(edited)
    useful = tuple(path for path in reads if path in candidates)
    known = report_text is not None or (not investigation and bool(edited))
    available = bool(reads) and known
    return ReadEfficiency(
        value=len(useful) / len(reads) if available else None,
        read_files=reads,
        cited_files=cited,
        edited_files=edited,
        useful_files=useful,
        formula=INVESTIGATION_FORMULA if investigation else CODE_FORMULA,
        available=available,
        why="" if available else "no_reads" if not reads else "missing_report",
    )


def _relative_to(value: str, project_root: str) -> str:
    text = value.strip().replace("\\", "/")
    root = project_root.strip().replace("\\", "/").rstrip("/")
    if root and text.casefold().startswith(root.casefold() + "/"):
        return text[len(root) + 1 :]
    if PureWindowsPath(text).drive or text.startswith("/"):
        return ""
    return text.removeprefix("./")


def _line_number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _span(parameters: dict[str, object]) -> tuple[int, int]:
    lines = parameters.get("lines")
    if isinstance(lines, str):
        match = re.fullmatch(r"\s*(\d+)\s*(?:[-:]\s*(\d+))?\s*", lines)
        if match is not None:
            start = int(match[1])
            return start, int(match[2]) if match[2] else start
    offset = _line_number(parameters.get("offset"))
    limit = _line_number(parameters.get("limit"))
    start = max(1, offset or 1)
    if limit is not None and limit > 0:
        return start, start + limit - 1
    return start, 0


def read_ranges(
    events: Sequence[LedgerEvent], project_root: str
) -> tuple[tuple[str, int, int], ...]:
    completed = {_identity(event) for event in events if event.kind in RESULT_KINDS}
    found: dict[tuple[str, int, int], None] = {}
    for event in events:
        if event.kind not in RESULT_KINDS | {"tool_use"}:
            continue
        if event.kind == "tool_use" and event.tool_use_id and _identity(event) in completed:
            continue
        values = _attributes(event)
        if not _read_tool(event, values) or not _usable(event, values):
            continue
        path = _relative_to(_event_path(event, values), project_root)
        if not path:
            continue
        parameters = dict(values)
        for key in ("tool_input", "tool.parameters", "arguments"):
            parameters.update(_object(values.get(key)))
        start, end = _span(parameters)
        found[path, start, end] = None
    return tuple(found)
