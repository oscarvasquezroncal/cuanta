from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from tests.git.test_workflow import ROOT, advance_remote, commit_file, git, remote_for, run, script

pytest_plugins = ("tests.git.test_workflow",)


@pytest.fixture
def release(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(ROOT / "scripts" / "git"))
    return importlib.import_module("tag_release")


def ci_run(sha: str, **changes: object) -> dict[str, object]:
    return {
        "databaseId": 123,
        "attempt": 2,
        "headSha": sha,
        "headBranch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "workflowName": "ci",
        **changes,
    }


def ci_detail(release: ModuleType, sha: str, **changes: object) -> dict[str, object]:
    return {
        **ci_run(sha),
        "jobs": [
            {"name": name, "status": "completed", "conclusion": "success"}
            for name in sorted(release.EXPECTED_JOBS)
        ],
        **changes,
    }


def fake_ci(
    release: ModuleType, monkeypatch: pytest.MonkeyPatch, sha: str
) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def invoke(*args: str) -> object:
        calls.append(args)
        return [ci_run(sha)] if args[:2] == ("run", "list") else ci_detail(release, sha)

    monkeypatch.setattr(release, "gh", invoke)
    return calls


@dataclass(frozen=True)
class TagRepo:
    root: Path
    remote: Path
    sha: str


@pytest.fixture
def tag_repo(repo: Path, release: ModuleType, monkeypatch: pytest.MonkeyPatch) -> TagRepo:
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "release-fixture"\nversion = "0.3.0"\n', encoding="utf-8"
    )
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## [0.3.0] - 2026-09-26\n", encoding="utf-8")
    git(repo, "add", "pyproject.toml", "CHANGELOG.md")
    git(repo, "commit", "-m", "docs(test): release metadata")
    remote = remote_for(repo)
    sha = git(repo, "rev-parse", "HEAD")
    monkeypatch.chdir(repo)
    monkeypatch.setattr(release, "repository", lambda origin: "example/cuanta")
    fake_ci(release, monkeypatch, sha)
    return TagRepo(repo, remote, sha)


def assert_refused(case: TagRepo, release: ModuleType, match: str) -> None:
    before = (
        git(case.root, "rev-parse", "HEAD"),
        git(case.remote, "rev-parse", "main"),
        git(case.root, "show-ref", "--tags") if git(case.root, "tag", "--list") else "",
        git(case.remote, "show-ref", "--tags") if git(case.remote, "tag", "--list") else "",
    )
    with pytest.raises(release.WorkflowError, match=match):
        release.publish_tag()
    assert git(case.root, "rev-parse", "HEAD") == before[0]
    assert git(case.remote, "rev-parse", "main") == before[1]
    assert (
        git(case.root, "show-ref", "--tags") if git(case.root, "tag", "--list") else ""
    ) == before[2]
    assert (
        git(case.remote, "show-ref", "--tags") if git(case.remote, "tag", "--list") else ""
    ) == before[3]


def test_tag_pushes_only_one_annotated_tag_at_checked_sha(
    tag_repo: TagRepo, release: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = tag_repo
    monkeypatch.setenv("GH_HOST", "enterprise.example.invalid")
    monkeypatch.setenv("GH_REPO", "other/irrelevant")
    git(case.root, "tag", "--annotate", "unrelated", "--message", "Keep local")
    git(case.root, "config", "push.followTags", "true")
    evidence = case.root / ".cuanta" / "evidence.txt"
    evidence.parent.mkdir()
    evidence.write_text("ignored", encoding="utf-8")
    calls = fake_ci(release, monkeypatch, case.sha)
    message = release.publish_tag()
    assert "v0.3.0" in message and case.sha in message
    assert "https://github.com/example/cuanta/actions/runs/123/attempts/2" in message
    assert git(case.root, "cat-file", "-t", "v0.3.0") == "tag"
    assert git(case.remote, "cat-file", "-t", "v0.3.0") == "tag"
    assert git(case.remote, "rev-parse", "v0.3.0^{}") == case.sha
    assert git(case.remote, "tag", "--list") == "v0.3.0"
    assert git(case.root, "rev-parse", "HEAD") == git(case.remote, "rev-parse", "main") == case.sha
    assert [call[:2] for call in calls] == [("run", "list"), ("run", "view"), ("run", "list")]
    for call in calls:
        assert call[call.index("--repo") + 1] == "github.com/example/cuanta"
        assert "--status" not in call
    assert calls[0][calls[0].index("--commit") + 1] == case.sha
    assert calls[0][calls[0].index("--workflow") + 1] == "ci.yml"
    assert calls[1][calls[1].index("--attempt") + 1] == "2"


@pytest.mark.parametrize(
    "state",
    [
        "worktree",
        "index",
        "untracked",
        "topic",
        "detached",
        "origin",
        "ahead",
        "behind",
        "push-url",
    ],
)
def test_tag_refuses_unsafe_repository_state(
    tag_repo: TagRepo, release: ModuleType, state: str
) -> None:
    case = tag_repo
    match = "clean"
    if state in ("worktree", "index"):
        (case.root / "CHANGELOG.md").write_text("changed", encoding="utf-8")
        if state == "index":
            git(case.root, "add", "CHANGELOG.md")
    elif state == "untracked":
        (case.root / "untracked.txt").write_text("changed", encoding="utf-8")
    elif state in ("topic", "detached"):
        git(
            case.root,
            "switch",
            "--detach" if state == "detached" else "-c",
            *([] if state == "detached" else ["topic"]),
        )
        match = "requires branch main"
    elif state == "origin":
        git(case.root, "remote", "remove", "origin")
        match = "No origin"
    elif state == "ahead":
        commit_file(case.root)
        match = "HEAD to equal origin main"
    elif state == "behind":
        advance_remote(case.root, case.remote)
        match = "HEAD to equal origin main"
    else:
        git(case.root, "remote", "set-url", "--push", "origin", "https://github.com/other/repo.git")
        match = "push URL"
    assert_refused(case, release, match)


@pytest.mark.parametrize("location", ["local", "remote"])
@pytest.mark.parametrize("annotated", [False, True])
def test_existing_release_tags_are_never_overwritten(
    tag_repo: TagRepo, release: ModuleType, location: str, annotated: bool
) -> None:
    case = tag_repo
    target = case.root if location == "local" else case.remote
    git(
        target,
        "-c",
        "user.name=Test User",
        "-c",
        "user.email=test@example.invalid",
        "tag",
        *(["--annotate", "--message", "Keep"] if annotated else []),
        "v0.3.0",
        "main",
    )
    assert_refused(case, release, "already exists")


@pytest.mark.parametrize(
    ("filename", "text", "message"),
    [
        ("pyproject.toml", "[project]\n", "project.version"),
        ("pyproject.toml", '[project]\nversion = "0.3"\n', "project.version"),
        ("pyproject.toml", '[project]\nversion = "0.3.0rc1"\n', "project.version"),
        ("pyproject.toml", "[project]\nversion = 3\n", "project.version"),
        ("pyproject.toml", "[broken", "valid TOML"),
        ("CHANGELOG.md", "## [Unreleased]\nMention 0.3.0 here\n", "dated release"),
        ("CHANGELOG.md", "## [0.3.01] - 2026-09-26\n", "dated release"),
        ("CHANGELOG.md", "## [0.3.0] - 2026-02-30\n", "invalid date"),
    ],
)
def test_tag_requires_committed_release_metadata(
    tag_repo: TagRepo,
    release: ModuleType,
    filename: str,
    text: str,
    message: str,
) -> None:
    case = tag_repo
    (case.root / filename).write_text(text, encoding="utf-8")
    git(case.root, "add", filename)
    git(case.root, "commit", "-m", "test(release): invalid metadata")
    git(case.root, "push", "origin", "main")
    assert_refused(case, release, message)


@pytest.mark.parametrize(
    "change",
    [
        {"status": "in_progress"},
        {"conclusion": "failure"},
        {"conclusion": "cancelled"},
        {"conclusion": "skipped"},
        {"headSha": "other"},
        {"headBranch": "topic"},
        {"event": "pull_request"},
        {"workflowName": "release"},
        {"databaseId": None},
        {"attempt": 0},
        {"attempt": True},
    ],
)
def test_ci_rejects_unsuccessful_or_mismatched_latest_run(
    release: ModuleType, monkeypatch: pytest.MonkeyPatch, change: dict[str, object]
) -> None:
    calls: list[tuple[str, ...]] = []

    def invoke(*args: str) -> object:
        calls.append(args)
        return [ci_run("sha", **change)]

    monkeypatch.setattr(release, "gh", invoke)
    with pytest.raises(release.WorkflowError):
        release.verify_ci("example/cuanta", "sha")
    assert len(calls) == 1


@pytest.mark.parametrize("payload", [[], {}, None, [None], [{}, {}]])
def test_ci_rejects_missing_or_malformed_latest_run(
    release: ModuleType, monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    monkeypatch.setattr(release, "gh", lambda *args: payload)
    with pytest.raises(release.WorkflowError):
        release.verify_ci("example/cuanta", "sha")


@pytest.mark.parametrize(
    "problem",
    [
        "empty",
        "missing",
        "duplicate",
        "failed",
        "pending",
        "skipped",
        "malformed",
        "attempt",
        "sha",
        "latest",
    ],
)
def test_ci_requires_all_jobs_from_unchanged_current_attempt(
    release: ModuleType, monkeypatch: pytest.MonkeyPatch, problem: str
) -> None:
    detail: dict[str, Any] = ci_detail(release, "sha")
    latest = ci_run("sha")
    if problem == "empty":
        detail["jobs"] = []
    elif problem == "missing":
        detail["jobs"].pop()
    elif problem == "duplicate":
        detail["jobs"][-1] = detail["jobs"][0]
    elif problem in ("failed", "skipped"):
        detail["jobs"][0]["conclusion"] = "failure" if problem == "failed" else "skipped"
    elif problem == "pending":
        detail["jobs"][0]["status"] = "in_progress"
    elif problem == "malformed":
        detail["jobs"][0] = None
    elif problem == "attempt":
        detail["attempt"] = 1
    elif problem == "sha":
        detail["headSha"] = "other"
    else:
        latest["attempt"] = 3
    responses = iter([[ci_run("sha")], detail, [latest]])
    monkeypatch.setattr(release, "gh", lambda *args: next(responses))
    with pytest.raises(release.WorkflowError):
        release.verify_ci("example/cuanta", "sha")


@pytest.mark.parametrize("change", ["head", "dirty", "branch", "remote", "tag", "origin"])
def test_repository_is_rechecked_after_ci_before_creating_a_tag(
    tag_repo: TagRepo, release: ModuleType, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    case = tag_repo

    def changed(repo: str, sha: str) -> str:
        if change == "head":
            commit_file(case.root)
        elif change == "dirty":
            (case.root / "dirty.txt").write_text("changed", encoding="utf-8")
        elif change == "branch":
            git(case.root, "switch", "-c", "topic")
        elif change == "remote":
            git(case.remote, "update-ref", "refs/heads/main", git(case.root, "rev-parse", "HEAD^"))
        elif change == "tag":
            git(case.root, "tag", "v0.3.0", "HEAD^")
        else:
            git(case.root, "remote", "set-url", "origin", "https://github.com/other/repo.git")
        return "https://github.com/example/cuanta/actions/runs/123"

    monkeypatch.setattr(release, "verify_ci", changed)
    with pytest.raises(release.WorkflowError):
        release.publish_tag()
    assert git(case.remote, "tag", "--list") == ""
    assert git(case.root, "tag", "--list") == ("v0.3.0" if change == "tag" else "")
    if change == "tag":
        assert git(case.root, "rev-parse", "v0.3.0") == git(case.root, "rev-parse", "HEAD^")


def test_failed_tag_push_retains_local_tag_and_refuses_automatic_retry(
    tag_repo: TagRepo, release: ModuleType
) -> None:
    case = tag_repo
    hook = case.remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    with pytest.raises(release.WorkflowError, match=r"local tag v0\.3\.0.*was retained"):
        release.publish_tag()
    assert git(case.root, "rev-parse", "v0.3.0^{}") == case.sha
    assert git(case.remote, "tag", "--list") == ""
    assert_refused(case, release, "Local tag.*already exists")


@pytest.mark.parametrize("failure", ["missing", "timeout", "exit", "json"])
def test_gh_boundary_fails_closed(
    release: ModuleType, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    def invoke(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["timeout"] == 60
        if failure == "missing":
            raise FileNotFoundError("gh")
        if failure == "timeout":
            raise subprocess.TimeoutExpired("gh", 60)
        return subprocess.CompletedProcess("gh", 1 if failure == "exit" else 0, "invalid", "denied")

    monkeypatch.setattr(release.subprocess, "run", invoke)
    with pytest.raises(release.WorkflowError, match="Cannot verify release CI"):
        release.gh("run", "list")


def test_gh_boundary_uses_argv_and_decodes_json(
    release: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def invoke(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert argv == ["gh", "run", "list", "--repo", "example/cuanta"]
        assert kwargs["timeout"] == 60 and kwargs["check"] is False
        return subprocess.CompletedProcess(argv, 0, json.dumps([ci_run("sha")]), "")

    monkeypatch.setattr(release.subprocess, "run", invoke)
    assert release.gh("run", "list", "--repo", "example/cuanta") == [ci_run("sha")]


@pytest.mark.parametrize("problem", ["pending", "missing jobs", "gh failure"])
def test_ci_refusal_never_creates_or_publishes_a_tag(
    tag_repo: TagRepo, release: ModuleType, monkeypatch: pytest.MonkeyPatch, problem: str
) -> None:
    def invoke(*args: str) -> object:
        if problem == "gh failure":
            raise release.WorkflowError("Cannot verify release CI")
        if args[:2] == ("run", "list"):
            return [
                ci_run(tag_repo.sha, status="in_progress" if problem == "pending" else "completed")
            ]
        return ci_detail(release, tag_repo.sha, jobs=[])

    monkeypatch.setattr(release, "gh", invoke)
    assert_refused(tag_repo, release, "CI")


def test_required_ci_jobs_match_the_committed_workflow_matrix(release: ModuleType) -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    gates, smoke = workflow.split("  install-smoke:", 1)
    systems = re.search(r"^        os: \[([^\]]+)\]$", gates, re.MULTILINE)
    versions = re.search(r"^        python: \[([^\]]+)\]$", gates, re.MULTILINE)
    smoke_systems = re.search(r"^        os: \[([^\]]+)\]$", smoke, re.MULTILINE)
    assert systems is not None and versions is not None and smoke_systems is not None
    expected = {
        f"gates ({system.strip()}, {version.strip().strip(chr(34))})"
        for system in systems[1].split(",")
        for version in versions[1].split(",")
    }
    expected.update(f"install-smoke ({system.strip()})" for system in smoke_systems[1].split(","))
    assert expected == release.EXPECTED_JOBS


@pytest.mark.parametrize(
    "origin",
    [
        "git@github.com:owner/project.git",
        "https://github.com/owner/project.git",
        "ssh://git@github.com/owner/project",
    ],
)
def test_repository_comes_from_origin(release: ModuleType, origin: str) -> None:
    assert release.repository(origin) == "owner/project"


@pytest.mark.parametrize(
    "origin",
    [
        "/tmp/remote.git",
        "https://example.org/owner/project",
        "https://github.com/owner/project/extra",
        "https://github.com/owner/project?query=1",
    ],
)
def test_ambiguous_origin_is_refused(release: ModuleType, origin: str) -> None:
    with pytest.raises(release.WorkflowError):
        release.repository(origin)


def test_unknown_push_arguments_never_publish(repo: Path) -> None:
    remote = remote_for(repo)
    head = git(remote, "rev-parse", "main")
    commit_file(repo)
    result = script(repo, "push", "--tags")
    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr
    assert git(remote, "rev-parse", "main") == head


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd launcher")
def test_cmd_launcher_forwards_tag_without_ordinary_push(repo: Path) -> None:
    remote = remote_for(repo)
    head = git(remote, "rev-parse", "main")
    commit_file(repo)
    (repo / "dirty.txt").write_text("dirty", encoding="utf-8")
    result = run(repo, "cmd.exe", "/d", "/c", "scripts\\git\\push.cmd", "--tag")
    assert result.returncode == 1
    assert "clean worktree" in result.stderr
    assert git(remote, "rev-parse", "main") == head
