from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

AI_TOOL = re.compile(
    r"(?<![\w])(?:claude|anthropic|codex|openai|chatgpt|opencode|copilot)(?![\w])",
    re.IGNORECASE,
)
AI_GENERATOR = re.compile(r"\b(?:ai|gemini|cursor|windsurf|aider|devin|codeium)\b", re.IGNORECASE)
CONVENTIONAL = re.compile(
    r"(?:feat|fix|test|docs|refactor|perf|build|ci|chore|style|revert)"
    r"(?:\([^\r\n()]+\))?!?: \S[^\r\n]*"
)


class WorkflowError(Exception):
    pass


def git(*args: str, input_text: str | None = None, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and result.returncode:
        raise WorkflowError(result.stderr.strip() or result.stdout.strip() or "Git failed.")
    return result.stdout.strip()


def require_main() -> None:
    if git("symbolic-ref", "--quiet", "--short", "HEAD", check=False) != "main":
        raise WorkflowError("This workflow requires branch main. No changes were made.")


def is_ai_marker(line: str) -> bool:
    lower = line.strip().lower()
    return bool(
        re.match(r"claude-session\s*:", lower)
        or "claude.ai/code/session_" in lower
        or (AI_TOOL.search(line) and re.match(r"co-authored-by\s*:", lower))
        or (
            re.search(r"generated\s+with", lower)
            and (AI_TOOL.search(line) or AI_GENERATOR.search(line))
        )
    )


def clean_message(message: str) -> str:
    lines = [line for line in message.splitlines() if not is_ai_marker(line)]
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) + "\n"


def require_hook() -> None:
    root = Path(git("rev-parse", "--show-toplevel"))
    configured = git("config", "--get", "core.hooksPath", check=False)
    hooks = Path(configured).expanduser()
    if not hooks.is_absolute():
        hooks = root / hooks
    hook = hooks / "commit-msg"
    if (
        not configured
        or hooks.resolve() != (root / ".githooks").resolve()
        or not hook.is_file()
        or (os.name != "nt" and not os.access(hook, os.X_OK))
    ):
        raise WorkflowError("The hook is not active. Run scripts\\git\\setup.cmd first.")


def identity() -> tuple[str, str]:
    name = git("config", "--get", "user.name", check=False)
    email = git("config", "--get", "user.email", check=False)
    if not name or not email:
        raise WorkflowError("Set git user.name and user.email before pushing.")
    return name, email


def check_outgoing(revision: str) -> None:
    name, email = identity()
    for commit in git("rev-list", "--reverse", revision).splitlines():
        details = git("show", "-s", "--format=%an%x00%ae%x00%cn%x00%ce%x00%B", commit)
        author, author_email, committer, committer_email, message = details.split("\0", 4)
        if any(is_ai_marker(line) for line in message.splitlines()):
            raise WorkflowError(f"Commit {commit[:12]} contains AI attribution. Push stopped.")
        if (author, author_email, committer, committer_email) != (name, email, name, email):
            raise WorkflowError(
                f"Commit {commit[:12]} author or committer differs from configured identity. "
                "Push stopped; history was not rewritten."
            )
