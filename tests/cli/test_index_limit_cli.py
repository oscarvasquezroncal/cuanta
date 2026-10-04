from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from cuanta.domain import index_limit
from cuanta.domain.index_rebuild import rebuild_leftover
from tests.cli.test_engine_guarantees import mandate_args
from tests.fakes import FakeRunner
from tests.real_run import real_project_tree
from tests.support import invoke, strip_ansi


def checkout(root: Path, files: int) -> None:
    for number in range(files):
        target = root / ".odoo_ref" / "addons" / f"module_{number}.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"value = {number}\n", encoding="utf-8")
    (root / "app.py").write_text("value = 0\n", encoding="utf-8")


def stored(database: Path) -> tuple[int, int, int]:
    with closing(sqlite3.connect(database)) as connection:
        counts = tuple(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("files", "symbols", "meta")
        )
    return counts[0], counts[1], counts[2]


def test_a_rebuild_above_the_limit_leaves_the_stored_index(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout(tmp_path, 8)
    assert invoke(["index", "--json", "--project", str(tmp_path)]).exit_code == 0
    state = tmp_path / ".cuanta"
    before = stored(state / "index.db")
    assert before[0] == 9
    monkeypatch.setattr(index_limit, "INDEX_FILE_LIMIT", 5)
    result = invoke(["index", "--plain", "--rebuild", "--project", str(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "the index stopped before reading any file: 9 files" in result.output
    assert 'exclude = [".odoo_ref"]' in result.output.splitlines()
    assert stored(state / "index.db") == before
    assert [item.name for item in state.iterdir() if rebuild_leftover("index.db", item.name)] == []


def test_a_fresh_project_above_the_limit_gets_no_index(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout(tmp_path, 8)
    monkeypatch.setattr(index_limit, "INDEX_FILE_LIMIT", 5)
    for arguments in (["index", "--json"], ["index", "--rebuild", "--json"]):
        result = invoke([*arguments, "--project", str(tmp_path)])
        assert result.exit_code == 1, result.output
        assert json.loads(result.stdout)["error"]["kind"] == "IndexTooLarge"
        assert not (tmp_path / ".cuanta" / "index.db").exists()


def test_a_dry_run_above_the_limit_stops_with_the_lines_to_paste(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout(tmp_path, 8)
    monkeypatch.setattr(index_limit, "INDEX_FILE_LIMIT", 5)
    result = invoke([*mandate_args(tmp_path, "claude"), "--dry-run", "--plain"])
    assert result.exit_code == 1, result.output
    assert "the index stopped before reading any file: 9 files" in result.output
    assert "Largest folders: .odoo_ref (8)" in result.output
    lines = result.output.splitlines()
    assert "[detect]" in lines
    assert 'exclude = [".odoo_ref"]' in lines
    assert not fake_runner.stdins


def test_the_pretty_failure_shows_the_lines_to_paste(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout(tmp_path, 8)
    monkeypatch.setattr(index_limit, "INDEX_FILE_LIMIT", 5)
    result = invoke([*mandate_args(tmp_path, "claude"), "--dry-run"], pretty=True)
    assert result.exit_code == 1
    assert "[detect]" in result.output
    assert 'exclude = [".odoo_ref"]' in result.output


def test_the_pretty_failure_prints_the_lines_to_paste_whole(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (".venv312", ".venv_ci", ".odoo_ref", ".deploy-backups", "creator"):
        for number in range(3):
            target = tmp_path / name / f"module_{number}.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"value = {number}\n", encoding="utf-8")
    monkeypatch.setattr(index_limit, "INDEX_FILE_LIMIT", 2)
    result = invoke(["index", "--project", str(tmp_path)], pretty=True, env={"COLUMNS": "80"})
    assert result.exit_code == 1
    lines = [line.rstrip() for line in strip_ansi(result.output).splitlines()]
    assert "[detect]" in lines
    assert 'exclude = [".deploy-backups", ".odoo_ref", ".venv312", ".venv_ci", "creator"]' in lines
    assert any("leave out what is not your code" in line for line in lines)


def test_cuanta_index_counts_the_real_run_tree(tmp_path: Path, fake_runner: FakeRunner) -> None:
    expected = real_project_tree(tmp_path)
    result = invoke(["index", "--json", "--project", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["files"] == len(expected)


def test_cuanta_index_above_the_limit_fails_before_reading(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout(tmp_path, 8)
    monkeypatch.setattr(index_limit, "INDEX_FILE_LIMIT", 5)
    result = invoke(["index", "--plain", "--project", str(tmp_path)])
    assert result.exit_code == 1
    assert "Largest folders: .odoo_ref (8)" in result.output
    assert 'exclude = [".odoo_ref"]' in result.output.splitlines()
