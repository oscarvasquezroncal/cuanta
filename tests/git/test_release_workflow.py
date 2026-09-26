from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path

import pytest
from tests.git.test_workflow import ROOT, git, repo, run

__all__ = ["repo"]


def metadata_step() -> str:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    match = re.search(
        r"^\s+uv run --no-project --python 3\.12 python - <<'PY'\n(.*?)^\s+PY$",
        workflow,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    return textwrap.dedent(match[1])


@pytest.fixture
def release_commit(repo: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    (repo / "pyproject.toml").write_text('[project]\nversion = "0.3.0"\n', encoding="utf-8")
    (repo / "CHANGELOG.md").write_text("## [0.3.0] - 2026-09-26\n", encoding="utf-8")
    git(repo, "add", "pyproject.toml", "CHANGELOG.md")
    git(repo, "commit", "-m", "chore(release): prepare fixture")
    output = repo.parent / "github-output"
    monkeypatch.setenv("GITHUB_SHA", git(repo, "rev-parse", "HEAD"))
    monkeypatch.setenv("GITHUB_REF_TYPE", "tag")
    monkeypatch.setenv("GITHUB_REF_NAME", "v0.3.0")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    return repo, output


def test_release_workflow_emits_only_the_validated_version(
    release_commit: tuple[Path, Path],
) -> None:
    repository, output = release_commit
    result = run(repository, sys.executable, "-c", metadata_step())
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_text(encoding="utf-8") == "version=0.3.0\n"
    assert git(repository, "tag", "--list") == ""


@pytest.mark.parametrize(
    ("ref_type", "ref_name"),
    [("branch", "v0.3.0"), ("branch", "main"), ("tag", "v0.3.1"), ("tag", "v0.3.0-rc1")],
)
def test_release_workflow_rejects_a_ref_that_is_not_the_release_tag(
    release_commit: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    ref_type: str,
    ref_name: str,
) -> None:
    repository, output = release_commit
    monkeypatch.setenv("GITHUB_REF_TYPE", ref_type)
    monkeypatch.setenv("GITHUB_REF_NAME", ref_name)
    result = run(repository, sys.executable, "-c", metadata_step())
    assert result.returncode != 0
    assert "Release requires tag v0.3.0" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize(
    "changelog",
    ["## [Unreleased]\nPrepare 0.3.0.\n", "## [0.3.0] - 2026-02-30\n"],
)
def test_release_workflow_refuses_missing_or_invalid_committed_release_notes(
    release_commit: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, changelog: str
) -> None:
    repository, output = release_commit
    (repository / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    git(repository, "add", "CHANGELOG.md")
    git(repository, "commit", "-m", "test(release): invalidate fixture release notes")
    monkeypatch.setenv("GITHUB_SHA", git(repository, "rev-parse", "HEAD"))
    (repository / "CHANGELOG.md").write_text("## [0.3.0] - 2026-09-26\n", encoding="utf-8")
    result = run(repository, sys.executable, "-c", metadata_step())
    assert result.returncode != 0
    assert "CHANGELOG.md" in result.stderr
    assert not output.exists()
