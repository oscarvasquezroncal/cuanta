from __future__ import annotations

from pathlib import Path

import pytest

from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.adapters.telemetry.config_editors import Backups, ClaudeSettingsWiring
from cuanta.application.new_files import NewFileReview
from cuanta.domain.errors import DomainFailure
from cuanta.domain.new_files import Change, original_of, side_by_side
from cuanta.tui.views.loop import parse_iterations
from cuanta.tui.views.settings import parse_exclusions, parse_port


@pytest.mark.parametrize(
    ("new", "original"),
    [
        (".claude/agents/tester.new.md", ".claude/agents/tester.md"),
        ("CLAUDE.new.md", "CLAUDE.md"),
        ("docs/config.new.toml", "docs/config.toml"),
        ("scripts/run.new", "scripts/run"),
    ],
)
def test_original_of_reverses_the_new_naming(new: str, original: str) -> None:
    assert original_of(new) == original


def test_original_of_rejects_plain_files() -> None:
    with pytest.raises(ValueError, match=r"not a \.new file"):
        original_of("docs/readme.md")


def test_side_by_side_marks_each_change() -> None:
    rows = side_by_side("a\nb\nc\n", "a\nB\nc\nd\n")
    assert [row.change for row in rows] == [Change.SAME, Change.CHANGED, Change.SAME, Change.ADDED]
    assert rows[3].left_number is None
    assert rows[3].right_number == 4
    removed = side_by_side("a\nb\n", "a\n")
    assert removed[-1].change is Change.REMOVED
    assert removed[-1].right == ""


def test_review_keeps_mine_or_uses_new(tmp_path: Path) -> None:
    workspace = LocalWorkspace(tmp_path)
    workspace.write_text("CLAUDE.md", "mine\n")
    workspace.write_text("CLAUDE.new.md", "theirs\n")
    review = NewFileReview(workspace)
    pair = review.load("CLAUDE.new.md")
    assert pair.original_path == "CLAUDE.md"
    assert pair.rows[0].change is Change.CHANGED
    assert review.keep_mine("CLAUDE.new.md") == "CLAUDE.md"
    assert workspace.read_text("CLAUDE.md") == "mine\n"
    assert not workspace.exists("CLAUDE.new.md")
    workspace.write_text("CLAUDE.new.md", "theirs\n")
    review.use_new("CLAUDE.new.md")
    assert workspace.read_text("CLAUDE.md") == "theirs\n"
    assert not workspace.exists("CLAUDE.new.md")
    with pytest.raises(DomainFailure, match="no longer exists"):
        review.load("CLAUDE.new.md")


def test_wiring_plan_names_the_file_and_backup(tmp_path: Path) -> None:
    backups = Backups(tmp_path / ".cuanta" / "backups")
    wiring = ClaudeSettingsWiring(tmp_path, backups)
    fresh = wiring.plan()
    assert fresh.engine == "claude"
    assert fresh.target == str(tmp_path / ".claude" / "settings.local.json")
    assert (fresh.exists, fresh.backup) == (False, "")
    settings = tmp_path / ".claude" / "settings.local.json"
    settings.parent.mkdir()
    settings.write_text("{}", encoding="utf-8")
    existing = wiring.plan()
    assert existing.exists
    assert existing.backup == str(
        tmp_path / ".cuanta" / "backups" / "claude-settings.local.json.bak"
    )
    wiring.enable(4318, "shop")
    assert wiring.plan().backup == existing.backup
    assert Path(existing.backup).is_file()


def test_form_parsers() -> None:
    assert parse_port("4318") == 4318
    assert parse_port("80") is None
    assert parse_port("abc") is None
    assert parse_exclusions(" dist, build ,,") == ["dist", "build"]
    assert parse_iterations("3") == 3
    assert parse_iterations("0") is None
    assert parse_iterations("99") is None
