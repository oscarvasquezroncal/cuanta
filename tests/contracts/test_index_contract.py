from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from cuanta.adapters.storage.sqlite_index import SqliteIndex
from cuanta.domain.code_index import INDEX_TABLES, INDEX_VERSION, IndexedFile, IndexRow, IndexTable
from cuanta.ports.code_index import CodeIndex


def _file(path: str = "src/a.py", content_hash: str = "hash-a") -> IndexedFile:
    return IndexedFile(path, content_hash, "python", 100, "inventory", "full")


def _record(identifier: str = "A", path: str = "src/a.py") -> IndexRow:
    return IndexRow(identifier, path, "hash-a", "ast", "def run", 2, 5, "src/b.py", "calls", 0.8)


def test_all_index_records_roundtrip_and_order_deterministically(tmp_path: Path) -> None:
    with closing(SqliteIndex(tmp_path / "index.db")) as index:
        port: CodeIndex = index
        port.replace_files([_file("src/b.py"), _file()], [])
        for table in INDEX_TABLES:
            port.put_rows(table, [_record("B", "src/b.py"), _record("A")])
            assert port.rows(table) == (_record("A"), _record("B", "src/b.py"))
            assert port.rows(table, "src\\a.py") == (_record("A"),)
        assert port.files() == (_file(), _file("src/b.py"))
        port.set_meta({"updated_at": "2026-09-27T00:00:00Z", "hash": "inventory-hash"})
        assert port.meta()["updated_at"] == "2026-09-27T00:00:00Z"
        assert port.meta()["schema_version"] == str(INDEX_VERSION)
    with closing(SqliteIndex(tmp_path / "index.db")) as reopened:
        assert not reopened.recovered
        assert len(reopened.files()) == 2
        assert reopened.rows("notes")[0].source_hash == "hash-a"
        assert reopened.rows("notes")[0].provenance == "ast"


def test_changed_hash_invalidates_structure_and_retains_stale_anchors(tmp_path: Path) -> None:
    with closing(SqliteIndex(tmp_path / "index.db")) as index:
        index.replace_files([_file(), _file("src/b.py")], [])
        for table in INDEX_TABLES:
            index.put_rows(table, [_record("A"), _record("B", "src/b.py")])
        index.replace_files([_file(content_hash="hash-new")], [])
        for table in ("symbols", "edges", "rules", "test_links"):
            assert index.rows(table) == (_record("B", "src/b.py"),)
        for table in ("notes", "history"):
            assert index.rows(table) == (
                replace(_record("A"), stale=True),
                _record("B", "src/b.py"),
            )


def test_structural_replacement_is_atomic_and_scoped(tmp_path: Path) -> None:
    with closing(SqliteIndex(tmp_path / "index.db")) as index:
        index.replace_files([_file(), _file("src/b.py")], [])
        index.put_rows("symbols", [_record("A"), _record("B", "src/b.py")])
        with pytest.raises(ValueError, match="source path"):
            index.replace_rows("symbols", "src/a.py", [_record("wrong", "src/b.py")])
        assert index.rows("symbols", "src/a.py") == (_record("A"),)
        with pytest.raises(sqlite3.IntegrityError):
            index.replace_rows("symbols", "src/a.py", [_record("C"), _record("C")])
        assert index.rows("symbols", "src/a.py") == (_record("A"),)
        index.replace_rows("symbols", "src\\a.py", [_record("C")])
        assert index.rows("symbols") == (_record("C"), _record("B", "src/b.py"))


def test_replacement_cannot_make_unproven_source_rows_fresh(tmp_path: Path) -> None:
    with closing(SqliteIndex(tmp_path / "index.db")) as index:
        index.replace_files([_file()], [])
        mismatch = replace(_record(), source_hash="previous")
        index.replace_rows("symbols", "src/a.py", [mismatch])
        assert index.rows("symbols") == (replace(mismatch, stale=True),)
        missing = _record("missing", "src/missing.py")
        index.replace_rows("symbols", "src/missing.py", [missing])
        assert index.rows("symbols", "src/missing.py") == (replace(missing, stale=True),)


def test_unchanged_hash_preserves_structure_and_fresh_anchors(tmp_path: Path) -> None:
    with closing(SqliteIndex(tmp_path / "index.db")) as index:
        index.replace_files([_file()], [])
        for table in INDEX_TABLES:
            index.put_rows(table, [_record()])
        index.replace_files([replace(_file(), coverage="partial")], [])
        for table in INDEX_TABLES:
            assert index.rows(table) == (_record(),)
        assert index.files()[0].coverage == "partial"


def test_deleted_file_removes_inbound_links_but_keeps_stale_history(tmp_path: Path) -> None:
    with closing(SqliteIndex(tmp_path / "index.db")) as index:
        index.replace_files([_file(), _file("src/b.py")], [])
        for table in INDEX_TABLES:
            index.put_rows(table, [_record("A"), _record("B", "src/b.py")])
        index.replace_files([], ["src\\b.py"])
        assert index.files() == (_file(),)
        assert index.rows("edges") == ()
        assert index.rows("test_links") == ()
        assert index.rows("symbols") == (_record("A"),)
        assert index.rows("history", "src/b.py") == (replace(_record("B", "src/b.py"), stale=True),)


@pytest.mark.parametrize("path", ["../outside.py", "src/../../outside.py", "/root.py", "C:\\a.py"])
def test_file_batch_rejects_escapes_without_partial_writes(tmp_path: Path, path: str) -> None:
    with closing(SqliteIndex(tmp_path / "index.db")) as index:
        index.replace_files([_file()], [])
        with pytest.raises(ValueError, match="Index paths"):
            index.replace_files([_file("src/new.py"), _file(path)], [])
        assert index.files() == (_file(),)
        with pytest.raises(ValueError, match="Index paths"):
            index.replace_files([_file("src/new.py")], [path])
        assert index.files() == (_file(),)


def test_invalid_record_batch_does_not_write_earlier_valid_records(tmp_path: Path) -> None:
    with closing(SqliteIndex(tmp_path / "index.db")) as index:
        index.replace_files([_file()], [])
        with pytest.raises(ValueError, match="Index paths"):
            index.put_rows("notes", [_record("valid"), _record("escape", "../outside.py")])
        assert index.rows("notes") == ()
        with pytest.raises(ValueError, match="Unsupported index table"):
            index.rows(cast(IndexTable, "files; DROP TABLE files"))
        with pytest.raises(ValueError, match="Unsupported index table"):
            index.put_rows(cast(IndexTable, "unknown"), [_record()])


def test_failed_transaction_rolls_back_file_update_and_invalidations(tmp_path: Path) -> None:
    path = tmp_path / "index.db"
    with closing(SqliteIndex(path)) as index:
        index.replace_files([_file()], [])
        index.put_rows("notes", [_record()])
        with closing(sqlite3.connect(path)) as connection:
            connection.execute(
                "CREATE TRIGGER forbid_delete BEFORE DELETE ON files BEGIN "
                "SELECT RAISE(ABORT, 'deletion refused'); END"
            )
            connection.commit()
        with pytest.raises(sqlite3.IntegrityError, match="deletion refused"):
            index.replace_files([_file("src/new.py")], ["src/a.py"])
        assert index.files() == (_file(),)
        assert index.rows("notes") == (_record(),)


def test_unknown_or_mismatched_hash_is_stale_until_revalidated(tmp_path: Path) -> None:
    with closing(SqliteIndex(tmp_path / "index.db")) as index:
        index.put_rows("notes", [_record()])
        assert index.rows("notes") == (replace(_record(), stale=True),)
        index.replace_files([_file()], [])
        index.put_rows("notes", [_record()])
        assert index.rows("notes") == (_record(),)
        index.put_rows("notes", [replace(_record(), source_hash="old-hash")])
        assert index.rows("notes")[0].stale


def test_corruption_recovers_index_and_preserves_original_and_ledger(tmp_path: Path) -> None:
    directory = tmp_path / ".cuanta"
    directory.mkdir()
    path = directory / "index.db"
    corrupt = b"not a sqlite database\x00original"
    path.write_bytes(corrupt)
    ledger = directory / "ledger.db"
    ledger.write_bytes(b"private ledger bytes")
    with closing(SqliteIndex(path)) as index:
        assert index.recovered
        assert index.files() == ()
        index.replace_files([_file()], [])
    backups = list(directory.glob("index.db.corrupt-*.bak"))
    assert len(backups) == 1 and backups[0].read_bytes() == corrupt
    assert ledger.read_bytes() == b"private ledger bytes"


@pytest.mark.parametrize("incompatibility", ["version", "missing_table", "columns"])
def test_incompatible_schema_is_preserved_and_recovered(
    tmp_path: Path, incompatibility: str
) -> None:
    path = tmp_path / "index.db"
    with closing(SqliteIndex(path)) as index:
        index.replace_files([_file()], [])
    with closing(sqlite3.connect(path)) as connection:
        if incompatibility == "version":
            connection.execute("UPDATE meta SET value = '999' WHERE key = 'schema_version'")
        elif incompatibility == "missing_table":
            connection.execute("DROP TABLE notes")
        else:
            connection.execute("ALTER TABLE files ADD COLUMN extra TEXT")
        connection.commit()
    original = path.read_bytes()
    with closing(SqliteIndex(path)) as recovered:
        assert recovered.recovered
        assert recovered.files() == ()
    backups = list(tmp_path.glob("index.db.corrupt-*.bak"))
    assert len(backups) == 1 and backups[0].read_bytes() == original


def test_rebuild_preserves_old_index_and_does_not_touch_ledger(tmp_path: Path) -> None:
    path = tmp_path / "index.db"
    ledger = tmp_path / "ledger.db"
    ledger.write_bytes(b"ledger")
    with closing(SqliteIndex(path)) as index:
        index.replace_files([_file()], [])
    original = path.read_bytes()
    with closing(SqliteIndex(path, rebuild=True)) as rebuilt:
        assert rebuilt.files() == ()
        assert not rebuilt.recovered
    backups = list(tmp_path.glob("index.db.rebuild-*.bak"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert ledger.read_bytes() == b"ledger"
    path.unlink()
    with closing(SqliteIndex(path)) as recreated:
        assert recreated.files() == ()


def test_file_normalization_duplicate_paths_and_schema_version_guard(tmp_path: Path) -> None:
    with closing(SqliteIndex(tmp_path / "index.db")) as index:
        index.replace_files([_file("src\\a.py")], [])
        assert index.files() == (_file(),)
        with pytest.raises(ValueError, match="distinct paths"):
            index.replace_files([_file(), _file("src\\a.py")], [])
        with pytest.raises(ValueError, match="distinct paths"):
            index.replace_files([_file()], ["src/a.py"])
        with pytest.raises(ValueError, match="schema version"):
            index.set_meta({"schema_version": "99", "unused": "no"})
        assert "unused" not in index.meta()
        index.close()
        index.close()


@pytest.mark.parametrize(
    "invalid",
    [
        replace(_record(), source_hash=""),
        replace(_record(), line=-1),
        replace(_record(), end_line=1),
        replace(_record(), confidence=float("nan")),
        replace(_record(), confidence=2.0),
    ],
)
def test_invalid_record_anchors_are_rejected(tmp_path: Path, invalid: IndexRow) -> None:
    with closing(SqliteIndex(tmp_path / "index.db")) as index:
        with pytest.raises(ValueError, match="Index record"):
            index.put_rows("notes", [invalid])
        assert index.rows("notes") == ()
