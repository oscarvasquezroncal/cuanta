from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.application.file_costs import FileCostsQuery
from cuanta.domain.code_index import IndexedFile, IndexHistory, IndexRow, IndexTable
from cuanta.domain.costs import CostSource
from cuanta.domain.index_facts import history_row
from cuanta.domain.ledger import Run
from cuanta.ports.code_index import CodeIndex
from cuanta.ports.ledger import Ledger

NOW = "2026-01-31T00:00:00Z"
AT = "2026-01-01T00:00:00Z"


class StorageReadError(Exception):
    pass


class FakeIndex:
    def __init__(
        self, records: tuple[IndexRow, ...], calls: list[str], errors: Mapping[str, Exception]
    ) -> None:
        self.records = records
        self.calls = calls
        self.errors = errors

    def files(self) -> tuple[IndexedFile, ...]:
        raise AssertionError("File cost query must read existing history only")

    def rows(self, table: IndexTable, path: str = "") -> tuple[IndexRow, ...]:
        assert table == "history"
        assert path == ""
        self.calls.append("index:read")
        if error := self.errors.get("read"):
            raise error
        return self.records

    def replace_files(self, files: Sequence[IndexedFile], removed: Sequence[str]) -> None:
        raise AssertionError(f"Unexpected file mutation: {files!r}, {removed!r}")

    def put_rows(self, table: IndexTable, rows: Sequence[IndexRow]) -> None:
        raise AssertionError(f"Unexpected row mutation: {table!r}, {rows!r}")

    def replace_rows(self, table: IndexTable, path: str, rows: Sequence[IndexRow]) -> None:
        raise AssertionError(f"Unexpected row mutation: {table!r}, {path!r}, {rows!r}")

    def meta(self) -> Mapping[str, str]:
        raise AssertionError("File cost query must read existing history only")

    def set_meta(self, values: Mapping[str, str]) -> None:
        raise AssertionError(f"Unexpected metadata mutation: {values!r}")

    def close(self) -> None:
        self.calls.append("index:close")
        if error := self.errors.get("close"):
            raise error


class FakeLedger(MemoryLedger):
    def __init__(self, calls: list[str], errors: Mapping[str, Exception]) -> None:
        super().__init__()
        self.calls = calls
        self.errors = errors

    def runs(
        self, kind: str = "", hu_ref: str = "", limit: int = 0, since: str = ""
    ) -> tuple[Run, ...]:
        self.calls.append("ledger:read")
        if error := self.errors.get("read"):
            raise error
        return super().runs(kind, hu_ref, limit, since)

    def close(self) -> None:
        self.calls.append("ledger:close")
        if error := self.errors.get("close"):
            raise error


class Harness:
    def __init__(
        self,
        rows: tuple[IndexRow, ...] = (),
        runs: tuple[Run, ...] = (),
        index_errors: Mapping[str, Exception] | None = None,
        ledger_errors: Mapping[str, Exception] | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.index_errors = index_errors or {}
        self.ledger_errors = ledger_errors or {}
        self.index = FakeIndex(rows, self.calls, self.index_errors)
        self.ledger = FakeLedger(self.calls, self.ledger_errors)
        for run in runs:
            self.ledger.add_run(run)

    def open_index(self) -> CodeIndex:
        self.calls.append("index:open")
        if error := self.index_errors.get("open"):
            raise error
        return self.index

    def open_ledger(self) -> Ledger:
        self.calls.append("ledger:open")
        if error := self.ledger_errors.get("open"):
            raise error
        return self.ledger

    def now(self) -> str:
        self.calls.append("clock")
        return NOW

    def query(
        self, error_types: tuple[type[Exception], ...] = (OSError, ValueError)
    ) -> FileCostsQuery:
        return FileCostsQuery(self.open_index, self.open_ledger, self.now, error_types=error_types)


def _run(
    identity: str = "R",
    cost: float | None = 2.0,
    source: CostSource = "reported",
    kind: str = "mandate",
    parent: str = "",
) -> Run:
    return Run(
        identity,
        kind,
        engine="claude",
        status="ok",
        task_type="feature",
        parent_id=parent,
        started_at=AT,
        ended_at=AT,
        cost_usd=cost,
        cost_source=source,
    )


def _row(path: str = "src/a.py", run_id: str = "R") -> IndexRow:
    return history_row(IndexHistory(path, run_id, "feature", "read", AT), "source-hash")


def test_query_reads_closes_index_before_ledger_and_conserves_known_cost() -> None:
    harness = Harness((_row(), _row("src/b.py")), (_run(),))
    view = harness.query().report()
    assert view.available
    assert view.unavailable_reason == ""
    assert view.report.known_cost_usd == view.report.allocated_known_usd == 2.0
    assert view.report.unallocated_known_usd == 0.0
    assert [row.known_allocated_usd for row in view.report.rows] == [1.0, 1.0]
    assert harness.calls == [
        "index:open",
        "index:read",
        "index:close",
        "ledger:open",
        "ledger:read",
        "ledger:close",
        "clock",
    ]


@pytest.mark.parametrize("stage", ["open", "read", "close"])
def test_index_unavailable_never_opens_ledger_or_exposes_exception_details(stage: str) -> None:
    harness = Harness(index_errors={stage: OSError("storage-private-path")})
    view = harness.query().report()
    assert not view.available
    assert view.unavailable_reason == "index_unavailable"
    assert view.report.attempts == 0
    assert "storage-private-path" not in str(asdict(view))
    assert not any(call.startswith("ledger:") for call in harness.calls)
    assert "clock" not in harness.calls
    assert harness.calls.count("index:close") == (0 if stage == "open" else 1)


@pytest.mark.parametrize("stage", ["open", "read", "close"])
def test_ledger_unavailable_closes_open_resources_and_has_safe_reason(stage: str) -> None:
    harness = Harness((_row(),), (_run(),), ledger_errors={stage: ValueError("private-schema")})
    view = harness.query().report()
    assert not view.available
    assert view.unavailable_reason == "ledger_unavailable"
    assert "private-schema" not in str(asdict(view))
    assert harness.calls[:3] == ["index:open", "index:read", "index:close"]
    assert harness.calls.count("ledger:close") == (0 if stage == "open" else 1)
    assert "clock" not in harness.calls


@pytest.mark.parametrize("resource", ["index", "ledger"])
@pytest.mark.parametrize("stage", ["open", "read"])
def test_injected_storage_error_type_is_safely_unavailable(resource: str, stage: str) -> None:
    errors = {stage: StorageReadError("private-driver-detail")}
    harness = Harness(
        index_errors=errors if resource == "index" else None,
        ledger_errors=errors if resource == "ledger" else None,
    )
    view = harness.query((OSError, ValueError, StorageReadError)).report()
    assert not view.available
    assert view.unavailable_reason == resource + "_unavailable"
    assert "private-driver-detail" not in str(asdict(view))
    assert harness.calls.count(resource + ":close") == (0 if stage == "open" else 1)


@pytest.mark.parametrize("resource", ["index", "ledger"])
def test_unexpected_read_error_propagates_after_closing_resource(resource: str) -> None:
    errors = {"read": RuntimeError("unexpected storage bug")}
    harness = Harness(
        index_errors=errors if resource == "index" else None,
        ledger_errors=errors if resource == "ledger" else None,
    )
    with pytest.raises(RuntimeError, match="unexpected storage bug"):
        harness.query().report()
    assert harness.calls.count(resource + ":close") == 1
    if resource == "index":
        assert "ledger:open" not in harness.calls


def test_existing_empty_index_remains_available_and_known_cost_is_unallocated() -> None:
    harness = Harness(runs=(_run(),))
    view = harness.query().report()
    assert view.available
    assert view.report.rows == ()
    assert view.report.known_cost_usd == view.report.unallocated_known_usd == 2.0
    assert view.report.allocated_known_usd == 0.0
    assert view.report.unallocated_attempts == 1
    assert harness.calls.count("ledger:read") == 1


def test_query_preserves_unknown_price_instead_of_zero_file_allocation() -> None:
    harness = Harness((_row(),), (_run(cost=None, source="unknown"),))
    view = harness.query().report()
    assert view.available
    assert view.report.unknown_attempts == 1
    assert view.report.known_attempts == 0
    assert view.report.rows[0].allocated_cost_usd is None
    assert view.report.rows[0].unknown_samples == 1


def test_query_groups_cross_root_children_as_one_attempt_and_file_sample() -> None:
    harness = Harness(
        (_row(run_id="ROOT"), _row(run_id="CHILD")),
        (_run("ROOT", 0.5, kind="cross"), _run("CHILD", 1.5, kind="cross", parent="ROOT")),
    )
    view = harness.query().report()
    assert view.available
    assert view.report.attempts == 1
    assert view.report.known_cost_usd == view.report.allocated_known_usd == 2.0
    assert len(view.report.rows) == 1
    assert view.report.rows[0].samples == 1
    assert view.report.rows[0].mix == "cross-engine"
    assert harness.calls.count("ledger:read") == 1


def test_unknown_cross_child_prevents_known_root_becoming_a_complete_price() -> None:
    harness = Harness(
        (_row(run_id="CHILD"),),
        (
            _run("ROOT", 0.5, kind="cross"),
            _run("CHILD", None, "unknown", kind="cross", parent="ROOT"),
        ),
    )
    view = harness.query().report()
    assert view.available
    assert view.report.attempts == view.report.unknown_attempts == 1
    assert view.report.rows[0].allocated_cost_usd is None


def test_malformed_history_is_counted_and_known_cost_stays_unallocated() -> None:
    harness = Harness((IndexRow("bad", "src/a.py", "hash", "ledger:R", "{}"),), (_run(),))
    view = harness.query().report()
    assert view.available
    assert view.report.invalid_rows == 1
    assert view.report.rows == ()
    assert view.report.known_cost_usd == view.report.unallocated_known_usd == 2.0
