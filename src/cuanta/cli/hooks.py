from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import cast

from cuanta.application.session_profile import record_hook_event
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.read_discipline import (
    POST_CONTEXT,
    ReadDisciplineDecision,
    decide_read_discipline,
)
from cuanta.domain.stable import stable_json


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("Hook input must be a JSON object")
    return cast(dict[str, object], value)


def _contained(root: Path, value: str) -> Path:
    supplied = Path(value)
    if ".." in supplied.parts:
        raise ValueError("Hook paths must not contain parent traversal")
    candidate = supplied if supplied.is_absolute() else root / supplied
    normalized = Path(os.path.abspath(candidate))
    if not normalized.is_relative_to(root):
        raise ValueError("Hook paths must stay in the current project copy")
    current = root
    for part in normalized.relative_to(root).parts:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("Hook paths must not traverse links")
    if not normalized.resolve().is_relative_to(root):
        raise ValueError("Hook paths must stay in the current project copy")
    return normalized


def _string(data: Mapping[str, object], name: str, default: str = "") -> str:
    value = data.get(name, default)
    if not isinstance(value, str):
        raise ValueError("Hook text fields must be strings")
    return value


def hook_decision(payload: Mapping[str, object], root: Path) -> tuple[ReadDisciplineDecision, str]:
    root = root.resolve()
    cwd = _contained(root, _string(payload, "cwd", str(root)))
    if not cwd.is_dir():
        raise ValueError("Hook working directory is unavailable")
    tool = _string(payload, "tool_name")
    inputs = _object(payload.get("tool_input"))
    relative = ""
    file_lines = 0
    file_characters = 0
    for key in ("file_path", "notebook_path", "path"):
        if key not in inputs:
            continue
        path = _contained(root, str(cwd / _string(inputs, key)))
        if key in {"file_path", "notebook_path"}:
            relative = path.relative_to(root).as_posix()
        if key == "path" and path == root:
            inputs = {**inputs, "path": "."}
        if tool == "Read" and key == "file_path":
            text = path.read_text(encoding="utf-8", errors="replace")
            file_lines = len(text.splitlines())
            file_characters = len(text)
    if tool == "Read" and not relative:
        raise ValueError("Read requires a contained file path")
    return decide_read_discipline(tool, inputs, file_lines, file_characters), relative


def _log(
    payload: Mapping[str, object], decision: ReadDisciplineDecision, root: Path, relative: str
) -> None:
    run_id = os.environ.get("CUANTA_RUN_ID", "")
    if not run_id or not decision.permission:
        return
    from cuanta.bootstrap import Container

    container = Container.for_project(root)
    try:
        event = LedgerEvent(
            run_id=run_id,
            source="cuanta_hook",
            session_id=_string(payload, "session_id"),
            agent=_string(payload, "agent_type", "orchestrator"),
            kind="read_discipline",
            tool_name=_string(payload, "tool_name"),
            tool_use_id=_string(payload, "tool_use_id"),
            file_path=relative,
            query_source=decision.permission,
            ts=container.clock.now_iso(),
            raw=stable_json({"avoided_tokens_estimate": decision.avoided_tokens}),
        )
        record_hook_event(container.ledger(), event)
    finally:
        container.close()


def hook_output(payload: object, root: Path, mode: str) -> dict[str, object]:
    if mode not in {"pre", "post"}:
        raise ValueError("Hook mode must be pre or post")
    event = "PreToolUse" if mode == "pre" else "PostToolUse"
    try:
        data = _object(payload)
        if _string(data, "hook_event_name", event) != event:
            raise ValueError("Hook event does not match the selected mode")
        decision, relative = hook_decision(data, root)
    except (OSError, ValueError):
        if mode == "post":
            return {}
        decision = ReadDisciplineDecision("deny", "Invalid or uncontained hook tool input.")
        data, relative = {}, ""
    if mode == "post":
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": POST_CONTEXT}}
    if not decision.permission:
        return {}
    with suppress(OSError, ValueError, sqlite3.Error):
        _log(data, decision, root, relative)
    specific: dict[str, object] = {
        "hookEventName": event,
        "permissionDecision": decision.permission,
        "permissionDecisionReason": decision.reason,
    }
    if decision.updated_input is not None:
        specific["updatedInput"] = decision.updated_input
    return {"hookSpecificOutput": specific}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"pre", "post"}:
        return 2
    try:
        payload: object = json.load(sys.stdin)
    except (ValueError, OSError):
        payload = None
    result = hook_output(payload, Path.cwd(), sys.argv[1])
    if result:
        sys.stdout.write(stable_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
