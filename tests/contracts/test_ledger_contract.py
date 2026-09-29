from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.storage.migrations import LATEST_VERSION
from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.domain.ledger import (
    Baseline,
    Capsule,
    Decision,
    Forecast,
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


def test_run_turns_round_trip(ledger: Ledger) -> None:
    run = Run(id="T", kind="mandate", max_turns=40, turns=42, end_reason="error_max_turns")
    ledger.add_run(run)
    assert ledger.get_run("T") == run


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


def test_read_only_ledger_reads_current_runs_events_and_snapshots_without_mutation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.db"
    run = Run(id="read-only", kind="mandate", status="ok")
    event = LedgerEvent(run_id=run.id, kind="tool_result", file_path="src/cart.py")
    snapshot = Snapshot(run.id, "start", "src/cart.py", "original-hash")
    with closing(SqliteLedger(path)) as writer:
        writer.add_run(run)
        writer.add_events([event])
        writer.add_snapshots([snapshot])
    before = path.read_bytes()
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        schema = connection.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()
    with closing(SqliteLedger(path, read_only=True)) as reader:
        assert reader.schema_version() == LATEST_VERSION
        assert reader.runs() == (run,)
        assert reader.get_run(run.id) == run
        events = reader.events(EventQuery(run_id=run.id))
        assert len(events) == 1 and replace(events[0], id=0) == event
        assert reader.snapshots(run.id, "start") == (snapshot,)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.add_run(Run(id="forbidden", kind="mandate"))
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.add_events([event])
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.add_snapshots([Snapshot(run.id, "start", "new.py", "new")])
        assert reader.runs() == (run,)
    assert path.read_bytes() == before
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        assert (
            connection.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()
            == schema
        )


def test_read_only_ledger_does_not_create_a_missing_database_or_parent(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "ledger.db"
    with pytest.raises(ValueError, match=r"read-only.*schema"):
        SqliteLedger(path, read_only=True)
    assert not path.exists()
    assert not path.parent.exists()


@pytest.mark.parametrize("version", [0, LATEST_VERSION - 1, LATEST_VERSION + 1])
def test_read_only_ledger_rejects_missing_old_or_future_schemas_without_changes(
    tmp_path: Path, version: int
) -> None:
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as connection:
        if version:
            connection.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
            connection.execute("INSERT INTO schema_version VALUES (?)", (version,))
            connection.commit()
    before = path.read_bytes()
    with pytest.raises(ValueError, match=r"read-only.*schema"):
        SqliteLedger(path, read_only=True)
    assert path.read_bytes() == before
    path.unlink()


def test_migration_adds_turn_columns_with_zero_defaults(tmp_path: Path) -> None:
    import sqlite3

    from cuanta.adapters.storage.migrations import MIGRATIONS

    path = tmp_path / "v8.db"
    connection = sqlite3.connect(path)
    for version, statements in enumerate(MIGRATIONS[:8], start=1):
        connection.executescript(statements)
        connection.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))
    connection.execute("INSERT INTO runs(id, kind) VALUES ('R', 'mandate')")
    connection.commit()
    connection.close()
    upgraded = SqliteLedger(path)
    try:
        assert upgraded.schema_version() == LATEST_VERSION
        run = upgraded.get_run("R")
        assert run is not None
        assert (run.max_turns, run.turns, run.end_reason) == (0, 0, "")
    finally:
        upgraded.close()


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


def test_cost_provenance_and_unknown_event_and_decision_round_trip(ledger: Ledger) -> None:
    ledger.add_run(Run("E", "mandate", cost_usd=0.25, cost_source="estimated"))
    stored = ledger.get_run("E")
    assert stored is not None and stored.cost_source == "estimated"
    ledger.add_events(
        [
            LedgerEvent(run_id="E", kind="result_usage", cost_usd=None),
            LedgerEvent(run_id="E", kind="result_usage", cost_usd=0.0),
        ]
    )
    assert [event.cost_usd for event in ledger.events(EventQuery(run_id="E"))] == [None, 0.0]
    for cost in (None, 0.0):
        ledger.add_decision(Decision("E", "llm", "noul", "q", "", "yes", 1.0, 1, cost_usd=cost))
    assert {decision.cost_usd for decision in ledger.decisions(run_id="E")} == {None, 0.0}


def test_cost_migration_preserves_legacy_values_fields_and_indexes(tmp_path: Path) -> None:
    import sqlite3

    from cuanta.adapters.storage.migrations import MIGRATIONS

    path = tmp_path / "legacy-cost.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        for version, statements in enumerate(MIGRATIONS[:9], start=1):
            connection.executescript(statements)
            connection.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))
        connection.execute("INSERT INTO runs(id, kind, cost_usd) VALUES ('R', 'mandate', 0)")
        connection.execute(
            "INSERT INTO events(id, run_id, cost_usd, effort, ttft_ms, raw) VALUES (?, ?, ?, ?, ?, ?)",
            (42, "R", 0.0, "low", 17, "preserved"),
        )
        connection.execute("INSERT INTO events(id, run_id) VALUES (100, 'deleted')")
        connection.execute("DELETE FROM events WHERE id = 100")
        connection.execute(
            "INSERT INTO decisions(id,run_id,backend,primitive,question,options,answer,confidence,"
            "latency_ms,cost_usd,preview,request_hash,fallback_error,fallback_from) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (12, "R", "llm", "noul", "q", "", "yes", 0.7, 5, 0.0, 1, "hash", "error", "jev"),
        )
    upgraded = SqliteLedger(path)
    try:
        run = upgraded.get_run("R")
        assert run is not None and run.cost_usd == 0.0 and run.cost_source == "unknown"
        event = upgraded.events(EventQuery(run_id="R"))[0]
        assert (event.id, event.cost_usd, event.effort, event.ttft_ms, event.raw) == (
            42,
            0.0,
            "low",
            17,
            "preserved",
        )
        decision = upgraded.decisions(run_id="R")[0]
        assert (decision.id, decision.cost_usd, decision.preview, decision.request_hash) == (
            12,
            0.0,
            True,
            "hash",
        )
        assert (decision.fallback_error, decision.fallback_from) == ("error", "jev")
        upgraded.add_events([LedgerEvent(run_id="new", cost_usd=None)])
        assert upgraded.events(EventQuery(run_id="new"))[0].id > 100
    finally:
        upgraded.close()
    reopened = SqliteLedger(path)
    try:
        assert reopened.schema_version() == LATEST_VERSION
        assert reopened.events(EventQuery(run_id="new"))[0].cost_usd is None
    finally:
        reopened.close()
    with closing(sqlite3.connect(path)) as connection, connection:
        indexes = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
    assert {
        "idx_events_run",
        "idx_events_trace",
        "idx_events_session",
        "idx_events_ts",
        "idx_decisions_run",
        "idx_decisions_hash",
    } <= indexes


def test_run_outcome_is_recorded_once_with_its_time(ledger: Ledger) -> None:
    ledger.add_run(Run(id="S", kind="mandate", mode="sandbox"))
    assert ledger.set_run_outcome("S", "accepted", "2026-09-26T10:00:00+00:00")
    assert not ledger.set_run_outcome("S", "rejected", "2026-09-26T11:00:00+00:00")
    assert not ledger.set_run_outcome("missing", "accepted", "2026-09-26T10:00:00+00:00")
    stored = ledger.get_run("S")
    assert stored is not None
    assert (stored.mode, stored.outcome, stored.outcome_at) == (
        "sandbox",
        "accepted",
        "2026-09-26T10:00:00+00:00",
    )


def test_run_outcome_survives_a_later_full_row_update(ledger: Ledger) -> None:
    stale = Run(id="S", kind="mandate", mode="sandbox", status="running")
    ledger.add_run(stale)
    assert ledger.set_run_outcome("S", "rejected", "2026-09-26T10:00:00+00:00", "too broad")
    ledger.update_run(replace(stale, status="ok", cost_usd=0.5))
    stored = ledger.get_run("S")
    assert stored is not None
    assert (stored.status, stored.cost_usd) == ("ok", 0.5)
    assert (stored.outcome, stored.outcome_reason) == ("rejected", "too broad")
    assert not ledger.set_run_outcome("S", "accepted", "2026-09-26T11:00:00+00:00")


def test_update_run_inserts_a_run_it_has_not_seen(ledger: Ledger) -> None:
    ledger.update_run(Run(id="N", kind="loop", status="ok"))
    stored = ledger.get_run("N")
    assert stored is not None and stored.status == "ok"


def test_estimates_caps_and_reasons_round_trip(ledger: Ledger) -> None:
    run = Run(
        id="E",
        kind="mandate",
        estimate_low=0.4,
        estimate_high=0.9,
        estimate_source="history",
        estimate_samples=5,
        cap_usd=0.0,
    )
    ledger.add_run(run)
    assert ledger.get_run("E") == run
    ledger.add_run(Run(id="P", kind="mandate", estimate_source="none"))
    plain = ledger.get_run("P")
    assert plain is not None
    assert (plain.estimate_low, plain.estimate_high, plain.cap_usd) == (None, None, None)


def test_runs_since_keeps_runs_started_in_the_window(ledger: Ledger) -> None:
    ledger.add_run(Run(id="A", kind="mandate", started_at="2026-08-01T00:00:00Z"))
    ledger.add_run(Run(id="B", kind="mandate", started_at="2026-09-01T00:00:00Z"))
    ledger.add_run(Run(id="C", kind="cross", started_at="2026-09-02T00:00:00Z"))
    assert [run.id for run in ledger.runs(since="2026-09-01T00:00:00Z")] == ["C", "B"]
    assert [run.id for run in ledger.runs(kind="cross", since="2026-08-01T00:00:00Z")] == ["C"]


def test_migration_adds_estimate_cap_and_reason_columns_as_unknown(tmp_path: Path) -> None:
    import sqlite3
    from contextlib import closing

    from cuanta.adapters.storage.migrations import MIGRATIONS

    path = tmp_path / "v11.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        for version, statements in enumerate(MIGRATIONS[:11], start=1):
            connection.executescript(statements)
            connection.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))
        connection.execute(
            "INSERT INTO runs(id, kind, status, cost_usd, outcome) "
            "VALUES ('R', 'mandate', 'ok', 0.3, 'accepted')"
        )
    upgraded = SqliteLedger(path)
    try:
        assert upgraded.schema_version() == LATEST_VERSION
        run = upgraded.get_run("R")
        assert run is not None
        assert (run.cost_usd, run.outcome) == (0.3, "accepted")
        assert (
            run.estimate_low,
            run.estimate_high,
            run.estimate_source,
            run.estimate_samples,
            run.cap_usd,
            run.outcome_reason,
        ) == (None, None, "", 0, None, "")
    finally:
        upgraded.close()


def test_migration_adds_mode_and_outcome_columns_with_empty_defaults(tmp_path: Path) -> None:
    import sqlite3

    from cuanta.adapters.storage.migrations import MIGRATIONS

    path = tmp_path / "v10.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        for version, statements in enumerate(MIGRATIONS[:10], start=1):
            connection.executescript(statements)
            connection.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))
        connection.execute("INSERT INTO runs(id, kind, status) VALUES ('R', 'mandate', 'ok')")
    upgraded = SqliteLedger(path)
    try:
        assert upgraded.schema_version() == LATEST_VERSION
        run = upgraded.get_run("R")
        assert run is not None
        assert (run.status, run.mode, run.outcome, run.outcome_at) == ("ok", "", "", "")
    finally:
        upgraded.close()


def test_project_level_test_runs_leave_out_runs_made_in_an_isolated_copy(ledger: Ledger) -> None:
    ledger.add_run(Run(id="P", kind="mandate"))
    ledger.add_run(Run(id="S", kind="mandate", mode="sandbox"))
    ledger.add_test_run(_test_run("T1", "P", "2026-09-26T10:00:00"), [])
    ledger.add_test_run(_test_run("T2", "S", "2026-09-26T11:00:00"), [])
    ledger.add_test_run(_test_run("T3", "", "2026-09-26T09:00:00"), [])
    assert [record.id for record in ledger.test_runs(limit=1)] == ["T2"]
    assert [record.id for record in ledger.test_runs(project_only=True)] == ["T1", "T3"]
    assert [record.id for record in ledger.test_runs(run_id="S")] == ["T2"]


def _forecast(
    run_id: str, created_at: str, provider: str = "claude", task_type: str = "feature"
) -> Forecast:
    return Forecast(
        run_id,
        created_at,
        provider,
        task_type,
        "normal",
        "pipeline",
        0.5,
        0.8,
        1.5,
        "comfortable",
        '{"start":1}',
        '[{"role":"senior"}]',
        '{"edit_files":2}',
    )


def test_forecasts_round_trip_with_their_actuals(ledger: Ledger) -> None:
    ledger.add_run(Run(id="A", kind="mandate", status="ok", cost_usd=0.4))
    ledger.add_run(Run(id="B", kind="cross", status="ok", cost_usd=0.1))
    ledger.add_run(Run(id="B1", kind="cross", status="ok", cost_usd=0.2, parent_id="B"))
    ledger.add_run(Run(id="C", kind="mandate", cost_usd=0.1))
    ledger.add_forecast(_forecast("A", "2026-09-28T01:00:00Z"))
    ledger.add_forecast(_forecast("B", "2026-09-28T02:00:00Z", task_type="bug"))
    ledger.add_forecast(_forecast("C", "2026-09-28T03:00:00Z", provider="codex"))
    ledger.add_forecast(_forecast("D", "2026-09-28T04:00:00Z"))
    found = ledger.forecasts()
    assert [item.forecast.run_id for item in found] == ["D", "C", "B", "A"]
    assert found[-1].forecast == _forecast("A", "2026-09-28T01:00:00Z")
    actuals = {item.forecast.run_id: item.actual_usd for item in found}
    assert (actuals["A"], actuals["C"], actuals["D"]) == (0.4, None, None)
    assert actuals["B"] == pytest.approx(0.3)
    assert [item.forecast.run_id for item in ledger.forecasts(provider="claude")] == [
        "D",
        "B",
        "A",
    ]
    assert [item.forecast.run_id for item in ledger.forecasts(task_type="fix")] == ["B"]
    assert [item.forecast.run_id for item in ledger.forecasts("claude", limit=2)] == ["D", "B"]
    ledger.add_forecast(replace(_forecast("A", "2026-09-28T01:00:00Z"), p50_usd=0.6))
    (again,) = ledger.forecasts("claude", "feature", limit=2)[1:]
    assert (again.forecast.run_id, again.forecast.p50_usd) == ("A", 0.6)
    assert ledger.forecast("B") == _forecast("B", "2026-09-28T02:00:00Z", task_type="bug")
    one = ledger.forecast("A")
    assert one is not None and one.p50_usd == 0.6
    assert ledger.forecast("missing") is None


def test_migration_adds_the_forecasts_table_idempotently(tmp_path: Path) -> None:
    from cuanta.adapters.storage.migrations import MIGRATIONS

    path = tmp_path / "v12.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        for version, statements in enumerate(MIGRATIONS[:12], start=1):
            connection.executescript(statements)
            connection.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))
        connection.execute(
            "INSERT INTO runs(id, kind, status, cost_usd) VALUES ('R', 'mandate', 'ok', 0.3)"
        )
    upgraded = SqliteLedger(path)
    try:
        assert upgraded.schema_version() == LATEST_VERSION
        assert upgraded.forecasts() == ()
        upgraded.add_forecast(_forecast("R", "2026-09-28T00:00:00Z"))
    finally:
        upgraded.close()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(MIGRATIONS[12])
    reopened = SqliteLedger(path)
    try:
        assert reopened.schema_version() == LATEST_VERSION
        (item,) = reopened.forecasts()
        assert (item.forecast, item.actual_usd) == (_forecast("R", "2026-09-28T00:00:00Z"), 0.3)
        run = reopened.get_run("R")
        assert run is not None
        assert (run.status, run.cost_usd) == ("ok", 0.3)
    finally:
        reopened.close()
