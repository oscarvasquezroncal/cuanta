from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from cuanta.adapters.storage.sqlite_index import SqliteIndex
from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.domain.code_index import IndexHistory
from cuanta.domain.index_facts import history_row
from cuanta.domain.ledger import Run
from tests.fakes import FakeRunner
from tests.support import invoke


def _state(project: Path) -> dict[str, bytes]:
    state = project / ".cuanta"
    return (
        {
            str(path.relative_to(state)): path.read_bytes()
            for path in state.rglob("*")
            if path.is_file()
        }
        if state.is_dir()
        else {}
    )


def _stats(project: Path) -> dict[str, object]:
    result = invoke(["models", "stats", "--files", "--json", "--project", str(project)])
    assert result.exit_code == 0, result.stdout
    value: dict[str, object] = json.loads(result.stdout)
    return value


def _history(project: Path) -> None:
    index = SqliteIndex(project / ".cuanta" / "index.db")
    index.put_rows(
        "history",
        [
            history_row(
                IndexHistory(
                    path="src/cart.py",
                    run_id="known",
                    task_type="investigation",
                    action="read",
                    at="2026-09-27T00:00:00Z",
                    outcome="accepted",
                ),
                "hash",
            ),
            history_row(
                IndexHistory(
                    path="src/cart.py",
                    run_id="unknown",
                    task_type="investigation",
                    action="cite",
                    at="2026-09-27T00:00:00Z",
                ),
                "hash",
            ),
        ],
    )
    index.close()


def test_files_stats_missing_index_does_not_create_state_or_refresh_models(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    before = tuple(tmp_path.iterdir())
    payload = _stats(tmp_path)
    assert payload["available"] is False
    assert payload["unavailable_reason"] == "index_unavailable"
    assert tuple(tmp_path.iterdir()) == before
    assert fake_runner.calls == []


def test_files_stats_conserves_known_cost_and_keeps_unknown_visible_without_writes(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    _history(tmp_path)
    ledger = SqliteLedger(tmp_path / ".cuanta" / "ledger.db")
    for run in (
        Run(
            "known",
            "mandate",
            engine="claude",
            task_type="investigation",
            status="done",
            cost_usd=2.0,
            cost_source="reported",
            outcome="accepted",
        ),
        Run(
            "unknown",
            "mandate",
            engine="claude",
            task_type="investigation",
            status="done",
            cost_usd=None,
            cost_source="unknown",
        ),
    ):
        ledger.add_run(run)
    ledger.close()
    before = _state(tmp_path)
    payload = _stats(tmp_path)
    assert payload["available"] is True
    assert payload["known_cost_usd"] == 2.0
    assert payload["allocated_known_usd"] == 2.0
    assert payload["unallocated_known_usd"] == 0.0
    assert payload["unknown_attempts"] == 1
    rows = payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["allocated_cost_usd"] is None
    assert rows[0]["known_allocated_usd"] == 2.0
    assert rows[0]["unknown_samples"] == 1
    assert rows[0]["samples"] == 2
    assert _state(tmp_path) == before
    assert fake_runner.calls == []
    plain = invoke(["models", "stats", "--files", "--plain", "--project", str(tmp_path)])
    assert "retrospective heuristic" in plain.stdout
    assert "n/a" in plain.stdout
    assert "src/cart.py" in plain.stdout
    assert _state(tmp_path) == before


@pytest.mark.parametrize("kind", ["missing", "old", "corrupt", "wal", "columns"])
def test_files_stats_unavailable_ledger_is_preserved(
    tmp_path: Path, fake_runner: FakeRunner, kind: str
) -> None:
    _history(tmp_path)
    path = tmp_path / ".cuanta" / "ledger.db"
    if kind == "old":
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA user_version=1")
        connection.close()
    elif kind == "corrupt":
        path.write_bytes(b"broken snapshot")
    elif kind == "wal":
        ledger = SqliteLedger(path)
        ledger.close()
        path.with_name(path.name + "-wal").write_bytes(b"busy")
    elif kind == "columns":
        ledger = SqliteLedger(path)
        ledger.add_run(Run("old-columns", "mandate", status="done"))
        ledger.close()
        connection = sqlite3.connect(path)
        connection.execute("ALTER TABLE runs RENAME COLUMN task_type TO missing_task_type")
        connection.close()
    before = _state(tmp_path)
    payload = _stats(tmp_path)
    assert payload["available"] is False
    assert payload["unavailable_reason"] == "ledger_unavailable"
    assert _state(tmp_path) == before
    assert fake_runner.calls == []
