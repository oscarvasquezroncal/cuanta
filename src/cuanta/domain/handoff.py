from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from cuanta.domain.report import slug
from cuanta.domain.sandbox import docs_only, trial_folder
from cuanta.domain.shells import Shell


class Workflow(StrEnum):
    BRANCHES = "branches"
    TRUNK = "trunk"


WORKFLOWS = tuple(item.value for item in Workflow)
COMMIT_TYPES = {"feature": "feat", "bug": "fix", "refactor": "refactor"}
FALLBACK_TYPE = "chore"
INVESTIGATION = "investigation"
CONVENTIONAL = re.compile(
    r"(?:feat|fix|test|docs|refactor|perf|build|ci|chore|style|revert)"
    r"(?:\([^\r\n()]+\))?!?: \S[^\r\n]*"
)
SUBJECT_LIMIT = 72
BRANCH_SLUG_LIMIT = 40
BRANCH_SUFFIX = 4
PATHS_FILE = "paths.nul"
COMMIT_FILE = "commit.txt"
HEAD_PREFIX = "ref: refs/heads/"
GITDIR_PREFIX = "gitdir:"


@dataclass(frozen=True, slots=True)
class Handoff:
    workflow: Workflow
    kind: str
    subject: str
    branch: str
    current_branch: str
    commands: tuple[str, ...]
    chained: str = ""
    uncommitted: tuple[str, ...] = ()
    untracked: tuple[str, ...] = ()


def change_type(task_type: str, paths: Iterable[str]) -> str:
    if task_type == INVESTIGATION:
        return ""
    if docs_only(paths):
        return "docs"
    return COMMIT_TYPES.get(task_type, FALLBACK_TYPE)


def proposed_subject(proposal: str) -> str:
    for line in proposal.splitlines():
        cleaned = line.strip().strip("`*>\"' ").removeprefix("- ").strip()
        if CONVENTIONAL.fullmatch(cleaned):
            return _clip(cleaned)
    return ""


def _clip(text: str) -> str:
    if len(text) <= SUBJECT_LIMIT:
        return text
    cut = text[:SUBJECT_LIMIT].rsplit(" ", 1)[0]
    return cut or text[:SUBJECT_LIMIT]


def commit_subject(kind: str, what: str, proposal: str) -> str:
    proposed = proposed_subject(proposal)
    if proposed:
        return proposed
    summary = " ".join(what.split()).rstrip(".")
    summary = summary[:1].lower() + summary[1:] if summary[:2].istitle() else summary
    return _clip(f"{kind}: {summary}" if summary else f"{kind}: apply cuanta changes")


def branch_name(kind: str, subject: str, fallback: str) -> str:
    body = subject.split(": ", 1)[1] if ": " in subject else subject
    name = slug(body, BRANCH_SLUG_LIMIT)
    if name == "report":
        return f"{kind}/{fallback.lower()[-8:]}"
    return f"{kind}/{name}-{fallback.lower()[-BRANCH_SUFFIX:]}"


def branch_from_head(text: str) -> str:
    line = text.strip()
    return line.removeprefix(HEAD_PREFIX) if line.startswith(HEAD_PREFIX) else ""


def gitdir_from_file(text: str) -> str:
    line = text.strip()
    return line.removeprefix(GITDIR_PREFIX).strip() if line.startswith(GITDIR_PREFIX) else ""


def cd_line(path: str, shell: Shell) -> str | None:
    if shell is Shell.CMD:
        return f'cd /d "{path}"'
    if shell is Shell.POWERSHELL:
        return "Set-Location -LiteralPath '" + path.replace("'", "''") + "'"
    if shell is Shell.FISH:
        return "cd '" + path.replace("\\", "\\\\").replace("'", "\\'") + "'"
    if shell in {Shell.BASH, Shell.ZSH}:
        return "cd '" + path.replace("'", "'\\''") + "'"
    return None


def handoff_commands(
    project: str,
    run_id: str,
    shell: Shell,
    branch: str = "",
    pending: bool = True,
    git: bool = True,
) -> tuple[str, ...]:
    folder = trial_folder(run_id)
    lines: list[str] = []
    change_dir = cd_line(project, shell)
    if change_dir is not None:
        lines.append(change_dir)
    if git and branch:
        lines.append(f"git switch -c {branch}")
    if pending:
        lines.append(f"cuanta runs apply {run_id} --yes")
    if git:
        paths = f"--pathspec-from-file={folder}/{PATHS_FILE} --pathspec-file-nul"
        lines.append(f"git --literal-pathspecs add -A {paths}")
        lines.append(f"git --literal-pathspecs commit -F {folder}/{COMMIT_FILE} {paths}")
    return tuple(lines)


def chain(commands: tuple[str, ...], shell: Shell) -> str:
    if not commands:
        return ""
    if shell is Shell.POWERSHELL:
        text = commands[-1]
        for command in reversed(commands[:-1]):
            text = f"{command}; if ($?) {{ {text} }}"
        return text
    joiner = "; and " if shell is Shell.FISH else " && "
    return joiner.join(commands)


def handoff(
    workflow: Workflow,
    kind: str,
    subject: str,
    run_id: str,
    project: str,
    shell: Shell,
    current_branch: str,
    pending: bool,
    git: bool,
    uncommitted: tuple[str, ...] = (),
    untracked: tuple[str, ...] = (),
) -> Handoff:
    branch = branch_name(kind, subject, run_id) if workflow is Workflow.BRANCHES else ""
    commands = handoff_commands(project, run_id, shell, branch, pending, git)
    return Handoff(
        workflow,
        kind,
        subject,
        branch,
        current_branch,
        commands,
        chain(commands, shell),
        uncommitted,
        untracked,
    )


def parse_workflow(value: str) -> Workflow:
    return Workflow(value) if value in WORKFLOWS else Workflow.BRANCHES
