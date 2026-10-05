from __future__ import annotations

import math
import os
import sqlite3
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import closing, contextmanager, suppress
from dataclasses import replace
from functools import partial
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from cuanta.adapters.storage.readonly_snapshot import closed_snapshot_uri
from cuanta.domain.code_index import (
    INDEX_TABLES,
    INDEX_VERSION,
    IndexedFile,
    IndexRow,
    IndexTable,
    index_path,
)
from cuanta.domain.index_rebuild import (
    IndexBusy,
    IndexReadOnly,
    IndexRecoveryBusy,
    IndexRefused,
    carried,
    rebuild_backup,
    rebuild_leftover,
    sidecars,
)

FILE_COLUMNS = ("path", "content_hash", "language", "size_bytes", "provenance", "coverage")
ROW_COLUMNS = (
    "id",
    "path",
    "source_hash",
    "provenance",
    "text",
    "line",
    "end_line",
    "target",
    "relation",
    "confidence",
    "stale",
)
SWAP_RETRY_S = 2.0
SWAP_PAUSE_S = 0.1
CARRY_TIMEOUT_S = SWAP_RETRY_S
HOLD_SUFFIX = ".hold"
STRUCTURAL_TABLES: tuple[IndexTable, ...] = ("symbols", "edges", "rules", "test_links")
ANCHORED_TABLES: tuple[IndexTable, ...] = ("notes", "history")


if sys.platform == "win32":

    def _hold_file(_path: Path) -> BinaryIO | None:
        return None

    def _hold(_holder: BinaryIO, _exclusive: bool) -> None:
        return None

else:
    import fcntl

    def _hold_file(path: Path) -> BinaryIO | None:
        return path.with_name(path.name + HOLD_SUFFIX).open("ab")

    def _hold(holder: BinaryIO, exclusive: bool) -> None:
        operation = fcntl.LOCK_EX | fcntl.LOCK_NB if exclusive else fcntl.LOCK_SH
        fcntl.flock(holder.fileno(), operation)


def _table(table: IndexTable) -> str:
    if table not in INDEX_TABLES:
        raise ValueError("Unsupported index table")
    return table


def _file(item: IndexedFile) -> IndexedFile:
    if item.size_bytes < 0 or not item.content_hash or not item.provenance:
        raise ValueError("Indexed files require a hash, provenance and nonnegative size")
    return replace(item, path=index_path(item.path))


def _row(item: IndexRow) -> IndexRow:
    if not item.id or not item.source_hash or not item.provenance:
        raise ValueError("Index records require an identifier, hash and provenance")
    if item.line < 0 or item.end_line < 0 or (item.end_line and item.end_line < item.line):
        raise ValueError("Index record line ranges must be ordered and nonnegative")
    if not math.isfinite(item.confidence) or not 0.0 <= item.confidence <= 1.0:
        raise ValueError("Index record confidence must be finite and between zero and one")
    return replace(item, path=index_path(item.path))


def _record(row: sqlite3.Row) -> IndexRow:
    return IndexRow(
        str(row["id"]),
        str(row["path"]),
        str(row["source_hash"]),
        str(row["provenance"]),
        str(row["text"]),
        int(row["line"]),
        int(row["end_line"]),
        str(row["target"]),
        str(row["relation"]),
        float(row["confidence"]),
        bool(row["stale"]),
    )


def _label(path: Path) -> str:
    return f"{path.parent.name}/{path.name}"


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _kept(path: Path) -> tuple[IndexRow, ...]:
    if not path.is_file():
        return ()
    try:
        with closing(sqlite3.connect(path, timeout=CARRY_TIMEOUT_S)) as connection:
            connection.row_factory = sqlite3.Row
            columns = tuple(str(row[1]) for row in connection.execute("PRAGMA table_info(notes)"))
            records = (
                connection.execute("SELECT * FROM notes ORDER BY path, line, id").fetchall()
                if columns == ROW_COLUMNS
                else []
            )
    except sqlite3.OperationalError as error:
        if getattr(error, "sqlite_errorname", "").startswith(("SQLITE_BUSY", "SQLITE_LOCKED")):
            raise IndexBusy(_label(path)) from error
        return ()
    except sqlite3.DatabaseError:
        return ()
    kept: list[IndexRow] = []
    for record in records:
        with suppress(ValueError, TypeError):
            row = _record(record)
            if carried(row.provenance):
                kept.append(_row(row))
    return tuple(kept)


def _modified(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _carried(path: Path) -> tuple[IndexRow, ...]:
    current = _kept(path)
    try:
        backups = [item for item in path.parent.iterdir() if rebuild_backup(path.name, item.name)]
    except OSError:
        backups = []
    rows: dict[str, IndexRow] = {}
    for backup in sorted(backups, key=lambda item: (_modified(item), item.name)):
        with suppress(IndexBusy):
            rows.update((row.id, row) for row in _kept(backup))
    rows.update((row.id, row) for row in current)
    return tuple(rows.values())


def _retried(
    action: Callable[[], None],
    refusal: Callable[[], IndexRefused],
    retried: type[OSError] = PermissionError,
) -> None:
    deadline = time.monotonic() + SWAP_RETRY_S
    while True:
        try:
            action()
            return
        except retried as error:
            if time.monotonic() >= deadline:
                raise refusal() from error
            time.sleep(SWAP_PAUSE_S)


def _replace(staged: Path, path: Path) -> None:
    try:
        os.replace(staged, path)
    except PermissionError as error:
        if path.exists() and not os.access(path, os.W_OK):
            raise IndexReadOnly(_label(path)) from error
        raise


def _swap(staged: Path, path: Path) -> None:
    _retried(partial(_replace, staged, path), partial(IndexBusy, _label(path)))


def _sweep(path: Path) -> int:
    try:
        leftovers = [
            item for item in path.parent.iterdir() if rebuild_leftover(path.name, item.name)
        ]
    except OSError:
        return 0
    removed = 0
    for item in leftovers:
        size = _size(item)
        try:
            item.unlink()
        except OSError:
            continue
        removed += size
    return removed


class SqliteIndex:
    def __init__(self, path: Path, *, rebuild: bool = False, read_only: bool = False) -> None:
        if read_only and rebuild:
            raise ValueError("A read-only code index cannot be rebuilt")
        if not read_only:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._read_only = read_only
        self._lock = threading.RLock()
        self._closed = False
        self._rows_cache: dict[tuple[IndexTable, str], tuple[IndexRow, ...]] = {}
        self._rows_version = -1
        self.recovered = False
        self._replaced = 0
        self._holder = None if read_only else _hold_file(path)
        try:
            if rebuild:
                self._claim()
                self._replaced = self._recreate()
            if self._holder is not None:
                _hold(self._holder, False)
            self._open()
        except BaseException:
            self._release()
            raise

    def _claim(self) -> None:
        if self._holder is not None:
            _retried(
                partial(_hold, self._holder, True),
                partial(IndexBusy, _label(self._path)),
                BlockingIOError,
            )

    def _release(self) -> None:
        if self._holder is not None:
            self._holder.close()
            self._holder = None

    def _open(self) -> None:
        self._connection = self._connect()
        try:
            self._initialize()
        except sqlite3.DatabaseError as error:
            self._connection.close()
            if self._read_only:
                raise
            if not any(
                wording in str(error).lower()
                for wording in (
                    "file is not a database",
                    "database disk image is malformed",
                    "code index failed its integrity check",
                    "code index schema is incompatible",
                    "code index columns are incompatible",
                    "code index version is incompatible",
                )
            ):
                raise
            self._preserve()
            self.recovered = True
            self._connection = self._connect()
            self._initialize()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def reclaimed_bytes(self) -> int:
        return max(0, self._replaced - _size(self._path)) if self._replaced else 0

    def _preserve(self) -> None:
        if not self._path.exists():
            return
        backup = self._path.with_name(f"{self._path.name}.corrupt-{uuid4().hex}.bak")
        _retried(
            partial(os.rename, self._path, backup),
            partial(IndexRecoveryBusy, _label(self._path)),
        )
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = self._path.with_name(self._path.name + suffix)
            if sidecar.exists():
                sidecar.rename(backup.with_name(backup.name + suffix))

    def _recreate(self) -> int:
        kept = _carried(self._path)
        before = _size(self._path)
        staged = self._path.with_name(f"{self._path.name}.new-{uuid4().hex}")
        try:
            with closing(SqliteIndex(staged)) as fresh:
                fresh.put_rows("notes", kept)
            _swap(staged, self._path)
        except BaseException:
            for name in (staged.name, *sidecars(staged.name), staged.name + HOLD_SUFFIX):
                with suppress(OSError):
                    staged.with_name(name).unlink(missing_ok=True)
            raise
        return before + _sweep(self._path)

    def _connect(self) -> sqlite3.Connection:
        database = closed_snapshot_uri(self._path, "code index") if self._read_only else self._path
        connection = sqlite3.connect(
            database, uri=self._read_only, timeout=30, check_same_thread=False
        )
        connection.row_factory = sqlite3.Row
        if self._read_only:
            connection.execute("PRAGMA query_only=ON")
        return connection

    def _initialize(self) -> None:
        checked = self._connection.execute("PRAGMA quick_check").fetchone()
        if checked is None or checked[0] != "ok":
            raise sqlite3.DatabaseError("The code index failed its integrity check")
        tables = {
            str(row[0])
            for row in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if tables:
            required = {"files", "meta", *INDEX_TABLES}
            if tables != required:
                raise sqlite3.DatabaseError("The code index schema is incompatible")
            for table, expected in (
                ("files", FILE_COLUMNS),
                ("meta", ("key", "value", "source_hash", "provenance")),
                *((table, ROW_COLUMNS) for table in INDEX_TABLES),
            ):
                columns = tuple(
                    str(row[1]) for row in self._connection.execute(f"PRAGMA table_info({table})")
                )
                if columns != expected:
                    raise sqlite3.DatabaseError("The code index columns are incompatible")
            version = self._connection.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
            if version is None or str(version[0]) != str(INDEX_VERSION):
                raise sqlite3.DatabaseError("The code index version is incompatible")
            return
        if self._read_only:
            raise sqlite3.DatabaseError("The code index schema is incompatible")
        with self._transaction() as connection:
            connection.execute(
                "CREATE TABLE files (path TEXT PRIMARY KEY, content_hash TEXT NOT NULL, "
                "language TEXT NOT NULL, size_bytes INTEGER NOT NULL, provenance TEXT NOT NULL, "
                "coverage TEXT NOT NULL)"
            )
            for table in INDEX_TABLES:
                connection.execute(
                    f"CREATE TABLE {table} (id TEXT PRIMARY KEY, path TEXT NOT NULL, "
                    "source_hash TEXT NOT NULL, provenance TEXT NOT NULL, text TEXT NOT NULL, "
                    "line INTEGER NOT NULL, end_line INTEGER NOT NULL, target TEXT NOT NULL, "
                    "relation TEXT NOT NULL, confidence REAL NOT NULL, stale INTEGER NOT NULL)"
                )
                connection.execute(f"CREATE INDEX {table}_path ON {table} (path)")
            connection.execute(
                "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
                "source_hash TEXT NOT NULL, provenance TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO meta VALUES ('schema_version', ?, ?, 'index-meta')",
                (str(INDEX_VERSION), sha256(str(INDEX_VERSION).encode()).hexdigest()),
            )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self._require_write()
        with self._lock:
            self._rows_cache.clear()
            if self._connection.in_transaction:
                try:
                    yield self._connection
                finally:
                    self._rows_cache.clear()
                return
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
            finally:
                self._rows_cache.clear()

    @contextmanager
    def batch(self) -> Iterator[None]:
        with self._transaction():
            yield

    def counts(self) -> tuple[tuple[IndexTable, int], ...]:
        with self._lock:
            return tuple(
                (
                    table,
                    int(self._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]),
                )
                for table in INDEX_TABLES
            )

    def _require_write(self) -> None:
        if self._read_only:
            raise sqlite3.OperationalError("The code index is read-only")

    def files(self) -> tuple[IndexedFile, ...]:
        with self._lock:
            records = self._connection.execute("SELECT * FROM files ORDER BY path").fetchall()
        return tuple(
            IndexedFile(
                str(row["path"]),
                str(row["content_hash"]),
                str(row["language"]),
                int(row["size_bytes"]),
                str(row["provenance"]),
                str(row["coverage"]),
            )
            for row in records
        )

    def rows(self, table: IndexTable, path: str = "") -> tuple[IndexRow, ...]:
        selected = _table(table)
        parameters: tuple[str, ...] = (index_path(path),) if path else ()
        normalized = parameters[0] if parameters else ""
        where = " WHERE path = ?" if path else ""
        with self._lock:
            version = int(self._connection.execute("PRAGMA data_version").fetchone()[0])
            if version != self._rows_version:
                self._rows_cache.clear()
                self._rows_version = version
            key = table, normalized
            if key not in self._rows_cache:
                whole = self._rows_cache.get((table, "")) if normalized else None
                if whole is not None:
                    self._rows_cache[key] = tuple(row for row in whole if row.path == normalized)
                else:
                    records = self._connection.execute(
                        f"SELECT * FROM {selected}{where} ORDER BY path, line, id", parameters
                    ).fetchall()
                    self._rows_cache[key] = tuple(_record(row) for row in records)
            return self._rows_cache[key]

    def replace_files(self, files: Sequence[IndexedFile], removed: Sequence[str]) -> None:
        self._require_write()
        prepared = tuple(_file(item) for item in files)
        deleted = tuple(index_path(path) for path in removed)
        paths = [item.path for item in prepared]
        if len(set(paths)) != len(paths) or set(paths).intersection(deleted):
            raise ValueError(
                "Index file updates must have distinct paths and not overlap deletions"
            )
        with self._transaction() as connection:
            changed: set[str] = set(deleted)
            for item in prepared:
                previous = connection.execute(
                    "SELECT content_hash FROM files WHERE path = ?", (item.path,)
                ).fetchone()
                if previous is not None and str(previous[0]) != item.content_hash:
                    changed.add(item.path)
                connection.execute(
                    "INSERT INTO files VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET "
                    "content_hash = excluded.content_hash, language = excluded.language, "
                    "size_bytes = excluded.size_bytes, provenance = excluded.provenance, "
                    "coverage = excluded.coverage",
                    (
                        item.path,
                        item.content_hash,
                        item.language,
                        item.size_bytes,
                        item.provenance,
                        item.coverage,
                    ),
                )
            for path in deleted:
                connection.execute("DELETE FROM files WHERE path = ?", (path,))
                connection.execute("DELETE FROM edges WHERE target = ?", (path,))
                connection.execute("DELETE FROM test_links WHERE target = ?", (path,))
            for path in changed:
                for table in STRUCTURAL_TABLES:
                    connection.execute(f"DELETE FROM {table} WHERE path = ?", (path,))
                for table in ANCHORED_TABLES:
                    connection.execute(f"UPDATE {table} SET stale = 1 WHERE path = ?", (path,))

    def put_rows(self, table: IndexTable, rows: Sequence[IndexRow]) -> None:
        self._require_write()
        selected = _table(table)
        prepared = tuple(_row(item) for item in rows)
        with self._transaction() as connection:
            for item in prepared:
                source = connection.execute(
                    "SELECT content_hash FROM files WHERE path = ?", (item.path,)
                ).fetchone()
                stale = item.stale or source is None or str(source[0]) != item.source_hash
                connection.execute(
                    f"INSERT OR REPLACE INTO {selected} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        item.id,
                        item.path,
                        item.source_hash,
                        item.provenance,
                        item.text,
                        item.line,
                        item.end_line,
                        item.target,
                        item.relation,
                        item.confidence,
                        int(stale),
                    ),
                )

    def meta(self) -> Mapping[str, str]:
        with self._lock:
            return {
                str(row[0]): str(row[1])
                for row in self._connection.execute("SELECT key, value FROM meta ORDER BY key")
            }

    def replace_rows(self, table: IndexTable, path: str, rows: Sequence[IndexRow]) -> None:
        self._require_write()
        selected = _table(table)
        normalized = index_path(path)
        prepared = tuple(_row(item) for item in rows)
        if any(item.path != normalized for item in prepared):
            raise ValueError("Replacement records must belong to their source path")
        with self._transaction() as connection:
            connection.execute(f"DELETE FROM {selected} WHERE path = ?", (normalized,))
            source = connection.execute(
                "SELECT content_hash FROM files WHERE path = ?", (normalized,)
            ).fetchone()
            for item in prepared:
                stale = item.stale or source is None or str(source[0]) != item.source_hash
                connection.execute(
                    f"INSERT INTO {selected} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        item.id,
                        item.path,
                        item.source_hash,
                        item.provenance,
                        item.text,
                        item.line,
                        item.end_line,
                        item.target,
                        item.relation,
                        item.confidence,
                        int(stale),
                    ),
                )

    def set_meta(self, values: Mapping[str, str]) -> None:
        self._require_write()
        if "schema_version" in values and values["schema_version"] != str(INDEX_VERSION):
            raise ValueError("The index schema version is managed by its storage adapter")
        with self._transaction() as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO meta VALUES (?, ?, ?, 'index-meta')",
                (
                    (key, value, sha256(value.encode()).hexdigest())
                    for key, value in sorted(values.items())
                ),
            )

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True
                self._release()
