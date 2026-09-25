from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import astuple, fields, replace
from pathlib import Path
from typing import Any

from cuanta.adapters.storage.migrations import LATEST_VERSION, MIGRATIONS
from cuanta.domain.ledger import (
    Baseline,
    Capsule,
    Decision,
    LedgerEvent,
    RouteAudit,
    RoutingDecision,
    Run,
    SignatureRecord,
    Snapshot,
    TestRunRecord,
)
from cuanta.ports.ledger import EventQuery

RUN_COLUMNS = tuple(item.name for item in fields(Run))
LOCK_RETRY_S = 10.0
EVENT_COLUMNS = tuple(item.name for item in fields(LedgerEvent) if item.name != "id")
TEST_RUN_COLUMNS = tuple(item.name for item in fields(TestRunRecord))
SIGNATURE_COLUMNS = tuple(item.name for item in fields(SignatureRecord))
CAPSULE_COLUMNS = tuple(item.name for item in fields(Capsule))
DECISION_COLUMNS = tuple(item.name for item in fields(Decision) if item.name != "id")
BASELINE_COLUMNS = tuple(item.name for item in fields(Baseline))
AUDIT_COLUMNS = tuple(item.name for item in fields(RouteAudit))
ROUTING_COLUMNS = tuple(item.name for item in fields(RoutingDecision) if item.name != "id")


def _placeholders(columns: Sequence[str]) -> str:
    return ", ".join("?" for _ in columns)


def _event_row(event: LedgerEvent) -> tuple[Any, ...]:
    values = []
    for name in EVENT_COLUMNS:
        value = getattr(event, name)
        if name == "success":
            value = None if value is None else int(bool(value))
        values.append(value)
    return tuple(values)


def _event(row: sqlite3.Row) -> LedgerEvent:
    data = {name: row[name] for name in EVENT_COLUMNS}
    success = data["success"]
    data["success"] = None if success is None else bool(success)
    return LedgerEvent(id=row["id"], **data)


class SqliteLedger:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(
            path, check_same_thread=False, timeout=30, isolation_level=None
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout=30000")
        self._retry(lambda: self._connection.execute("PRAGMA journal_mode=WAL"))
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._retry(self._migrate)

    @property
    def path(self) -> Path:
        return self._path

    def _retry(self, action: Callable[[], object]) -> None:
        deadline = time.monotonic() + LOCK_RETRY_S
        while True:
            try:
                action()
                return
            except sqlite3.OperationalError as error:
                if "locked" not in str(error) or time.monotonic() > deadline:
                    raise
                time.sleep(0.05)

    def _migrate(self) -> None:
        with self._lock:
            connection = self._connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                exists = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
                ).fetchone()
                current = 0
                if exists:
                    row = connection.execute(
                        "SELECT MAX(version) AS v FROM schema_version"
                    ).fetchone()
                    current = int(row["v"] or 0)
                for index in range(current, LATEST_VERSION):
                    for statement in MIGRATIONS[index].split(";"):
                        if statement.strip():
                            connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_version(version) VALUES (?)", (index + 1,)
                    )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            self._connection.execute("COMMIT")

    def _query(self, sql: str, parameters: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._connection.execute(sql, tuple(parameters)).fetchall())

    def _execute(self, sql: str, parameters: Sequence[Any] = ()) -> int:
        with self._transaction() as connection:
            cursor = connection.execute(sql, tuple(parameters))
            return int(cursor.lastrowid or 0)

    def schema_version(self) -> int:
        rows = self._query("SELECT MAX(version) AS v FROM schema_version")
        return int(rows[0]["v"] or 0)

    def add_run(self, run: Run) -> None:
        columns = ", ".join(RUN_COLUMNS)
        self._execute(
            f"INSERT OR REPLACE INTO runs ({columns}) VALUES ({_placeholders(RUN_COLUMNS)})",
            astuple(run),
        )

    def update_run(self, run: Run) -> None:
        self.add_run(run)

    def get_run(self, run_id: str) -> Run | None:
        rows = self._query("SELECT * FROM runs WHERE id = ?", (run_id,))
        return Run(**{name: rows[0][name] for name in RUN_COLUMNS}) if rows else None

    def runs(self, kind: str = "", hu_ref: str = "", limit: int = 0) -> tuple[Run, ...]:
        clauses, parameters = [], []
        if kind:
            clauses.append("kind = ?")
            parameters.append(kind)
        if hu_ref:
            clauses.append("hu_ref = ?")
            parameters.append(hu_ref)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        suffix = f" LIMIT {int(limit)}" if limit else ""
        rows = self._query(f"SELECT * FROM runs {where} ORDER BY id DESC{suffix}", parameters)
        return tuple(Run(**{name: row[name] for name in RUN_COLUMNS}) for row in rows)

    def run_by_trace(self, trace_id: str) -> Run | None:
        if not trace_id:
            return None
        rows = self._query("SELECT * FROM runs WHERE trace_id = ? LIMIT 1", (trace_id,))
        return Run(**{name: rows[0][name] for name in RUN_COLUMNS}) if rows else None

    def add_events(self, events: Sequence[LedgerEvent]) -> int:
        if not events:
            return 0
        columns = ", ".join(EVENT_COLUMNS)
        sql = f"INSERT INTO events ({columns}) VALUES ({_placeholders(EVENT_COLUMNS)})"
        with self._transaction() as connection:
            connection.executemany(sql, [_event_row(event) for event in events])
        return len(events)

    def delete_events(self, source: str) -> int:
        with self._transaction() as connection:
            cursor = connection.execute("DELETE FROM events WHERE source = ?", (source,))
        return int(cursor.rowcount)

    def events(self, query: EventQuery) -> tuple[LedgerEvent, ...]:
        clauses, parameters = [], []
        for column, value in (
            ("run_id", query.run_id),
            ("session_id", query.session_id),
            ("trace_id", query.trace_id),
        ):
            if value:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        if query.since:
            clauses.append("ts >= ?")
            parameters.append(query.since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        suffix = f" LIMIT {int(query.limit)}" if query.limit else ""
        rows = self._query(f"SELECT * FROM events {where} ORDER BY ts, id{suffix}", parameters)
        return tuple(_event(row) for row in rows)

    def add_test_run(self, record: TestRunRecord, signatures: Sequence[SignatureRecord]) -> None:
        with self._transaction() as connection:
            connection.execute(
                f"INSERT OR REPLACE INTO test_runs ({', '.join(TEST_RUN_COLUMNS)}) "
                f"VALUES ({_placeholders(TEST_RUN_COLUMNS)})",
                astuple(record),
            )
            connection.executemany(
                f"INSERT OR REPLACE INTO signatures ({', '.join(SIGNATURE_COLUMNS)}) "
                f"VALUES ({_placeholders(SIGNATURE_COLUMNS)})",
                [astuple(signature) for signature in signatures],
            )

    def test_runs(self, run_id: str = "", limit: int = 0) -> tuple[TestRunRecord, ...]:
        where = "WHERE run_id = ?" if run_id else ""
        parameters = (run_id,) if run_id else ()
        suffix = f" LIMIT {int(limit)}" if limit else ""
        rows = self._query(
            f"SELECT * FROM test_runs {where} ORDER BY started_at DESC, id DESC{suffix}", parameters
        )
        return tuple(
            TestRunRecord(**{name: row[name] for name in TEST_RUN_COLUMNS}) for row in rows
        )

    def signatures(self, test_run_id: str) -> tuple[SignatureRecord, ...]:
        rows = self._query(
            "SELECT * FROM signatures WHERE test_run_id = ? ORDER BY tests DESC, first_test",
            (test_run_id,),
        )
        return tuple(
            SignatureRecord(**{name: row[name] for name in SIGNATURE_COLUMNS}) for row in rows
        )

    def signature_history(self, signature_id: str) -> tuple[TestRunRecord, ...]:
        rows = self._query(
            "SELECT t.* FROM test_runs t JOIN signatures s ON s.test_run_id = t.id "
            "WHERE s.signature_id = ? ORDER BY t.started_at DESC, t.id DESC",
            (signature_id,),
        )
        return tuple(
            TestRunRecord(**{name: row[name] for name in TEST_RUN_COLUMNS}) for row in rows
        )

    def add_capsule(self, capsule: Capsule) -> None:
        self._execute(
            f"INSERT OR REPLACE INTO capsules ({', '.join(CAPSULE_COLUMNS)}) "
            f"VALUES ({_placeholders(CAPSULE_COLUMNS)})",
            astuple(capsule),
        )

    def get_capsule(self, reference: str) -> Capsule | None:
        key = reference.removeprefix("cap:")
        if not key:
            return None
        rows = self._query(
            "SELECT * FROM capsules WHERE id = ? OR id LIKE ? ORDER BY created_at DESC LIMIT 2",
            (f"cap:{key}", f"cap:{key}%"),
        )
        if len(rows) != 1 and not (rows and rows[0]["id"] == f"cap:{key}"):
            return None
        return Capsule(**{name: rows[0][name] for name in CAPSULE_COLUMNS})

    def add_decision(self, decision: Decision) -> int:
        values = tuple(getattr(decision, name) for name in DECISION_COLUMNS)
        return self._execute(
            f"INSERT INTO decisions ({', '.join(DECISION_COLUMNS)}) "
            f"VALUES ({_placeholders(DECISION_COLUMNS)})",
            values,
        )

    def set_decision_outcome(self, decision_id: int, outcome: str) -> None:
        self._execute("UPDATE decisions SET outcome = ? WHERE id = ?", (outcome, decision_id))

    def decisions(self, run_id: str = "", limit: int = 0) -> tuple[Decision, ...]:
        where = "WHERE run_id = ?" if run_id else ""
        parameters = (run_id,) if run_id else ()
        suffix = f" LIMIT {int(limit)}" if limit else ""
        rows = self._query(f"SELECT * FROM decisions {where} ORDER BY id DESC{suffix}", parameters)
        return tuple(
            replace(
                Decision(id=row["id"], **{name: row[name] for name in DECISION_COLUMNS}),
                preview=bool(row["preview"]),
            )
            for row in rows
        )

    def preview_decision(self, request_hash: str, question: str) -> int | None:
        rows = self._query(
            "SELECT id FROM decisions WHERE request_hash = ? AND question = ? AND preview = 1 "
            "AND run_id = '' ORDER BY id DESC LIMIT 1",
            (request_hash, question),
        )
        return int(rows[0]["id"]) if rows else None

    def link_decisions(self, request_hash: str, run_id: str) -> int:
        linked = self._query(
            "SELECT COUNT(*) AS n FROM decisions WHERE request_hash = ? AND run_id = ''",
            (request_hash,),
        )
        self._execute(
            "UPDATE decisions SET run_id = ? WHERE request_hash = ? AND run_id = ''",
            (run_id, request_hash),
        )
        return int(linked[0]["n"]) if linked else 0

    def close_run_decisions(self, run_id: str, outcome: str) -> None:
        self._execute(
            "UPDATE decisions SET outcome = ? WHERE run_id = ? AND outcome = ''",
            (outcome, run_id),
        )

    def add_routing_decision(self, decision: RoutingDecision) -> int:
        values = tuple(getattr(decision, name) for name in ROUTING_COLUMNS)
        return self._execute(
            f"INSERT INTO routing_decisions ({', '.join(ROUTING_COLUMNS)}) "
            f"VALUES ({_placeholders(ROUTING_COLUMNS)})",
            values,
        )

    def routing_decisions(self, run_id: str = "", limit: int = 0) -> tuple[RoutingDecision, ...]:
        where = "WHERE run_id = ?" if run_id else ""
        parameters = (run_id,) if run_id else ()
        suffix = f" LIMIT {int(limit)}" if limit else ""
        rows = self._query(
            f"SELECT * FROM routing_decisions {where} ORDER BY id DESC{suffix}", parameters
        )
        return tuple(
            RoutingDecision(id=row["id"], **{name: row[name] for name in ROUTING_COLUMNS})
            for row in rows
        )

    def set_routing_outcome(
        self, run_id: str, outcome: str, cost_usd: float | None, retries: int
    ) -> None:
        self._execute(
            "UPDATE routing_decisions SET outcome = ?, cost_usd = ?, retries = ? WHERE run_id = ?",
            (outcome, cost_usd, retries, run_id),
        )

    def set_routing_accepted(self, run_id: str, accepted: bool) -> None:
        self._execute(
            "UPDATE routing_decisions SET accepted = ? WHERE run_id = ?",
            (1 if accepted else 0, run_id),
        )

    def add_route_audits(self, audits: Sequence[RouteAudit]) -> None:
        with self._transaction() as connection:
            connection.executemany(
                f"INSERT OR REPLACE INTO route_audits ({', '.join(AUDIT_COLUMNS)}) "
                f"VALUES ({_placeholders(AUDIT_COLUMNS)})",
                [astuple(audit) for audit in audits],
            )

    def route_audits(self, run_id: str) -> tuple[RouteAudit, ...]:
        rows = self._query("SELECT * FROM route_audits WHERE run_id = ? ORDER BY agent", (run_id,))
        return tuple(RouteAudit(**{name: row[name] for name in AUDIT_COLUMNS}) for row in rows)

    def add_snapshots(self, snapshots: Sequence[Snapshot]) -> None:
        with self._transaction() as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO file_snapshots (run_id, phase, path, sha256) "
                "VALUES (?, ?, ?, ?)",
                [astuple(snapshot) for snapshot in snapshots],
            )

    def snapshots(self, run_id: str, phase: str) -> tuple[Snapshot, ...]:
        rows = self._query(
            "SELECT * FROM file_snapshots WHERE run_id = ? AND phase = ? ORDER BY path",
            (run_id, phase),
        )
        return tuple(
            Snapshot(row["run_id"], row["phase"], row["path"], row["sha256"]) for row in rows
        )

    def add_baselines(self, baselines: Sequence[Baseline]) -> None:
        with self._transaction() as connection:
            connection.executemany(
                f"INSERT INTO baselines ({', '.join(BASELINE_COLUMNS)}) "
                f"VALUES ({_placeholders(BASELINE_COLUMNS)})",
                [astuple(baseline) for baseline in baselines],
            )

    def baselines(self, run_id: str = "") -> tuple[Baseline, ...]:
        where = "WHERE run_id = ?" if run_id else ""
        parameters = (run_id,) if run_id else ()
        rows = self._query(f"SELECT * FROM baselines {where} ORDER BY id", parameters)
        return tuple(Baseline(**{name: row[name] for name in BASELINE_COLUMNS}) for row in rows)

    def close(self) -> None:
        with self._lock:
            self._connection.close()
