from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ("Claude", "Anthropic", "Codex", "OpenAI", "ChatGPT", "OpenCode", "Copilot")


def run(root: Path, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=root,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )


def git(root: Path, *args: str) -> str:
    result = run(root, "git", *args)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def new_repo(root: Path, *, bare: bool = False) -> Path:
    root.mkdir()
    git(root, "init", "--initial-branch=main", *(["--bare"] if bare else []))
    if not bare:
        git(root, "config", "user.name", "Test User")
        git(root, "config", "user.email", "test@example.invalid")
    return root


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for variable in tuple(os.environ):
        if variable.startswith("GIT_"):
            monkeypatch.delenv(variable)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    monkeypatch.setenv("UV_OFFLINE", "1")
    root = new_repo(tmp_path / "working copy")
    shutil.copytree(ROOT / "scripts" / "git", root / "scripts" / "git")
    shutil.copytree(ROOT / ".githooks", root / ".githooks")
    shutil.copy2(ROOT / ".gitattributes", root / ".gitattributes")
    shutil.copy2(ROOT / ".gitignore", root / ".gitignore")
    (root / ".githooks" / "commit-msg").chmod(0o755)
    git(root, "config", "core.hooksPath", ".githooks")
    git(root, "add", ".")
    git(root, "commit", "-m", "feat(git): seed fixture")
    return root


def script(repo: Path, name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return run(repo, sys.executable, str(repo / "scripts" / "git" / f"{name}.py"), *args)


def commit_file(repo: Path, name: str = "change.txt", message: str = "feat(test): change") -> str:
    (repo / name).write_text(message, encoding="utf-8")
    git(repo, "add", "--", name)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def remote_for(repo: Path) -> Path:
    remote = new_repo(repo.parent / "remote.git", bare=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    return remote


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_hook_strips_markers_preserves_humans_and_trims(
    repo: Path, tool: str, newline: str
) -> None:
    message = newline.join(
        (
            "test(git): preserve human work",
            "",
            "Keep the Claude adapter and the OpenAI documentation.",
            "",
            "Co-authored-by: Human Contributor <human@example.invalid>",
            "Generated with a deterministic compiler",
            f"  cO-aUtHoReD-bY : {tool.swapcase()} <bot@example.invalid>",
            f"🤖 gEnErAtEd WiTh [{tool.swapcase()}](https://example.invalid)",
            "Generated with AI / Gemini / Cursor / Windsurf / Aider / Devin / Codeium",
            " cLaUdE-SeSsIoN : example",
            "See https://CLAUDE.AI/code/session_123",
            "",
            " \t",
        )
    )
    message_path = repo / ".git" / "message.txt"
    message_path.write_bytes(message.encode("utf-8"))
    git(repo, "commit", "--allow-empty", "--cleanup=verbatim", "--file", str(message_path))
    expected = (
        "test(git): preserve human work\n\n"
        "Keep the Claude adapter and the OpenAI documentation.\n\n"
        "Co-authored-by: Human Contributor <human@example.invalid>\n"
        "Generated with a deterministic compiler\n"
    )
    assert (repo / ".git" / "COMMIT_EDITMSG").read_bytes() == expected.encode("utf-8")
    assert git(repo, "log", "-1", "--format=%B") == expected.rstrip()


def test_hook_never_fails_for_missing_message(repo: Path) -> None:
    git_binary = shutil.which("git")
    assert git_binary is not None
    shell = shutil.which("sh")
    if os.name == "nt":
        shell = str(Path(git_binary).resolve().parents[1] / "bin" / "sh.exe")
    assert shell is not None
    result = run(repo, shell, str(repo / ".githooks" / "commit-msg"), "missing")
    assert result.returncode == 0


@pytest.mark.parametrize("branch", ["topic", "detached"])
def test_commit_refuses_off_main_before_staging(repo: Path, branch: str) -> None:
    git(
        repo,
        "switch",
        "--detach" if branch == "detached" else "-c",
        *(["topic"] if branch == "topic" else []),
    )
    (repo / "change.txt").write_text("unchanged index", encoding="utf-8")
    before = git(repo, "rev-parse", "HEAD")
    result = script(repo, "commit", "-m", "fix(test): refuse")
    assert result.returncode == 1
    assert "requires branch main" in result.stderr
    assert git(repo, "diff", "--cached") == ""
    assert git(repo, "rev-parse", "HEAD") == before


@pytest.mark.parametrize("state", ["unset", "wrong", "missing"])
def test_commit_requires_active_hook(repo: Path, state: str) -> None:
    if state == "unset":
        git(repo, "config", "--unset", "core.hooksPath")
    elif state == "wrong":
        git(repo, "config", "core.hooksPath", ".git/hooks")
    else:
        (repo / ".githooks" / "commit-msg").unlink()
    result = script(repo, "commit", "-m", "fix(test): refuse")
    assert result.returncode == 1
    assert "setup.cmd" in result.stderr
    assert git(repo, "diff", "--cached") == ""


@pytest.mark.parametrize("paths", [True, False])
def test_commit_stages_selected_paths_or_all(repo: Path, paths: bool) -> None:
    (repo / "chosen file.txt").write_text("one", encoding="utf-8")
    (repo / "other.txt").write_text("two", encoding="utf-8")
    result = script(
        repo,
        "commit",
        "-m",
        "feat(test): stage files",
        *(["chosen file.txt"] if paths else []),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "feat(test): stage files" in result.stdout
    changed = git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()
    assert changed == (["chosen file.txt"] if paths else ["chosen file.txt", "other.txt"])


def test_commit_rejects_nonconventional_message_before_staging(repo: Path) -> None:
    (repo / "change.txt").write_text("one", encoding="utf-8")
    result = script(repo, "commit", "-m", "misc changes")
    assert result.returncode == 1
    assert "conventional" in result.stderr
    assert git(repo, "diff", "--cached") == ""


def test_commit_repairs_only_just_created_message(repo: Path) -> None:
    hook = repo / ".githooks" / "commit-msg"
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8", newline="\n")
    original = git(repo, "rev-parse", "HEAD")
    (repo / "chosen.txt").write_text("one", encoding="utf-8")
    result = script(
        repo,
        "commit",
        "-m",
        "fix(test): repair message\n\nCo-Authored-By: Codex <bot@example.invalid>",
        "chosen.txt",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert git(repo, "rev-parse", "HEAD^") == original
    assert git(repo, "log", "-1", "--format=%B") == "fix(test): repair message"
    assert git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD") == "chosen.txt"


def test_failed_commit_does_not_amend_previous_commit(repo: Path) -> None:
    original = git(repo, "rev-parse", "HEAD")
    result = script(repo, "commit", "-m", "test(git): nothing to commit")
    assert result.returncode == 1
    assert git(repo, "rev-parse", "HEAD") == original


@pytest.mark.parametrize("filename", ["cuanta-readme.zip", "ARCHIVE.ZIP"])
def test_commit_refuses_even_force_staged_zip_archives(repo: Path, filename: str) -> None:
    (repo / filename).write_bytes(b"fixture")
    assert git(repo, "check-ignore", filename) == filename
    git(repo, "add", "--force", "--", filename)
    before = git(repo, "rev-parse", "HEAD")
    result = script(repo, "commit", "-m", "docs(test): archive")
    assert result.returncode == 1
    assert "ZIP archives cannot be committed" in result.stderr
    assert git(repo, "rev-parse", "HEAD") == before


def test_push_without_remote_explains_setup(repo: Path) -> None:
    result = script(repo, "push")
    assert result.returncode == 1
    assert "git remote add origin <URL>" in result.stderr


@pytest.mark.parametrize("detached", [False, True])
def test_push_refuses_off_main(repo: Path, detached: bool) -> None:
    remote = remote_for(repo)
    before = git(remote, "rev-parse", "main")
    if detached:
        git(repo, "switch", "--detach")
    else:
        git(repo, "switch", "-c", "topic")
    result = script(repo, "push")
    assert result.returncode == 1
    assert "requires branch main" in result.stderr
    assert git(remote, "rev-parse", "main") == before


@pytest.mark.parametrize(
    "marker",
    [
        *(f"Co-Authored-By: {tool} <bot@example.invalid>" for tool in TOOLS),
        "Generated with OpenCode",
        "Generated with AI",
        "Generated with Cursor",
        "Claude-Session: abc",
        "https://claude.ai/code/session_123",
    ],
)
def test_push_rejects_every_outgoing_marker(repo: Path, marker: str) -> None:
    remote = remote_for(repo)
    before = git(remote, "rev-parse", "main")
    git(
        repo,
        "-c",
        "core.hooksPath=",
        "commit",
        "--allow-empty",
        "-m",
        f"test(git): dirty\n\n{marker}",
    )
    commit_file(repo)
    result = script(repo, "push")
    assert result.returncode == 1
    assert "AI attribution" in result.stderr
    assert git(remote, "rev-parse", "main") == before


@pytest.mark.parametrize(
    "variable", ["GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"]
)
def test_push_rejects_identity_mismatch(
    repo: Path, monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    remote = remote_for(repo)
    before = git(remote, "rev-parse", "main")
    monkeypatch.setenv(variable, "other@example.invalid" if variable.endswith("EMAIL") else "Other")
    commit_file(repo)
    monkeypatch.delenv(variable)
    result = script(repo, "push")
    assert result.returncode == 1
    assert "configured identity" in result.stderr
    assert git(remote, "rev-parse", "main") == before


def test_push_clean_commits_to_initial_remote(repo: Path) -> None:
    remote = new_repo(repo.parent / "remote.git", bare=True)
    git(repo, "remote", "add", "origin", str(remote))
    result = script(repo, "push")
    assert result.returncode == 0, result.stdout + result.stderr
    assert git(remote, "rev-parse", "main") == git(repo, "rev-parse", "main")


def test_push_checks_all_history_for_new_remote(repo: Path) -> None:
    remote = new_repo(repo.parent / "remote.git", bare=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(
        repo,
        "-c",
        "core.hooksPath=",
        "commit",
        "--allow-empty",
        "-m",
        "test(git): dirty\n\nClaude-Session: hidden",
    )
    result = script(repo, "push")
    assert result.returncode == 1
    assert "AI attribution" in result.stderr
    assert git(remote, "for-each-ref", "refs/heads") == ""


def test_push_stops_on_fetch_failure(repo: Path) -> None:
    git(repo, "remote", "add", "origin", str(repo.parent / "missing.git"))
    before = git(repo, "rev-parse", "main")
    result = script(repo, "push")
    assert result.returncode == 1
    assert "Push completed" not in result.stdout
    assert git(repo, "rev-parse", "main") == before


def advance_remote(repo: Path, remote: Path) -> None:
    peer = new_repo(repo.parent / "peer")
    git(peer, "remote", "add", "origin", str(remote))
    git(peer, "fetch", "origin")
    git(peer, "merge", "--ff-only", "origin/main")
    commit_file(peer, "remote.txt", "feat(test): remote change")
    git(peer, "push", "origin", "main")


def test_push_fast_forwards_when_behind(repo: Path) -> None:
    remote = remote_for(repo)
    advance_remote(repo, remote)
    result = script(repo, "push")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo / "remote.txt").exists()
    assert git(repo, "rev-parse", "main") == git(remote, "rev-parse", "main")


def test_push_refuses_divergence_without_changing_local_history(repo: Path) -> None:
    remote = remote_for(repo)
    advance_remote(repo, remote)
    local = commit_file(repo)
    before = git(remote, "rev-parse", "main")
    result = script(repo, "push")
    assert result.returncode == 1
    assert "diverged" in result.stderr
    assert git(repo, "rev-parse", "main") == local
    assert git(remote, "rev-parse", "main") == before


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd launcher")
def test_setup_checks_identity_then_enables_hook(repo: Path) -> None:
    git(repo, "config", "--unset", "core.hooksPath")
    git(repo, "config", "--unset", "user.email")
    result = run(repo, "cmd.exe", "/d", "/c", "scripts\\git\\setup.cmd")
    assert result.returncode == 1
    assert "user.email" in result.stderr
    assert run(repo, "git", "config", "--get", "core.hooksPath").returncode == 1
    git(repo, "config", "user.email", "test@example.invalid")
    result = run(repo, "cmd.exe", "/d", "/c", "scripts\\git\\setup.cmd")
    assert result.returncode == 0, result.stdout + result.stderr
    assert git(repo, "config", "--get", "core.hooksPath") == ".githooks"


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd launcher")
def test_cmd_launchers_preserve_message_and_paths_with_spaces(repo: Path) -> None:
    (repo / "chosen file.txt").write_text("one", encoding="utf-8")
    result = run(
        repo,
        "cmd.exe",
        "/d",
        "/c",
        "scripts\\git\\commit.cmd",
        "-m",
        "feat(git): cmd launch",
        "chosen file.txt",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert git(repo, "log", "-1", "--format=%B") == "feat(git): cmd launch"
    result = run(repo, "cmd.exe", "/d", "/c", "scripts\\git\\push.cmd")
    assert result.returncode == 1
    assert "git remote add origin" in result.stderr
