from __future__ import annotations

import json
import re
import subprocess
import tomllib
from datetime import date

from git_workflow import WorkflowError, git, identity, require_main

EXPECTED_JOBS = frozenset(
    [
        f"gates ({system}, {python})"
        for system in ("ubuntu-latest", "macos-latest", "windows-latest")
        for python in ("3.12", "3.13")
    ]
    + [
        f"install-smoke ({system})"
        for system in ("ubuntu-latest", "macos-latest", "windows-latest")
    ]
)
RUN_FIELDS = "databaseId,headSha,headBranch,event,status,conclusion,attempt,workflowName"


def gh(*args: str) -> object:
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorkflowError(f"Cannot verify release CI with gh: {error}") from error
    if result.returncode:
        raise WorkflowError(f"Cannot verify release CI with gh: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise WorkflowError("Cannot verify release CI: gh returned invalid JSON.") from error


def repository(origin: str) -> str:
    match = re.fullmatch(
        r"(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)"
        r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?",
        origin,
    )
    if match is None:
        raise WorkflowError("Release CI requires origin to identify a github.com owner/repository.")
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
            "Release tagging requires HEAD to equal origin main. "
            "Update or push main normally, then wait for its CI."
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


def record(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise WorkflowError("Cannot verify release CI: gh returned an invalid run record.")
    return value


def positive_number(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise WorkflowError("Cannot verify release CI: missing run ID or attempt.")
    return value


def require_success(run: dict[str, object], sha: str) -> tuple[int, int]:
    if (
        run.get("headSha") != sha
        or run.get("headBranch") != "main"
        or run.get("event") != "push"
        or run.get("workflowName") != "ci"
    ):
        raise WorkflowError("Release CI must be ci.yml for this exact HEAD on main (push event).")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise WorkflowError("The latest CI run for this HEAD is not completed successfully.")
    return positive_number(run.get("databaseId")), positive_number(run.get("attempt"))


def latest_run(repo: str, sha: str) -> dict[str, object]:
    value = gh(
        "run",
        "list",
        "--repo",
        f"github.com/{repo}",
        "--workflow",
        "ci.yml",
        "--branch",
        "main",
        "--commit",
        sha,
        "--event",
        "push",
        "--limit",
        "1",
        "--json",
        RUN_FIELDS,
    )
    if not isinstance(value, list) or len(value) != 1:
        raise WorkflowError("Cannot verify release CI: expected the latest exact-HEAD CI run.")
    return record(value[0])


def verify_ci(repo: str, sha: str) -> str:
    run_id, attempt = require_success(latest_run(repo, sha), sha)
    detail = record(
        gh(
            "run",
            "view",
            str(run_id),
            "--repo",
            f"github.com/{repo}",
            "--attempt",
            str(attempt),
            "--json",
            f"{RUN_FIELDS},jobs",
        )
    )
    if require_success(detail, sha) != (run_id, attempt):
        raise WorkflowError("Release CI run or attempt changed during validation.")
    jobs = detail.get("jobs")
    if not isinstance(jobs, list) or len(jobs) != len(EXPECTED_JOBS):
        raise WorkflowError(
            "Release CI must contain all nine required gate and install-smoke jobs."
        )
    names: set[str] = set()
    for raw in jobs:
        job = record(raw)
        name = job.get("name")
        if (
            not isinstance(name, str)
            or job.get("status") != "completed"
            or job.get("conclusion") != "success"
        ):
            raise WorkflowError("Every required CI job must be completed successfully.")
        names.add(name)
    if names != EXPECTED_JOBS:
        raise WorkflowError("Release CI is missing required gate or install-smoke jobs.")
    if require_success(latest_run(repo, sha), sha) != (run_id, attempt):
        raise WorkflowError("The latest CI run or attempt changed during validation.")
    return f"https://github.com/{repo}/actions/runs/{run_id}/attempts/{attempt}"


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
    repo = repository(origin)
    git("fetch", "--no-tags", "origin", "refs/heads/main")
    require_remote_head(sha)
    tag = release_tag(sha)
    require_new_tag(tag)
    run_url = verify_ci(repo, sha)
    require_main()
    require_clean()
    if git("rev-parse", "HEAD") != sha or origin_url() != origin:
        raise WorkflowError("HEAD or origin changed during release validation. No tag was created.")
    require_remote_head(sha)
    require_new_tag(tag)
    git("tag", "--annotate", tag, sha, "--message", f"Release {tag}")
    try:
        git("-c", "push.followTags=false", "push", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
    except (OSError, WorkflowError) as error:
        raise WorkflowError(
            f"Tag push failed; local tag {tag} at {sha} was retained. "
            f"No tag was deleted or moved. Check remote state before recovery. {error}"
        ) from error
    return f"Published annotated tag {tag} at {sha}. CI: {run_url}"
