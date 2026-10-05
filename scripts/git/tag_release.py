from __future__ import annotations

import json
import re
import sys
import tomllib
from datetime import date
from pathlib import Path

from git_workflow import WorkflowError, git, identity, require_main

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dev.gate_record import require_green as checked_green


def repository(origin: str) -> str:
    match = re.fullmatch(
        r"(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)"
        r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?",
        origin,
    )
    if match is None:
        raise WorkflowError("Release requires origin to identify a github.com owner/repository.")
    return match[1]


def require_clean() -> None:
    if git("status", "--porcelain", "--untracked-files=all"):
        raise WorkflowError("Release tagging requires a clean worktree and index.")


def origin_url() -> str:
    origin = git("remote", "get-url", "origin")
    if git("remote", "get-url", "--push", "--all", "origin") != origin:
        raise WorkflowError("Release tagging requires one origin push URL matching its fetch URL.")
    return origin


def require_remote_head(sha: str) -> None:
    expected = f"{sha}\trefs/heads/main"
    if git("ls-remote", "--heads", "origin", "refs/heads/main") != expected:
        raise WorkflowError(
            "Release tagging requires HEAD to equal origin main. Update or push main normally."
        )


def release_tag(sha: str) -> str:
    try:
        metadata = tomllib.loads(git("show", f"{sha}:pyproject.toml"))
    except tomllib.TOMLDecodeError as error:
        raise WorkflowError("Release pyproject.toml is not valid TOML.") from error
    project = metadata.get("project")
    version = project.get("version") if isinstance(project, dict) else None
    if (
        not isinstance(version, str)
        or re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version) is None
    ):
        raise WorkflowError("Release project.version must be an exact X.Y.Z version.")
    try:
        package = json.loads(git("show", f"{sha}:packaging/npm/package.json"))
    except ValueError as error:
        raise WorkflowError("Release package.json is invalid JSON") from error
    if not isinstance(package, dict) or package.get("version") != version:
        raise WorkflowError("Release package.json version must match project.version")
    changelog = git("show", f"{sha}:CHANGELOG.md")
    heading = re.search(
        rf"^## \[{re.escape(version)}\] - (\d{{4}}-\d{{2}}-\d{{2}})[ \t]*$",
        changelog,
        re.MULTILINE,
    )
    if heading is None:
        raise WorkflowError(f"CHANGELOG.md needs a dated release heading for {version}.")
    try:
        date.fromisoformat(heading[1])
    except ValueError as error:
        raise WorkflowError(f"CHANGELOG.md release {version} has an invalid date.") from error
    return f"v{version}"


def require_new_tag(tag: str) -> None:
    reference = f"refs/tags/{tag}"
    if reference in git("for-each-ref", "--format=%(refname)", reference).splitlines():
        raise WorkflowError(f"Local tag {tag} already exists; it will not be moved or republished.")
    if git("ls-remote", "--tags", "origin", reference):
        raise WorkflowError(f"Remote tag {tag} already exists; it will not be overwritten.")


def require_green(sha: str) -> str:
    tree = git("rev-parse", f"{sha}^{{tree}}")
    try:
        checked_green(Path(git("rev-parse", "--show-toplevel")), tree)
    except ValueError as error:
        raise WorkflowError(str(error)) from error
    return tree


def publish_tag() -> str:
    require_main()
    require_clean()
    identity()
    if "origin" not in git("remote").splitlines():
        raise WorkflowError(
            "No origin remote configured. Add one with: git remote add origin <URL>"
        )
    sha = git("rev-parse", "HEAD")
    origin = origin_url()
    repository(origin)
    git("fetch", "--no-tags", "origin", "refs/heads/main")
    require_remote_head(sha)
    tag = release_tag(sha)
    require_new_tag(tag)
    tree = require_green(sha)
    require_main()
    require_clean()
    if git("rev-parse", "HEAD") != sha or origin_url() != origin:
        raise WorkflowError("HEAD or origin changed during release validation. No tag was created.")
    require_remote_head(sha)
    require_new_tag(tag)
    require_green(sha)
    git("tag", "--annotate", tag, sha, "--message", f"Release {tag}")
    try:
        git("-c", "push.followTags=false", "push", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
    except (OSError, WorkflowError) as error:
        raise WorkflowError(
            f"Tag push failed; local tag {tag} at {sha} was retained. "
            f"No tag was deleted or moved. Check remote state before recovery. {error}"
        ) from error
    return f"Published annotated tag {tag} at {sha}. Local gate: {tree}"
