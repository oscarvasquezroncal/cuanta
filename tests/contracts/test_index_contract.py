from __future__ import annotations

import os
import sqlite3
import stat
import sys
import time
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from cuanta.adapters.storage import sqlite_index
from cuanta.adapters.storage.sqlite_index import SqliteIndex
from cuanta.domain.code_index import INDEX_TABLES, INDEX_VERSION, IndexedFile, IndexRow, IndexTable
from cuanta.domain.errors import ExitCode
from cuanta.domain.index_rebuild import IndexBusy, IndexReadOnly, IndexRecoveryBusy
from cuanta.ports.code_index import CodeIndex

LEFTOVERS = {
    "index.db.rebuild-0123456789abcdef0123456789abcdef.bak": 1_048_576,
    "index.db.rebuild-0123456789abcdef0123456789abcdef.bak-journal": 512,
    "index.db.corrupt-fedcba9876543210fedcba9876543210.bak": 2048,
    "index.db.new-00112233445566778899aabbccddeeff": 4096,
}
BUSY = ".cuanta/index.db is open in another process, so it was not rebuilt; the index is unchanged"
HOLD = "index.db.hold"
READ_ONLY = ".cuanta/index.db is read-only, so it was not rebuilt; the index is unchanged"
RECOVERY_BUSY = (
    ".cuanta/index.db must be recreated (it is damaged or from another version) but is open in"
    " another process, so it was left as it was"
)
RECOVERY_BUSY_FIX = (
    "finish or stop the cuanta run that uses it (its index tools keep it open), then try again"
)


def _file(path: str = "src/a.py", content_hash: str = "hash-a") -> IndexedFile:
    return IndexedFile(path, content_hash, "python", 100, "inventory", "full")


def _record(identifier: str = "A", path: str = "src/a.py") -> IndexRow:
    return IndexRow(identifier, path, "hash-a", "ast", "def run", 2, 5, "src/b.py", "calls", 0.8)


def _bloated(path: Path) -> tuple[IndexRow, ...]:
    kept = (
        IndexRow(
            "note:kept", "src/a.py", "hash-a", "agent-note:hash-a", "keep me", 2, 3, "anchor:x"
        ),
        IndexRow(
            "summary:kept",
            "src/a.py",
            "hash-a",
            "economy-summary:model",
            "a paid summary",
            target="anchor:hash-a",
            relation="summary",
        ),
    )
    derived = IndexRow("finding:f", "src/a.py", "hash-a", "run-report:R#d", "from a report", 2, 2)
    history = IndexRow("history:h", "src/a.py", "hash-a", "ledger:R", "{}", relation="edit")
    with closing(SqliteIndex(path)) as index:
        index.replace_files([_file()], [])
        index.put_rows(
            "symbols",
            [
                IndexRow(f"symbol:{number}", "src/a.py", "hash-a", "ast", "x" * 1024, 1, 1)
                for number in range(2000)
            ],
        )
        index.put_rows("notes", [*kept, derived])
        index.put_rows("history", [history])
        index.replace_files([_file(content_hash="hash-b")], [])
    return kept


def _names(directory: Path) -> list[str]:
    return sorted(item.name for item in directory.iterdir() if item.name != HOLD)


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


def _incompatible(path: Path) -> bytes:
    _bloated(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE meta SET value = '999' WHERE key = 'schema_version'")
        connection.commit()
    return path.read_bytes()


def test_a_held_index_that_needs_recovery_is_refused_and_left_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / ".cuanta"
    directory.mkdir()
    path = directory / "index.db"
    before = _incompatible(path)
    attempts: list[str] = []

    def held(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        attempts.append(os.fspath(source))
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(sqlite_index, "SWAP_RETRY_S", 0.2)
    monkeypatch.setattr(os, "rename", held)
    with pytest.raises(IndexRecoveryBusy) as refused:
        SqliteIndex(path)
    assert refused.value.exit_code is ExitCode.ENVIRONMENT
    assert refused.value.message == RECOVERY_BUSY
    assert refused.value.hint == RECOVERY_BUSY_FIX
    assert len(attempts) > 1 and set(attempts) == {os.fspath(path)}
    assert path.read_bytes() == before
    assert _names(directory) == ["index.db"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows cannot rename an open database")
def test_an_open_connection_blocks_the_recovery_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "index.db"
    before = _incompatible(path)
    monkeypatch.setattr(sqlite_index, "SWAP_RETRY_S", 0.3)
    with closing(sqlite3.connect(path)) as holder:
        holder.execute("SELECT count(*) FROM files").fetchone()
        with pytest.raises(IndexRecoveryBusy, match="open in another process"):
            SqliteIndex(path)
    assert path.read_bytes() == before
    with closing(SqliteIndex(path)) as recovered:
        assert recovered.recovered


def test_rebuild_recreates_the_file_reclaims_its_space_and_keeps_agent_notes(
    tmp_path: Path,
) -> None:
    directory = tmp_path / ".cuanta"
    directory.mkdir()
    path = directory / "index.db"
    kept = _bloated(path)
    bloated = path.stat().st_size
    for name, size in LEFTOVERS.items():
        (directory / name).write_bytes(b"\0" * size)
    (directory / "ledger.db").write_bytes(b"ledger")
    (directory / "config.toml").write_text("[detect]\n", encoding="utf-8")
    with closing(SqliteIndex(path, rebuild=True)) as rebuilt:
        assert _names(directory) == ["config.toml", "index.db", "ledger.db"]
        assert path.stat().st_size * 10 < bloated
        assert rebuilt.reclaimed_bytes == bloated + sum(LEFTOVERS.values()) - path.stat().st_size
        assert rebuilt.files() == ()
        assert not rebuilt.recovered
        assert {row.id: row for row in rebuilt.rows("notes")} == {
            row.id: replace(row, stale=True) for row in kept
        }
        assert rebuilt.rows("history") == ()
        assert rebuilt.rows("symbols") == ()
    assert (directory / "ledger.db").read_bytes() == b"ledger"
    with closing(SqliteIndex(path)) as reopened:
        assert not reopened.recovered
        assert reopened.reclaimed_bytes == 0
        assert len(reopened.rows("notes")) == 2
    path.unlink()
    with closing(SqliteIndex(path, rebuild=True)) as recreated:
        assert recreated.files() == ()
        assert recreated.reclaimed_bytes == 0


def test_rebuild_of_an_unreadable_index_carries_nothing_and_keeps_no_backup(
    tmp_path: Path,
) -> None:
    path = tmp_path / "index.db"
    path.write_bytes(b"not a sqlite database\x00" * 100)
    with closing(SqliteIndex(path, rebuild=True)) as rebuilt:
        assert _names(tmp_path) == ["index.db"]
        assert rebuilt.rows("notes") == ()
        assert not rebuilt.recovered
        assert rebuilt.reclaimed_bytes == max(0, 2200 - path.stat().st_size) == 0


def test_rebuild_carries_the_notes_a_recovery_kept_only_in_its_backup(tmp_path: Path) -> None:
    directory = tmp_path / ".cuanta"
    directory.mkdir()
    path = directory / "index.db"
    kept = _bloated(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE meta SET value = '999' WHERE key = 'schema_version'")
        connection.commit()
    newer = replace(kept[0], text="kept after the recovery")
    with closing(SqliteIndex(path)) as recovered:
        assert recovered.recovered
        assert recovered.rows("notes") == ()
        recovered.put_rows("notes", [newer])
    assert len(list(directory.glob("index.db.corrupt-*.bak"))) == 1
    with closing(SqliteIndex(path, rebuild=True)) as rebuilt:
        notes = {row.id: row.text for row in rebuilt.rows("notes")}
    assert notes == {kept[0].id: "kept after the recovery", kept[1].id: kept[1].text}
    assert list(directory.glob("index.db.corrupt-*")) == []


def test_rebuild_drops_carried_rows_the_index_would_reject(tmp_path: Path) -> None:
    path = tmp_path / "index.db"
    kept = _bloated(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.executemany(
            "INSERT INTO notes VALUES "
            "(?, ?, 'hash-a', 'agent-note:x', 'n', ?, ?, '', 'note', 1, 0)",
            [("note:backwards", "src/a.py", 5, 2), ("note:outside", "../outside.py", 0, 0)],
        )
        connection.commit()
    with closing(SqliteIndex(path, rebuild=True)) as rebuilt:
        assert {row.id for row in rebuilt.rows("notes")} == {row.id for row in kept}


def test_rebuild_of_notes_it_cannot_decode_carries_nothing(tmp_path: Path) -> None:
    path = tmp_path / "index.db"
    _bloated(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "INSERT INTO notes VALUES ('note:binary', 'src/a.py', 'hash-a', 'agent-note:x', "
            "CAST(x'ff' AS TEXT), 0, 0, '', 'note', 1, 0)"
        )
        connection.commit()
    with closing(SqliteIndex(path, rebuild=True)) as rebuilt:
        assert rebuilt.rows("notes") == ()
        assert rebuilt.reclaimed_bytes > 0
    assert _names(tmp_path) == ["index.db"]


def test_a_held_index_is_refused_and_left_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / ".cuanta"
    directory.mkdir()
    path = directory / "index.db"
    _bloated(path)
    leftover = directory / "index.db.corrupt-fedcba9876543210fedcba9876543210.bak"
    leftover.write_bytes(b"an earlier recovery")
    before = path.read_bytes()
    attempts: list[str] = []

    def held(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        attempts.append(os.fspath(destination))
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(sqlite_index, "SWAP_RETRY_S", 0.2)
    monkeypatch.setattr(os, "replace", held)
    with pytest.raises(IndexBusy) as refused:
        SqliteIndex(path, rebuild=True)
    assert refused.value.exit_code is ExitCode.ENVIRONMENT
    assert refused.value.message == BUSY
    assert refused.value.hint.endswith("then run cuanta index --rebuild again")
    assert len(attempts) > 1 and set(attempts) == {os.fspath(path)}
    assert path.read_bytes() == before
    assert _names(directory) == ["index.db", leftover.name]


def test_a_locked_index_is_refused_instead_of_dropping_its_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / ".cuanta"
    directory.mkdir()
    path = directory / "index.db"
    _bloated(path)
    swapped: list[str] = []

    def recorded(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        swapped.append(os.fspath(destination))
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(sqlite_index, "CARRY_TIMEOUT_S", 0.1)
    monkeypatch.setattr(sqlite_index, "SWAP_RETRY_S", 0.1)
    monkeypatch.setattr(os, "replace", recorded)
    with closing(sqlite3.connect(path, isolation_level=None)) as holder:
        holder.execute("BEGIN EXCLUSIVE")
        with pytest.raises(IndexBusy, match="open in another process"):
            SqliteIndex(path, rebuild=True)
        holder.execute("ROLLBACK")
    assert swapped == []
    assert _names(directory) == ["index.db"]
    with closing(SqliteIndex(path)) as index:
        assert len(index.rows("notes")) == 3


def test_a_read_only_index_is_named_as_read_only_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / ".cuanta"
    directory.mkdir()
    path = directory / "index.db"
    _bloated(path)
    before = path.read_bytes()
    attempts: list[str] = []

    def refused(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        attempts.append(os.fspath(destination))
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(os, "replace", refused)
    os.chmod(path, stat.S_IREAD)
    try:
        with pytest.raises(IndexReadOnly) as caught:
            SqliteIndex(path, rebuild=True)
    finally:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    assert caught.value.exit_code is ExitCode.ENVIRONMENT
    assert caught.value.message == READ_ONLY
    assert caught.value.hint == (
        "make it writable (clear its read-only attribute), then run cuanta index --rebuild again"
    )
    assert attempts == [os.fspath(path)]
    assert path.read_bytes() == before
    assert _names(directory) == ["index.db"]


@pytest.mark.skipif(sys.platform != "win32", reason="POSIX replaces a read-only file")
def test_a_read_only_index_is_refused_without_waiting_on_windows(tmp_path: Path) -> None:
    directory = tmp_path / ".cuanta"
    directory.mkdir()
    path = directory / "index.db"
    _bloated(path)
    os.chmod(path, stat.S_IREAD)
    started = time.monotonic()
    try:
        with pytest.raises(IndexReadOnly, match="read-only"):
            SqliteIndex(path, rebuild=True)
    finally:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    assert time.monotonic() - started < sqlite_index.SWAP_RETRY_S
    assert _names(directory) == ["index.db"]


def test_a_writer_holding_the_index_delays_the_rebuild_only_briefly(tmp_path: Path) -> None:
    directory = tmp_path / ".cuanta"
    directory.mkdir()
    path = directory / "index.db"
    _bloated(path)
    with closing(sqlite3.connect(path, isolation_level=None)) as holder:
        holder.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        with pytest.raises(IndexBusy, match="open in another process"):
            SqliteIndex(path, rebuild=True)
        elapsed = time.monotonic() - started
        holder.execute("ROLLBACK")
    assert elapsed < 10
    with closing(SqliteIndex(path)) as index:
        assert len(index.rows("notes")) == 3


def test_a_brief_lock_is_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "index.db"
    _bloated(path)
    swap = os.replace
    attempts: list[str] = []

    def brief(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        attempts.append(os.fspath(destination))
        if len(attempts) < 3:
            raise PermissionError(13, "Access is denied")
        swap(source, destination)

    monkeypatch.setattr(os, "replace", brief)
    with closing(SqliteIndex(path, rebuild=True)) as rebuilt:
        assert rebuilt.files() == ()
        assert len(rebuilt.rows("notes")) == 2
    assert len(attempts) == 3
    assert _names(tmp_path) == ["index.db"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows cannot replace an open database")
def test_an_open_connection_blocks_the_rebuild_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / ".cuanta"
    directory.mkdir()
    path = directory / "index.db"
    _bloated(path)
    before = path.read_bytes()
    monkeypatch.setattr(sqlite_index, "SWAP_RETRY_S", 0.3)
    with closing(sqlite3.connect(path)) as holder:
        holder.execute("SELECT count(*) FROM files").fetchone()
        with pytest.raises(IndexBusy, match="open in another process"):
            SqliteIndex(path, rebuild=True)
    assert path.read_bytes() == before
    assert _names(directory) == ["index.db"]
    with closing(SqliteIndex(path, rebuild=True)) as rebuilt:
        assert rebuilt.files() == ()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX replaces an open database")
def test_an_open_connection_keeps_reading_the_old_file_on_posix(tmp_path: Path) -> None:
    path = tmp_path / "index.db"
    _bloated(path)
    with closing(sqlite3.connect(path)) as holder:
        holder.execute("SELECT count(*) FROM files").fetchone()
        with closing(SqliteIndex(path, rebuild=True)) as rebuilt:
            assert rebuilt.files() == ()
        assert holder.execute("SELECT count(*) FROM files").fetchone() == (1,)
    assert _names(tmp_path) == ["index.db"]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows refuses the replace itself")
def test_an_open_index_blocks_the_rebuild_on_posix_and_keeps_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / ".cuanta"
    directory.mkdir()
    path = directory / "index.db"
    _bloated(path)
    inode = path.stat().st_ino
    monkeypatch.setattr(sqlite_index, "SWAP_RETRY_S", 0.3)
    with closing(SqliteIndex(path)) as server, closing(SqliteIndex(path)) as other:
        with pytest.raises(IndexBusy, match="open in another process"):
            SqliteIndex(path, rebuild=True)
        assert path.stat().st_ino == inode
        server.set_meta({"knowledge_version": "1"})
        other.put_rows("notes", [_record("note:after")])
        assert len(server.rows("notes")) == 4
    assert (directory / HOLD).is_file()
    assert _names(directory) == ["index.db"]
    with closing(SqliteIndex(path, rebuild=True)) as rebuilt:
        assert rebuilt.files() == ()
        assert path.stat().st_ino != inode


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
