from __future__ import annotations

import importlib
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from cuanta.adapters.storage.sqlite_index import SqliteIndex
from cuanta.domain.code_index import INDEX_VERSION, IndexedFile, IndexRow


def _index(path: Path) -> tuple[IndexedFile, IndexRow]:
    source = IndexedFile("src/a.py", "hash-a", "python", 20)
    history = IndexRow("history-a", "src/a.py", "hash-a", "accepted-run", "feature")
    with closing(SqliteIndex(path)) as index:
        index.replace_files((source,), ())
        index.put_rows("history", (history,))
        index.set_meta({"history_status": "available"})
    return source, history


def _bytes(directory: Path) -> dict[str, bytes]:
    return {
        item.relative_to(directory).as_posix(): item.read_bytes()
        for item in directory.rglob("*")
        if item.is_file()
    }


def _mutate(index: SqliteIndex, method: str, source: IndexedFile, history: IndexRow) -> None:
    if method == "replace_files":
        index.replace_files((), (source.path,))
    elif method == "put_rows":
        index.put_rows("notes", (history,))
    elif method == "replace_rows":
        index.replace_rows("history", source.path, ())
    else:
        index.set_meta({"history_status": "changed"})


def test_read_only_existing_index_reads_files_history_meta_without_writes(tmp_path: Path) -> None:
    path = tmp_path / ".cuanta" / "index #.db"
    source, history = _index(path)
    ledger = path.parent / "ledger.db"
    ledger.write_bytes(b"private ledger bytes")
    before = _bytes(tmp_path)
    with closing(SqliteIndex(path, read_only=True)) as index:
        assert index.path == path
        assert not index.recovered
        assert index.files() == (source,)
        assert index.rows("history") == (history,)
        assert index.rows("history", "src\\a.py") == (history,)
        assert index.meta() == {
            "history_status": "available",
            "schema_version": str(INDEX_VERSION),
        }
        index.close()
        index.close()
    assert _bytes(tmp_path) == before


@pytest.mark.parametrize("method", ["replace_files", "put_rows", "replace_rows", "set_meta"])
def test_read_only_index_rejects_every_mutation_and_preserves_all_bytes(
    tmp_path: Path, method: str
) -> None:
    path = tmp_path / "index.db"
    source, history = _index(path)
    before = _bytes(tmp_path)
    with closing(SqliteIndex(path, read_only=True)) as index:
        with pytest.raises(sqlite3.OperationalError, match="read-only"):
            _mutate(index, method, source, history)
        assert index.files() == (source,)
        assert index.rows("history") == (history,)
    assert _bytes(tmp_path) == before


def test_read_only_index_sqlite_connection_itself_rejects_writes(tmp_path: Path) -> None:
    path = tmp_path / "index.db"
    _index(path)
    before = _bytes(tmp_path)
    with (
        closing(SqliteIndex(path, read_only=True)) as index,
        pytest.raises(sqlite3.OperationalError, match="readonly"),
    ):
        index._connection.execute("DELETE FROM history")
    assert _bytes(tmp_path) == before


def test_read_only_missing_parent_and_index_are_not_created(tmp_path: Path) -> None:
    parent = tmp_path / "missing" / ".cuanta"
    with pytest.raises(OSError, match="unavailable"):
        SqliteIndex(parent / "index.db", read_only=True)
    assert not (tmp_path / "missing").exists()
    assert _bytes(tmp_path) == {}


def test_read_only_rebuild_is_rejected_before_creating_any_directory(tmp_path: Path) -> None:
    parent = tmp_path / "missing" / ".cuanta"
    with pytest.raises(ValueError, match="cannot be rebuilt"):
        SqliteIndex(parent / "index.db", rebuild=True, read_only=True)
    assert not parent.exists()
    path = tmp_path / "index.db"
    _index(path)
    before = _bytes(tmp_path)
    with pytest.raises(ValueError, match="cannot be rebuilt"):
        SqliteIndex(path, rebuild=True, read_only=True)
    assert _bytes(tmp_path) == before


@pytest.mark.parametrize(
    "incompatibility", ["corrupt", "empty", "version", "missing_table", "columns"]
)
def test_read_only_corrupt_or_incompatible_index_is_not_repaired(
    tmp_path: Path, incompatibility: str
) -> None:
    path = tmp_path / ".cuanta" / "index.db"
    _index(path)
    if incompatibility == "corrupt":
        path.write_bytes(b"original corrupt index\x00")
    elif incompatibility == "empty":
        path.write_bytes(b"")
    else:
        with closing(sqlite3.connect(path)) as connection:
            if incompatibility == "version":
                connection.execute("UPDATE meta SET value = '999' WHERE key = 'schema_version'")
            elif incompatibility == "missing_table":
                connection.execute("DROP TABLE notes")
            else:
                connection.execute("ALTER TABLE files ADD COLUMN extra TEXT")
            connection.commit()
    before = _bytes(tmp_path)
    with pytest.raises(sqlite3.DatabaseError):
        SqliteIndex(path, read_only=True)
    assert _bytes(tmp_path) == before


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_read_only_index_with_sidecar_is_unavailable_without_altering_it(
    tmp_path: Path, suffix: str
) -> None:
    path = tmp_path / "index.db"
    _index(path)
    path.with_name(path.name + suffix).write_bytes(b"uncheckpointed bytes")
    before = _bytes(tmp_path)
    with pytest.raises(sqlite3.DatabaseError, match="closed snapshot"):
        SqliteIndex(path, read_only=True)
    assert _bytes(tmp_path) == before


def test_read_only_index_does_not_follow_file_symlink(tmp_path: Path) -> None:
    target = tmp_path / "outside.db"
    _index(target)
    path = tmp_path / "index.db"
    path.symlink_to(target)
    before = target.read_bytes()
    with pytest.raises(OSError, match="links or junctions"):
        SqliteIndex(path, read_only=True)
    assert path.is_symlink()
    assert target.read_bytes() == before
    assert {item.name for item in tmp_path.iterdir()} == {"outside.db", "index.db"}


def test_read_only_index_does_not_follow_linked_immediate_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "index.db"
    _index(target)
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
        SqliteIndex(parent / "index.db", read_only=True)
    assert _bytes(outside) == before
    assert {item.name for item in tmp_path.iterdir()} == {"outside", ".cuanta"}
