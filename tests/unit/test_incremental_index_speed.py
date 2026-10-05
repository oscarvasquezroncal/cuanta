from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from cuanta.adapters.graph.index_ast import AstIndexExtractor
from cuanta.adapters.storage.sqlite_index import SqliteIndex
from cuanta.adapters.system.index_inventory import LocalIndexInventory
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.change_plan import IndexChangePlan
from cuanta.application.code_index import IndexService
from cuanta.application.context_pack import IndexContextPack
from cuanta.application.index_read import IndexRead
from cuanta.domain.code_index import INDEX_TABLES, IndexRow, IndexTable
from cuanta.domain.mandate import MandateRequest


def test_warm_inventory_does_not_read_unchanged_files(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("value = 1\n", encoding="utf-8")
    inventory = LocalIndexInventory(tmp_path)
    before = inventory.candidates()
    with patch.object(inventory, "_bytes", wraps=inventory._bytes) as read:
        assert inventory.candidates() == before
        assert read.call_count == 0
        source.write_text("value = 2\n", encoding="utf-8")
        assert inventory.candidates() != before
        assert read.call_count == 1


def test_refresh_is_one_transaction_and_parses_only_changes(tmp_path: Path) -> None:
    for name in ("a.py", "b.py"):
        (tmp_path / name).write_text("value = 1\n", encoding="utf-8")
    index = SqliteIndex(tmp_path / ".cuanta/index.db")
    extractor = AstIndexExtractor()
    service = IndexService(index, LocalIndexInventory(tmp_path), lambda: "now", extractor=extractor)
    statements: list[str] = []
    index._connection.set_trace_callback(statements.append)
    try:
        service.update()
        assert sum(line == "BEGIN IMMEDIATE" for line in statements) == 1
        with patch.object(extractor, "extract", wraps=extractor.extract) as parse:
            service.update()
            assert parse.call_count == 0
            (tmp_path / "b.py").write_text("value = 2\n", encoding="utf-8")
            service.update()
            assert parse.call_count == 1
            assert parse.call_args.args[0].path == "b.py"
    finally:
        service.close()


def test_a_reopened_inventory_reuses_only_matching_file_stamps(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("value = 1\n", encoding="utf-8")
    before = LocalIndexInventory(tmp_path).candidates()
    reopened = LocalIndexInventory(tmp_path)
    with patch.object(reopened, "_bytes", wraps=reopened._bytes) as read:
        assert reopened.candidates() == before
        assert read.call_count == 0
        source.write_text("value = 2\n", encoding="utf-8")
        assert reopened.candidates() != before
        assert read.call_count == 1


def test_snapshot_hashes_read_only_changed_files(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("value = 1\n", encoding="utf-8")
    workspace = LocalWorkspace(tmp_path)
    first = workspace.sha256("main.py")
    with patch.object(Path, "open", autospec=True, wraps=Path.open) as read:
        assert workspace.sha256("main.py") == first
        assert read.call_count == 0
    source.write_text("value = 2\n", encoding="utf-8")
    assert workspace.sha256("main.py") != first


def test_cached_batched_index_and_plan_match_the_uncached_path(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "from helpers import title\ndef render():\n    return title()\n", encoding="utf-8"
    )
    (tmp_path / "helpers.py").write_text("def title():\n    return 'Hi'\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_main.py").write_text(
        "from main import render\ndef test_render():\n    assert render() == 'Hi'\n",
        encoding="utf-8",
    )
    buffered = IndexService(
        SqliteIndex(tmp_path / ".cuanta/index.db"),
        LocalIndexInventory(tmp_path),
        lambda: "now",
        extractor=AstIndexExtractor(),
    )
    uncached = IndexService(
        SqliteIndex(tmp_path / ".cuanta/reference.db"),
        LocalIndexInventory(tmp_path),
        lambda: "now",
        extractor=AstIndexExtractor(),
    )
    request = MandateRequest(
        "bug",
        "Fix render in main.py",
        "wrong title",
        tests="tests/test_main.py",
        out_of_scope="docs",
    )

    try:
        uncached._update()
        buffered.update()
        buffered.update()
        assert buffered.index.files() == uncached.index.files()
        for table in INDEX_TABLES:
            assert buffered.index.rows(table) == uncached.index.rows(table)
        assert IndexChangePlan(buffered.index, lambda: "now").compile(request) == IndexChangePlan(
            uncached.index, lambda: "now"
        ).compile(request)
        assert isinstance(uncached.index, SqliteIndex)
        read_rows = uncached.index.rows

        def reference_rows(table: IndexTable, path: str = "") -> tuple[IndexRow, ...]:
            assert isinstance(uncached.index, SqliteIndex)
            uncached.index._rows_cache.clear()
            return read_rows(table, path)

        with patch.object(uncached.index, "rows", side_effect=reference_rows):
            reference = IndexContextPack(IndexRead(uncached, lambda: "now")).compile(request)
        assert IndexContextPack(IndexRead(buffered, lambda: "now")).compile(request) == reference
    finally:
        buffered.close()
        uncached.close()


def test_repeated_row_reads_query_once_and_observe_local_and_external_writes(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".cuanta/index.db"
    index = SqliteIndex(path)
    writer = SqliteIndex(path)
    statements: list[str] = []
    index._connection.set_trace_callback(statements.append)
    try:
        first = index.rows("notes")
        assert index.rows("notes") == first
        assert sum(line.startswith("SELECT * FROM notes") for line in statements) == 1
        index.put_rows("notes", (IndexRow("N1", "main.py", "hash", "note", text="local"),))
        assert tuple(row.text for row in index.rows("notes")) == ("local",)
        writer.put_rows("notes", (IndexRow("N2", "main.py", "hash", "note", text="external"),))
        assert {row.text for row in index.rows("notes", "main.py")} == {"local", "external"}
        assert {row.text for row in index.rows("notes")} == {"local", "external"}
    finally:
        writer.close()
        index.close()


def test_a_failed_batch_rolls_back_files_and_cached_rows(tmp_path: Path) -> None:
    index = SqliteIndex(tmp_path / ".cuanta/index.db")
    try:
        before = index.rows("notes")

        def abort() -> None:
            with index.batch():
                index.put_rows(
                    "notes", (IndexRow("N1", "main.py", "hash", "note", text="discard"),)
                )
                assert index.rows("notes") != before
                raise RuntimeError("abort")

        with pytest.raises(RuntimeError, match="abort"):
            abort()
        assert index.rows("notes") == before
    finally:
        index.close()


@pytest.mark.perf
def test_warm_eight_hundred_file_refresh_budget(tmp_path: Path) -> None:
    for number in range(800):
        (tmp_path / f"module_{number}.py").write_text(f"value = {number}\n", encoding="utf-8")
    service = IndexService(
        SqliteIndex(tmp_path / ".cuanta/index.db"),
        LocalIndexInventory(tmp_path),
        lambda: "now",
        extractor=AstIndexExtractor(),
    )
    try:
        service.update()
        started = time.perf_counter()
        assert service.update().changed == 0
        assert time.perf_counter() - started < 2
    finally:
        service.close()
