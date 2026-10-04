from __future__ import annotations

from cuanta.domain.new_files import (
    SUGGESTED_DIR,
    Suggestion,
    line_delta,
    original_of,
    same_text,
    sibling_original,
    suggested_path,
)
from cuanta.ports.workspace import Workspace

FORGE_FOLDERS = (".claude/agents", ".claude/commands", ".claude/skills/agent-system-init")


def siblings(workspace: Workspace) -> tuple[str, ...]:
    found: list[str] = []
    for folder in FORGE_FOLDERS:
        for path in workspace.files_under(folder):
            original = sibling_original(path)
            if original is not None and workspace.exists(original):
                found.append(path)
    return tuple(found)


def suggest(workspace: Workspace, original: str, content: str) -> str:
    path = suggested_path(original)
    workspace.write_text(path, content)
    return path


def _drop(workspace: Workspace, sibling: str) -> bool:
    try:
        workspace.remove(sibling)
    except OSError:
        return False
    return True


def _place(workspace: Workspace, sibling: str, original: str, content: bytes) -> bool:
    text = content.decode("utf-8", errors="replace")
    mine = workspace.read_text(original)
    if mine is not None and same_text(mine, text):
        return _drop(workspace, sibling)
    for target in (suggested_path(original), suggested_path(sibling)):
        held = workspace.read_text(target)
        if held is not None and same_text(held, text):
            return _drop(workspace, sibling)
        if held is None:
            try:
                workspace.write_bytes(target, content)
            except OSError:
                return False
            return _drop(workspace, sibling)
    return False


def relocate(workspace: Workspace) -> tuple[str, ...]:
    moved: list[str] = []
    for sibling in siblings(workspace):
        original = sibling_original(sibling)
        content = workspace.read_bytes(sibling)
        if original is None or content is None:
            continue
        if _place(workspace, sibling, original, content):
            moved.append(sibling)
    return tuple(moved)


def listed(workspace: Workspace, drop_adopted: bool = False) -> tuple[Suggestion, ...]:
    found: list[Suggestion] = []
    for path in workspace.files_under(SUGGESTED_DIR):
        new = workspace.read_text(path)
        if new is None:
            continue
        original = original_of(path)
        mine = workspace.read_text(original)
        if mine is not None and same_text(mine, new):
            if drop_adopted:
                _drop(workspace, path)
            continue
        added, removed = line_delta(mine or "", new)
        found.append(Suggestion(path, original, added, removed))
    return tuple(found)
