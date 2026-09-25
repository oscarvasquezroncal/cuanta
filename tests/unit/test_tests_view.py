from __future__ import annotations

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.application.tests_view import LatestTests, from_records
from cuanta.domain.ledger import SignatureRecord, TestRunRecord

RECORD = TestRunRecord(
    "t1", "", "pytest", "pytest -q", "red", 10, 2, 1, 0, 3.5, "cap-1", "2026-09-23"
)
SIGNATURE = SignatureRecord(
    "t1", "sig-1", "KeyError", "KeyError: 'MX'", "tests/a.py::t", "a.py:3", 3
)


def test_from_records_maps_hairballs() -> None:
    summary = from_records(RECORD, [SIGNATURE])
    assert not summary.green
    assert summary.hairballs[0].id == "sig-1"
    assert summary.hairballs[0].tests == 3
    assert summary.capsule == "cap-1"


def test_latest_tests_reads_newest_run() -> None:
    ledger = MemoryLedger()
    assert LatestTests(lambda: ledger, lambda: False).run() is None
    assert LatestTests(lambda: ledger, lambda: True).run() is None
    ledger.add_test_run(RECORD, [SIGNATURE])
    summary = LatestTests(lambda: ledger, lambda: True).run()
    assert summary is not None
    assert summary.failed + summary.errored == 3
