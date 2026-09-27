from __future__ import annotations

import math
import sqlite3
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from cuanta.domain.code_index import (
    INDEX_TABLES,
    INDEX_VERSION,
    IndexedFile,
    IndexRow,
    IndexTable,
    index_path,
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
STRUCTURAL_TABLES: tuple[IndexTable, ...] = ("symbols", "edges", "rules", "test_links")
ANCHORED_TABLES: tuple[IndexTable, ...] = ("notes", "history")


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


class SqliteIndex:
    def __init__(self, path: Path, *, rebuild: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = threading.RLock()
        self._closed = False
        self.recovered = False
        if rebuild and path.exists():
            self._preserve("rebuild")
        self._connection = self._connect()
        try:
            self._initialize()
        except sqlite3.DatabaseError as error:
            self._connection.close()
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
            self._preserve("corrupt")
            self.recovered = True
            self._connection = self._connect()
            self._initialize()

    @property
    def path(self) -> Path:
        return self._path

    def _preserve(self, reason: str) -> None:
        if not self._path.exists():
            return
        backup = self._path.with_name(f"{self._path.name}.{reason}-{uuid4().hex}.bak")
        self._path.rename(backup)
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = self._path.with_name(self._path.name + suffix)
            if sidecar.exists():
                sidecar.rename(backup.with_name(backup.name + suffix))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
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
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise

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
        where = " WHERE path = ?" if path else ""
        with self._lock:
            records = self._connection.execute(
                f"SELECT * FROM {selected}{where} ORDER BY path, line, id", parameters
            ).fetchall()
        return tuple(
            IndexRow(
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
            for row in records
        )

    def replace_files(self, files: Sequence[IndexedFile], removed: Sequence[str]) -> None:
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

    def set_meta(self, values: Mapping[str, str]) -> None:
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
