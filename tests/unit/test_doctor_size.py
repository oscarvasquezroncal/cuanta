from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

from cuanta.adapters.storage.sqlite_index import SqliteIndex
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.doctor import DoctorReport, cuanta_dir_check, result
from cuanta.application.home import next_step
from cuanta.bootstrap import Container
from cuanta.domain.code_index import IndexedFile, IndexRow
from cuanta.domain.disk_usage import CUANTA_DIR_WARN_BYTES, DiskUsage, free_bytes, rebuild_helps
from cuanta.domain.fixes import FixAction, classify
from cuanta.domain.messages import msg
from cuanta.domain.progress import Status
from cuanta.tui.i18n import Catalog
from tests.tui.fakes import DETECTION

REBUILD = "cuanta index --rebuild"
MIB = 1_048_576


def sized(root: Path, relative: str, size: int) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(size)
    return path


def sqlite_header(page_size: int, free_pages: int) -> bytes:
    header = bytearray(100)
    header[:16] = b"SQLite format 3\x00"
    header[16:18] = page_size.to_bytes(2, "big")
    header[36:40] = free_pages.to_bytes(4, "big")
    return bytes(header)


def database(root: Path, relative: str, size: int, free_pages: int = 0) -> Path:
    path = sized(root, relative, size)
    with path.open("r+b") as handle:
        handle.write(sqlite_header(4096, free_pages))
    return path


def state_bytes(root: Path) -> int:
    return sum(item.stat().st_size for item in (root / ".cuanta").iterdir() if item.is_file())


def test_the_warning_starts_past_500_mib() -> None:
    assert CUANTA_DIR_WARN_BYTES == 500 * MIB


def test_a_large_index_is_named_with_its_size_and_the_rebuild(tmp_path: Path) -> None:
    database = sized(tmp_path, ".cuanta/index.db", 501 * MIB)
    try:
        sized(tmp_path, ".cuanta/ledger.db", 4096)
        rows = cuanta_dir_check(LocalWorkspace(tmp_path))(DETECTION)
    finally:
        database.unlink()
    assert [(row.name, row.status, row.fix) for row in rows] == [(".cuanta", Status.WARN, REBUILD)]
    assert rows[0].detail == "501.0 MB, over 500 MB; largest file .cuanta/index.db (501.0 MB)"
    assert Catalog("es").message(rows[0].message) == (
        "501.0 MB, más de 500 MB; el archivo más grande es .cuanta/index.db (501.0 MB)"
    )


def test_an_old_rebuild_backup_is_named_and_reclaimed_by_the_rebuild(tmp_path: Path) -> None:
    sized(tmp_path, ".cuanta/index.db", 100)
    sized(tmp_path, ".cuanta/index.db.rebuild-ab.bak", 300)
    sized(tmp_path, ".cuanta/trials/R/x", 50)
    rows = cuanta_dir_check(LocalWorkspace(tmp_path), limit_bytes=200)(DETECTION)
    assert [(row.name, row.status, row.fix) for row in rows] == [(".cuanta", Status.WARN, REBUILD)]
    assert ".cuanta/index.db.rebuild-ab.bak" in rows[0].detail


def test_a_folder_at_the_limit_is_only_reported(tmp_path: Path) -> None:
    sized(tmp_path, ".cuanta/index.db", 100)
    sized(tmp_path, ".cuanta/trials/R/x", 100)
    rows = cuanta_dir_check(LocalWorkspace(tmp_path), limit_bytes=200)(DETECTION)
    assert [(row.status, row.fix) for row in rows] == [(Status.OK, "")]
    assert rows[0].message is not None and rows[0].message.key == "doctor.cuanta_dir.size"


def test_a_large_ledger_is_named_without_a_command(tmp_path: Path) -> None:
    database(tmp_path, ".cuanta/index.db", 100)
    sized(tmp_path, ".cuanta/ledger.db", 300)
    rows = cuanta_dir_check(LocalWorkspace(tmp_path), limit_bytes=200)(DETECTION)
    assert [(row.status, row.fix) for row in rows] == [(Status.WARN, "")]
    assert ".cuanta/ledger.db" in rows[0].detail
    assert "delete" not in rows[0].detail


def test_a_missing_folder_still_points_to_init(tmp_path: Path) -> None:
    rows = cuanta_dir_check(LocalWorkspace(tmp_path))(DETECTION)
    assert [(row.status, row.fix) for row in rows] == [(Status.WARN, "cuanta init")]
    assert rows[0].message is not None and rows[0].message.key == "doctor.absent"


def test_a_clean_index_over_the_limit_is_named_without_the_rebuild(tmp_path: Path) -> None:
    for number in range(20):
        source = f"def run_{number}() -> int:\n    return {number}\n"
        (tmp_path / f"module_{number}.py").write_text(source, encoding="utf-8")
    container = Container.for_project(tmp_path)
    service = container.index_service()
    try:
        service.update()
    finally:
        service.close()
        container.close()
    rows = cuanta_dir_check(LocalWorkspace(tmp_path), limit_bytes=state_bytes(tmp_path) // 2)(
        DETECTION
    )
    assert [(row.status, row.fix) for row in rows] == [(Status.WARN, "")]
    assert "largest file .cuanta/index.db" in rows[0].detail
    assert next_step(DoctorReport(DETECTION, tuple(rows))) is None


def test_free_pages_in_the_index_get_the_rebuild(tmp_path: Path) -> None:
    file = IndexedFile("a.py", "hash-a", "python", 1)
    with closing(SqliteIndex(tmp_path / ".cuanta" / "index.db")) as index:
        index.replace_files([file], [])
        index.put_rows(
            "symbols",
            [
                IndexRow(f"symbol:{number}", "a.py", "hash-a", "ast", "x" * 2048)
                for number in range(400)
            ],
        )
        index.replace_files([], ["a.py"])
    rows = cuanta_dir_check(LocalWorkspace(tmp_path), limit_bytes=state_bytes(tmp_path) // 2)(
        DETECTION
    )
    assert [(row.status, row.fix) for row in rows] == [(Status.WARN, REBUILD)]


@pytest.mark.parametrize(
    ("total", "reclaimable", "limit", "offered"),
    [
        (1000, 0, 500, False),
        (1000, 500, 500, True),
        (2000, 499, 1000, False),
        (4000, 1000, 500, True),
        (4000, 999, 500, False),
        (600, 100, 500, True),
        (600, 99, 500, False),
    ],
)
def test_the_rebuild_is_offered_only_when_it_frees_enough(
    total: int, reclaimable: int, limit: int, offered: bool
) -> None:
    assert rebuild_helps(DiskUsage(total, 1, ".cuanta/x", total, reclaimable), limit) is offered


@pytest.mark.parametrize(
    ("header", "size", "free"),
    [
        (sqlite_header(4096, 10), 1_000_000, 40_960),
        (sqlite_header(1, 2), 1_000_000, 131_072),
        (sqlite_header(4096, 1000), 8192, 8192),
        (b"\0" * 100, 300, 300),
        (b"SQLite format 3\x00", 16, 16),
        (b"", 0, 0),
    ],
)
def test_free_bytes_come_from_the_database_header(header: bytes, size: int, free: int) -> None:
    assert free_bytes(header, size) == free


def test_home_points_a_large_index_to_the_rebuild_and_never_to_init(tmp_path: Path) -> None:
    sized(tmp_path, ".cuanta/index.db", 300)
    large = cuanta_dir_check(LocalWorkspace(tmp_path), limit_bytes=200)(DETECTION)
    ok = result("python", Status.OK, msg("doctor.python.ok", version="3.12.9"))
    listener = result(
        "listener", Status.WARN, msg("doctor.listener.off"), "cuanta listen --background"
    )
    step = next_step(DoctorReport(DETECTION, (ok, listener, *large)))
    assert step is not None and step.name == ".cuanta" and step.fix == REBUILD
    assert classify(step.fix).action is not FixAction.INIT
    (tmp_path / ".cuanta" / "index.db").unlink()
    sized(tmp_path, ".cuanta/ledger.db", 300)
    ledger = cuanta_dir_check(LocalWorkspace(tmp_path), limit_bytes=200)(DETECTION)
    assert ledger[0].status is Status.WARN
    assert next_step(DoctorReport(DETECTION, (ok, *ledger))) is None
