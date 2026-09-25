from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.storage.migrations import LATEST_VERSION
from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.domain.ledger import (
    Baseline,
    Capsule,
    Decision,
    LedgerEvent,
    Run,
    SignatureRecord,
    Snapshot,
    TestRunRecord,
)
from cuanta.ports.ledger import EventQuery, Ledger


@pytest.fixture(params=["sqlite", "memory"])
def ledger(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Ledger]:
    instance: Ledger = (
        SqliteLedger(tmp_path / "ledger.db") if request.param == "sqlite" else MemoryLedger()
    )
    yield instance
    instance.close()


def _test_run(identifier: str, run_id: str, started: str) -> TestRunRecord:
    return TestRunRecord(
        identifier, run_id, "pytest", "pytest", "red", 1, 1, 0, 0, 0.1, "cap:1", started
    )


def test_schema_version(ledger: Ledger) -> None:
    assert ledger.schema_version() == LATEST_VERSION


def test_runs_roundtrip_and_order(ledger: Ledger) -> None:
    ledger.add_run(Run(id="01A", kind="init", trace_id="t1", hu_ref="HU-001"))
    ledger.add_run(Run(id="01B", kind="mandate", hu_ref="HU-001"))
    ledger.update_run(Run(id="01A", kind="init", status="ok", cost_usd=1.5, trace_id="t1"))
    run = ledger.get_run("01A")
    assert run is not None and run.status == "ok" and run.cost_usd == 1.5
    assert [item.id for item in ledger.runs()] == ["01B", "01A"]
    assert [item.id for item in ledger.runs(kind="init")] == ["01A"]
    assert len(ledger.runs(hu_ref="HU-001", limit=1)) == 1
    assert ledger.run_by_trace("t1") is not None
    assert ledger.run_by_trace("") is None
    assert ledger.get_run("zzz") is None


def test_events_filtering(ledger: Ledger) -> None:
    events = [
        LedgerEvent(
            run_id="r1", session_id="s1", ts="2026-01-01T00:00:01Z", input_tokens=10, success=True
        ),
        LedgerEvent(
            run_id="r1", session_id="s2", ts="2026-01-01T00:00:00Z", output_tokens=5, success=None
        ),
        LedgerEvent(run_id="r2", ts="2026-01-02T00:00:00Z", trace_id="abc"),
    ]
    assert ledger.add_events(events) == 3
    selected = ledger.events(EventQuery(run_id="r1"))
    assert [event.ts for event in selected] == ["2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z"]
    assert selected[1].success is True and selected[0].success is None
    assert selected[1].total_tokens == 10
    assert len(ledger.events(EventQuery(session_id="s1"))) == 1
    assert len(ledger.events(EventQuery(since="2026-01-02"))) == 1
    assert len(ledger.events(EventQuery(trace_id="abc"))) == 1
    assert len(ledger.events(EventQuery(limit=2))) == 2
    assert ledger.add_events([]) == 0


def test_test_runs_and_signature_history(ledger: Ledger) -> None:
    ledger.add_test_run(
        _test_run("T1", "R", "2026-01-01T00:00:00Z"),
        [SignatureRecord("T1", "sig1", "E: x", "E: x", "t", "a.py:1", 2)],
    )
    ledger.add_test_run(
        _test_run("T2", "R", "2026-01-01T00:01:00Z"),
        [
            SignatureRecord("T2", "sig1", "E: x", "E: x", "t", "a.py:1", 1),
            SignatureRecord("T2", "sig2", "F: y", "F: y", "u", "", 3),
        ],
    )
    assert [record.id for record in ledger.test_runs(run_id="R")] == ["T2", "T1"]
    assert ledger.test_runs(limit=1)[0].id == "T2"
    assert [item.signature_id for item in ledger.signatures("T2")] == ["sig2", "sig1"]
    assert [record.id for record in ledger.signature_history("sig1")] == ["T2", "T1"]


def test_capsules_by_prefix(ledger: Ledger) -> None:
    ledger.add_capsule(Capsule("cap:abcdef0123456789", "abcdef", "p", "test", 10, 2, "s", "2026"))
    assert ledger.get_capsule("cap:abcdef") is not None
    assert ledger.get_capsule("abcdef0123456789") is not None
    assert ledger.get_capsule("cap:ffff") is None
    assert ledger.get_capsule("") is None


def test_decisions(ledger: Ledger) -> None:
    identifier = ledger.add_decision(
        Decision("R", "heuristic", "choose", "q", "[]", "normal", 0.7, 1)
    )
    ledger.set_decision_outcome(identifier, "correct")
    stored = ledger.decisions(run_id="R")
    assert stored[0].outcome == "correct"
    assert stored[0].id == identifier


def test_snapshots_and_baselines(ledger: Ledger) -> None:
    ledger.add_snapshots([Snapshot("R", "start", "b.py", "1"), Snapshot("R", "start", "a.py", "2")])
    ledger.add_snapshots([Snapshot("R", "start", "a.py", "3")])
    assert [(item.path, item.sha256) for item in ledger.snapshots("R", "start")] == [
        ("a.py", "3"),
        ("b.py", "1"),
    ]
    assert ledger.snapshots("R", "end") == ()
    ledger.add_baselines([Baseline("R", "rulebook", "CLAUDE.md", "CLAUDE.md", 400, 100)])
    assert ledger.baselines("R")[0].tokens == 100


def test_sqlite_uses_wal_and_migrates_idempotently(tmp_path: Path) -> None:
    path = tmp_path / "l.db"
    first = SqliteLedger(path)
    first.add_run(Run(id="X", kind="init"))
    first.close()
    second = SqliteLedger(path)
    assert second.get_run("X") is not None
    assert second.schema_version() == LATEST_VERSION
    second.close()
    assert (tmp_path / "l.db-wal").exists() or path.exists()


def test_unknown_run_cost_round_trips_as_none(ledger: Ledger) -> None:
    ledger.add_run(Run(id="C", kind="mandate", engine="codex", status="ok", cost_usd=None))
    stored = ledger.get_run("C")
    assert stored is not None
    assert stored.cost_usd is None


def test_migration_marks_old_codex_zero_costs_unknown(tmp_path: Path) -> None:
    import sqlite3

    from cuanta.adapters.storage.migrations import MIGRATIONS

    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.executescript(MIGRATIONS[0])
    connection.execute("INSERT INTO schema_version(version) VALUES (1)")
    rows = [
        ("A", "mandate", "codex", "ok", 0.0),
        ("B", "mandate", "claude", "ok", 0.0),
        ("C", "mandate", "codex", "ok", 1.5),
    ]
    connection.executemany(
        "INSERT INTO runs(id, kind, engine, status, cost_usd) VALUES (?, ?, ?, ?, ?)", rows
    )
    connection.commit()
    connection.close()
    upgraded = SqliteLedger(path)
    try:
        assert upgraded.schema_version() == LATEST_VERSION
        costs = {run_id: getattr(upgraded.get_run(run_id), "cost_usd", -1.0) for run_id in "ABC"}
        assert costs == {"A": None, "B": 0.0, "C": 1.5}
    finally:
        upgraded.close()


def test_routing_decisions_round_trip_and_close(ledger: Ledger) -> None:
    from cuanta.domain.ledger import RoutingDecision

    first = RoutingDecision("R1", "bug", "senior", "premium", "claude", "opus", "policy", 0.8)
    other = RoutingDecision("R2", "bug", "docs", "economy", "claude", "haiku", "policy")
    ledger.add_routing_decision(first)
    ledger.add_routing_decision(other)
    ledger.set_routing_outcome("R1", "green", 1.25, 1)
    ledger.set_routing_accepted("R1", True)
    stored = ledger.routing_decisions(run_id="R1")
    assert len(stored) == 1
    assert (stored[0].outcome, stored[0].cost_usd, stored[0].retries, stored[0].accepted) == (
        "green",
        1.25,
        1,
        1,
    )
    assert stored[0].confidence == 0.8
    assert ledger.routing_decisions(run_id="R2")[0].outcome == ""
    assert len(ledger.routing_decisions(limit=1)) == 1
