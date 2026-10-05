from __future__ import annotations

import importlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest
from tests.git.test_workflow import ROOT, advance_remote, commit_file, git, remote_for, run, script

pytest_plugins = ("tests.git.test_workflow",)


@pytest.fixture
def release(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(ROOT / "scripts" / "git"))
    return importlib.import_module("tag_release")


def fake_green(case: TagRepo) -> Path:
    tree = git(case.root, "rev-parse", "HEAD^{tree}")
    folder = case.root / ".cuanta" / "gates"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{tree}.json"
    path.write_text(
        json.dumps(
            {
                "tree": tree,
                "head": case.sha,
                "status": "green",
                "finished_at": "2026-10-05T12:00:00+00:00",
                "counts": {"static": 3, "functional": 10, "performance": 2},
            }
        ),
        encoding="utf-8",
    )
    return path


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
    (repo / "packaging/npm").mkdir(parents=True)
    (repo / "packaging/npm/package.json").write_text(
        json.dumps({"version": "0.3.0"}), encoding="utf-8"
    )
    git(repo, "add", "pyproject.toml", "CHANGELOG.md", "packaging/npm/package.json")
    git(repo, "commit", "-m", "docs(test): release metadata")
    remote = remote_for(repo)
    sha = git(repo, "rev-parse", "HEAD")
    monkeypatch.chdir(repo)
    monkeypatch.setattr(release, "repository", lambda origin: "example/cuanta")
    case = TagRepo(repo, remote, sha)
    fake_green(case)
    if hasattr(release, "gh"):
        monkeypatch.setattr(release, "gh", lambda *args: pytest.fail("CI queries forbidden"))
    return case


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
    evidence.parent.mkdir(exist_ok=True)
    evidence.write_text("ignored", encoding="utf-8")
    message = release.publish_tag()
    assert "v0.3.0" in message and case.sha in message
    assert "Local gate:" in message
    assert git(case.root, "cat-file", "-t", "v0.3.0") == "tag"
    assert git(case.remote, "cat-file", "-t", "v0.3.0") == "tag"
    assert git(case.remote, "rev-parse", "v0.3.0^{}") == case.sha
    assert git(case.remote, "tag", "--list") == "v0.3.0"
    assert git(case.root, "rev-parse", "HEAD") == git(case.remote, "rev-parse", "main") == case.sha


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


@pytest.mark.parametrize("change", ["head", "dirty", "branch", "remote", "tag", "origin"])
def test_repository_is_rechecked_after_local_gate_before_creating_a_tag(
    tag_repo: TagRepo, release: ModuleType, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    case = tag_repo

    def changed(sha: str) -> str:
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

    monkeypatch.setattr(release, "require_green", changed)
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


@pytest.mark.parametrize("problem", ["missing", "invalid", "failed", "tree", "partial"])
def test_local_gate_refusal_never_creates_a_tag(
    tag_repo: TagRepo, release: ModuleType, problem: str
) -> None:
    path = fake_green(tag_repo)
    if problem == "missing":
        path.unlink()
    elif problem == "invalid":
        path.write_text("broken", encoding="utf-8")
    else:
        record = json.loads(path.read_text(encoding="utf-8"))
        if problem == "failed":
            record["status"] = "failed"
        elif problem == "tree":
            record["tree"] = "0" * 40
        else:
            record["counts"]["performance"] = 0
        path.write_text(json.dumps(record), encoding="utf-8")
    assert_refused(tag_repo, release, "gate")


def test_committed_npm_version_must_match_python(tag_repo: TagRepo, release: ModuleType) -> None:
    path = tag_repo.root / "packaging/npm/package.json"
    path.write_text('{"version": "0.3.1"}', encoding="utf-8")
    git(tag_repo.root, "add", str(path))
    git(tag_repo.root, "commit", "-m", "test(release): drift fixture")
    with pytest.raises(release.WorkflowError, match="package"):
        release.release_tag(git(tag_repo.root, "rev-parse", "HEAD"))


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
