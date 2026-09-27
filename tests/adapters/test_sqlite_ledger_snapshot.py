from __future__ import annotations

import importlib
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from cuanta.adapters.storage.migrations import LATEST_VERSION
from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.domain.ledger import LedgerEvent, Run, Snapshot
from cuanta.ports.ledger import EventQuery


def _ledger(path: Path) -> tuple[Run, LedgerEvent, Snapshot]:
    run = Run(id="snapshot-run", kind="mandate", status="ok", cost_usd=0.5)
    event = LedgerEvent(
        run_id=run.id,
        kind="tool_result",
        file_path="src/cart.py",
        ts="2026-09-27T00:00:00Z",
    )
    snapshot = Snapshot(run.id, "start", "src/cart.py", "source-hash")
    with closing(SqliteLedger(path)) as writer:
        writer.add_run(run)
        writer.add_events((event,))
        writer.add_snapshots((snapshot,))
    return run, event, snapshot


def _bytes(directory: Path) -> dict[str, bytes]:
    return {
        item.relative_to(directory).as_posix(): item.read_bytes()
        for item in directory.rglob("*")
        if item.is_file()
    }


def test_immutable_closed_wal_ledger_reads_without_creating_sidecars(tmp_path: Path) -> None:
    path = tmp_path / ".cuanta" / "ledger #.db"
    run, event, snapshot = _ledger(path)
    assert path.read_bytes()[18:20] == b"\x02\x02"
    assert {item.name for item in path.parent.iterdir()} == {path.name}
    before = _bytes(tmp_path)
    with closing(SqliteLedger(path, read_only=True, immutable=True)) as reader:
        assert reader.schema_version() == LATEST_VERSION
        assert reader.get_run(run.id) == run
        assert reader.runs() == (run,)
        events = reader.events(EventQuery(since="2026-09-26T00:00:00Z"))
        assert len(events) == 1
        assert replace(events[0], id=0) == event
        assert reader.snapshots(run.id, "start") == (snapshot,)
        assert _bytes(tmp_path) == before
    assert _bytes(tmp_path) == before


def test_immutable_current_version_with_missing_run_column_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    _ledger(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("ALTER TABLE runs RENAME COLUMN task_type TO missing_task_type")
    before = _bytes(tmp_path)
    with pytest.raises(ValueError, match="run columns are incompatible"):
        SqliteLedger(path, read_only=True, immutable=True)
    assert _bytes(tmp_path) == before


def _mutate(reader: SqliteLedger, method: str, run: Run, event: LedgerEvent) -> None:
    if method == "add_run":
        reader.add_run(replace(run, id="forbidden"))
    elif method == "add_events":
        reader.add_events((event,))
    else:
        reader.add_snapshots((Snapshot(run.id, "start", "forbidden.py", "forbidden"),))


@pytest.mark.parametrize("method", ["add_run", "add_events", "add_snapshots"])
def test_immutable_ledger_denies_writes_without_changing_bytes(tmp_path: Path, method: str) -> None:
    path = tmp_path / "ledger.db"
    run, event, _ = _ledger(path)
    before = _bytes(tmp_path)
    with closing(SqliteLedger(path, read_only=True, immutable=True)) as reader:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            _mutate(reader, method, run, event)
        assert reader.runs() == (run,)
    assert _bytes(tmp_path) == before


def test_immutable_ledger_requires_read_only_before_touching_missing_or_existing_path(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing" / ".cuanta" / "ledger.db"
    with pytest.raises(ValueError, match="requires read_only=True"):
        SqliteLedger(missing, immutable=True)
    assert not missing.parent.parent.exists()
    path = tmp_path / "ledger.db"
    _ledger(path)
    before = _bytes(tmp_path)
    with pytest.raises(ValueError, match="requires read_only=True"):
        SqliteLedger(path, immutable=True)
    assert _bytes(tmp_path) == before


def test_immutable_missing_ledger_does_not_create_database_or_parent(tmp_path: Path) -> None:
    path = tmp_path / "missing" / ".cuanta" / "ledger.db"
    with pytest.raises(OSError, match="unavailable"):
        SqliteLedger(path, read_only=True, immutable=True)
    assert not path.parent.parent.exists()
    assert _bytes(tmp_path) == {}


@pytest.mark.parametrize("version", [0, LATEST_VERSION - 1, LATEST_VERSION + 1])
def test_immutable_missing_old_or_future_ledger_schema_is_never_migrated(
    tmp_path: Path, version: int
) -> None:
    path = tmp_path / "ledger.db"
    with closing(sqlite3.connect(path)) as connection:
        if version:
            connection.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
            connection.execute("INSERT INTO schema_version VALUES (?)", (version,))
            connection.commit()
    before = _bytes(tmp_path)
    with pytest.raises(ValueError, match=r"read-only.*schema"):
        SqliteLedger(path, read_only=True, immutable=True)
    assert _bytes(tmp_path) == before


def test_immutable_corrupt_ledger_is_not_changed_or_recovered(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    path.write_bytes(b"original corrupt ledger\x00")
    before = _bytes(tmp_path)
    with pytest.raises(ValueError, match=r"read-only.*schema"):
        SqliteLedger(path, read_only=True, immutable=True)
    assert _bytes(tmp_path) == before


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_immutable_ledger_sidecars_are_rejected_without_altering_them(
    tmp_path: Path, suffix: str
) -> None:
    path = tmp_path / "ledger.db"
    _ledger(path)
    path.with_name(path.name + suffix).write_bytes(b"original sidecar bytes")
    before = _bytes(tmp_path)
    with pytest.raises(sqlite3.DatabaseError, match="closed snapshot"):
        SqliteLedger(path, read_only=True, immutable=True)
    assert _bytes(tmp_path) == before


def test_immutable_ledger_does_not_follow_file_symlink(tmp_path: Path) -> None:
    target = tmp_path / "outside.db"
    _ledger(target)
    path = tmp_path / "ledger.db"
    path.symlink_to(target)
    before = target.read_bytes()
    with pytest.raises(OSError, match="links or junctions"):
        SqliteLedger(path, read_only=True, immutable=True)
    assert path.is_symlink()
    assert target.read_bytes() == before
    assert {item.name for item in tmp_path.iterdir()} == {"outside.db", "ledger.db"}


def test_immutable_ledger_does_not_follow_linked_immediate_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "ledger.db"
    _ledger(target)
    parent = tmp_path / ".cuanta"
    if sys.platform == "win32":
        winapi: Any = importlib.import_module("_winapi")
        winapi.CreateJunction(str(outside), str(parent))
        assert parent.is_junction()
    else:
        parent.symlink_to(outside, target_is_directory=True)
        assert parent.is_symlink()
    before = _bytes(outside)
    with pytest.raises(OSError, match="links or junctions"):
        SqliteLedger(parent / "ledger.db", read_only=True, immutable=True)
    assert _bytes(outside) == before
    assert {item.name for item in tmp_path.iterdir()} == {"outside", ".cuanta"}


def test_default_read_only_ledger_still_reads_live_wal_changes(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    run = Run(id="live-wal", kind="mandate", status="ok")
    with closing(SqliteLedger(path)) as writer:
        writer.add_run(run)
        assert path.with_name(path.name + "-wal").is_file()
        with closing(SqliteLedger(path, read_only=True)) as reader:
            assert reader.get_run(run.id) == run
        assert writer.get_run(run.id) == run
        with pytest.raises(sqlite3.DatabaseError, match="closed snapshot"):
            SqliteLedger(path, read_only=True, immutable=True)
    with closing(SqliteLedger(path)) as writer:
        writer.add_run(replace(run, status="updated"))
        assert writer.get_run(run.id) == replace(run, status="updated")
