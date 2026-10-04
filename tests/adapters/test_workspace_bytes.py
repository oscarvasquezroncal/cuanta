from __future__ import annotations

import importlib
import os
import sqlite3
import stat
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from cuanta.adapters.storage.sqlite_index import SqliteIndex
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.domain.code_index import IndexedFile, IndexRow
from cuanta.domain.disk_usage import DiskUsage


def _sized(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def test_read_only_files_are_replaced_and_stay_read_only(tmp_path: Path) -> None:
    target = tmp_path / "src" / "locked.ts"
    target.parent.mkdir()
    target.write_bytes(b"old\n")
    os.chmod(target, stat.S_IREAD)
    try:
        LocalWorkspace(tmp_path).write_bytes("src/locked.ts", b"new\n")
        assert target.read_bytes() == b"new\n"
        assert not os.stat(target).st_mode & stat.S_IWRITE
    finally:
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE)


def test_read_only_files_can_be_removed(tmp_path: Path) -> None:
    target = tmp_path / "locked.ts"
    target.write_bytes(b"old\n")
    os.chmod(target, stat.S_IREAD)
    LocalWorkspace(tmp_path).remove("locked.ts")
    assert not target.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_write_bytes_sets_or_clears_the_executable_bit(tmp_path: Path) -> None:
    workspace = LocalWorkspace(tmp_path)
    workspace.write_bytes("run.sh", b"echo\n", True)
    assert os.access(tmp_path / "run.sh", os.X_OK)
    workspace.write_bytes("run.sh", b"echo hi\n")
    assert os.access(tmp_path / "run.sh", os.X_OK)
    workspace.write_bytes("run.sh", b"echo hi\n", False)
    assert not os.access(tmp_path / "run.sh", os.X_OK)


def test_a_failed_replace_leaves_no_staged_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "locked.ts"
    target.write_bytes(b"old\n")
    os.chmod(target, stat.S_IREAD)

    def refused(source: object, destination: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "replace", refused)
    try:
        with pytest.raises(PermissionError):
            LocalWorkspace(tmp_path).write_bytes("locked.ts", b"new\n")
        assert sorted(path.name for path in tmp_path.iterdir()) == ["locked.ts"]
        assert target.read_bytes() == b"old\n"
        assert not os.stat(target).st_mode & stat.S_IWRITE
    finally:
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE)


def test_disk_usage_sums_nested_files_and_names_the_largest(tmp_path: Path) -> None:
    _sized(tmp_path / ".cuanta" / "index.db", 300)
    _sized(tmp_path / ".cuanta" / "trials" / "R" / "big.bin", 500)
    _sized(tmp_path / ".cuanta" / "ledger.db", 10)
    (tmp_path / ".cuanta" / "empty").mkdir()
    usage = LocalWorkspace(tmp_path).disk_usage(".cuanta")
    assert usage == DiskUsage(810, 3, ".cuanta/trials/R/big.bin", 500, 300)


def test_disk_usage_counts_only_what_a_rebuild_would_free(tmp_path: Path) -> None:
    state = tmp_path / ".cuanta"
    file = IndexedFile("a.py", "hash-a", "python", 1)
    with closing(SqliteIndex(state / "index.db")) as index:
        index.replace_files([file], [])
        index.put_rows(
            "symbols",
            [
                IndexRow(f"symbol:{number}", "a.py", "hash-a", "ast", "x" * 2048)
                for number in range(200)
            ],
        )
        index.replace_files([], ["a.py"])
    with closing(sqlite3.connect(state / "index.db")) as connection:
        pages = connection.execute("PRAGMA freelist_count").fetchone()[0]
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    assert pages > 0
    _sized(state / "index.db.rebuild-0123.bak", 700)
    _sized(state / "index.db-journal", 30)
    _sized(state / "ledger.db", 5000)
    _sized(state / "trials" / "R" / ".cuanta" / "index.db.rebuild-9.bak", 900)
    usage = LocalWorkspace(tmp_path).disk_usage(".cuanta")
    assert usage.reclaimable_bytes == pages * page_size + 700 + 30


def test_disk_usage_reports_the_exact_size_of_a_file_being_written(tmp_path: Path) -> None:
    log = tmp_path / ".cuanta" / "logs" / "listener.log"
    log.parent.mkdir(parents=True)
    _sized(tmp_path / ".cuanta" / "ledger.db", 512)
    with log.open("wb") as handle:
        handle.write(b"x" * 1024)
        handle.flush()
        os.fsync(handle.fileno())
        handle.write(b"y" * 10_485_760)
        handle.flush()
        usage = LocalWorkspace(tmp_path).disk_usage(".cuanta")
        exact = os.stat(log).st_size
    assert exact == 10_486_784
    assert (usage.largest, usage.largest_bytes) == (".cuanta/logs/listener.log", exact)
    assert usage.total_bytes == exact + 512


def test_disk_usage_of_a_missing_folder_is_empty(tmp_path: Path) -> None:
    assert LocalWorkspace(tmp_path).disk_usage(".cuanta") == DiskUsage()


def test_disk_usage_does_not_follow_links_or_junctions(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _sized(outside / "huge.bin", 1000)
    project = tmp_path / "project"
    _sized(project / ".cuanta" / "index.db", 10)
    linked = project / ".cuanta" / "linked"
    if sys.platform == "win32":
        winapi: Any = importlib.import_module("_winapi")
        winapi.CreateJunction(str(outside), str(linked))
        assert linked.is_junction()
    else:
        linked.symlink_to(outside, target_is_directory=True)
        assert linked.is_symlink()
    usage = LocalWorkspace(project).disk_usage(".cuanta")
    assert usage == DiskUsage(10, 1, ".cuanta/index.db", 10, 10)
    assert (outside / "huge.bin").stat().st_size == 1000
