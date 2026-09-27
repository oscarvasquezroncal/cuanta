from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

READ_LINE_LIMIT = 400
POST_CONTEXT = (
    "Cuanta: use the index before exploration, read bounded line windows, "
    "keep protected files unchanged, and run verification through cuanta test."
)
_TEST_COMMAND = re.compile(
    r"^(?:(?:uv|poetry|pdm)\s+run\s+)?"
    r"(?:(?:python(?:3(?:\.\d+)?)?|py)\s+-m\s+)?pytest(?:\s|$)"
    r"|^(?:(?:npx|pnpm\s+exec)\s+)?(?:vitest|jest)(?:\s|$)"
    r"|^(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test(?:[\s:]|$)"
    r"|^(?:go|cargo|dotnet)\s+test(?:\s|$)",
    re.IGNORECASE,
)
_COMPOUND = re.compile(r"[;&|<>`\n\r]|\$\(|%[^%]+%")


@dataclass(frozen=True, slots=True)
class ReadDisciplineDecision:
    permission: str = ""
    reason: str = ""
    updated_input: dict[str, object] | None = None
    avoided_tokens: int = 0


def _positive(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _raw_test(command: str) -> bool:
    return any(
        _TEST_COMMAND.match(part.strip().lstrip("(")) for part in re.split(r"[;&|\n\r]+", command)
    )


def decide_read_discipline(
    tool_name: str,
    tool_input: Mapping[str, object],
    file_lines: int = 0,
    file_characters: int = 0,
) -> ReadDisciplineDecision:
    if (
        tool_name == "Read"
        and file_lines > READ_LINE_LIMIT
        and not (_positive(tool_input.get("offset")) and _positive(tool_input.get("limit")))
    ):
        return ReadDisciplineDecision(
            "deny",
            "Large Read blocked: use an explicit offset and limit or the cuanta page tool.",
            avoided_tokens=max(0, (file_characters + 3) // 4),
        )
    if tool_name == "Grep":
        path = tool_input.get("path", ".")
        root = path in {"", ".", "./"} if isinstance(path, str) else False
        glob = tool_input.get("glob", "")
        scoped = bool(tool_input.get("type")) or (
            isinstance(glob, str) and glob not in {"", "*", "**", "**/*"}
        )
        if root and not scoped and tool_input.get("output_mode") == "content":
            return ReadDisciplineDecision(
                "deny", "Whole-tree content Grep blocked: select a path, glob or file type."
            )
    if tool_name in {"Bash", "PowerShell"}:
        command = tool_input.get("command")
        if isinstance(command, str) and _raw_test(command):
            if _COMPOUND.search(command) or not _TEST_COMMAND.match(command.strip()):
                return ReadDisciplineDecision(
                    "deny", "Compound test command blocked: run cuanta test as a separate command."
                )
            updated = {**tool_input, "command": "cuanta test"}
            return ReadDisciplineDecision(
                "allow", "Raw tests redirected to the normal cuanta test gate.", updated
            )
    return ReadDisciplineDecision()
