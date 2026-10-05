from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

from tests.git.test_workflow import ROOT, git, repo

__all__ = ["repo"]
sys.path.insert(0, str(ROOT / "scripts"))


def test_worktree_record_survives_commit_and_preserves_the_real_index(repo: Path) -> None:
    records = importlib.import_module("dev.gate_record")
    (repo / "change.txt").write_text("new", encoding="utf-8")
    before = git(repo, "write-tree")
    tree = records.worktree(repo)
    assert git(repo, "write-tree") == before
    assert tree != before
    head = git(repo, "rev-parse", "HEAD")
    records.write_green(repo, tree, head, {"static": 3, "functional": 10, "performance": 2})
    git(repo, "add", "change.txt")
    git(repo, "commit", "-m", "feat(test): accept fixture")
    assert git(repo, "rev-parse", "HEAD^{tree}") == tree
    records.require_green(repo, tree)


@pytest.mark.parametrize(
    "counts",
    [
        {},
        {"static": 3, "functional": 1, "performance": 0},
        {"static": True, "functional": 1, "performance": 1},
    ],
)
def test_partial_gate_never_writes_a_green_record(repo: Path, counts: dict[str, object]) -> None:
    records = importlib.import_module("dev.gate_record")
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    with pytest.raises(ValueError, match="gate"):
        records.write_green(repo, tree, git(repo, "rev-parse", "HEAD"), counts)
    assert not (repo / ".cuanta/gates" / f"{tree}.json").exists()


def test_malformed_gate_record_fails_closed(repo: Path) -> None:
    records = importlib.import_module("dev.gate_record")
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    path = repo / ".cuanta/gates" / f"{tree}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"tree": tree, "status": "green"}), encoding="utf-8")
    with pytest.raises(ValueError, match="gate"):
        records.require_green(repo, tree)


@pytest.mark.parametrize("changed", [False, True])
def test_gate_records_only_the_tree_that_finished_all_phases(
    repo: Path, monkeypatch: pytest.MonkeyPatch, changed: bool
) -> None:
    gate = importlib.import_module("dev.gate")
    results = importlib.import_module("dev.results")
    records = importlib.import_module("dev.gate_record")
    monkeypatch.setattr(gate, "ROOT", repo)
    report = results.Report("proof", repo)
    report.steps.extend(
        [results.Step(name, [], 0, 0, "") for name in ("ruff", "format", "mypy")]
        + [
            results.Step("pytest-1", [], 0, 0, "", results.Tests(passed=10)),
            results.Step("performance-1", [], 0, 0, "", results.Tests(passed=2)),
        ]
    )
    tree = records.worktree(repo)
    if changed:
        (repo / "late.txt").write_text("changed during checks", encoding="utf-8")
    gate.record_gate(report, tree, git(repo, "rev-parse", "HEAD"))
    assert report.steps[-1].code == int(changed)
    path = repo / ".cuanta/gates" / f"{tree}.json"
    assert path.exists() != changed
    if not changed:
        assert records.require_green(repo, tree)["counts"] == {
            "static": 3,
            "functional": 10,
            "performance": 2,
        }
