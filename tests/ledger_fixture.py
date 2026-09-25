from __future__ import annotations

from cuanta.domain.ledger import LedgerEvent, Run, SignatureRecord, Snapshot, TestRunRecord
from cuanta.ports.ledger import Ledger

RUN = "01KFIXTURERUN0000000000000"


def fill(ledger: Ledger) -> None:
    ledger.add_run(
        Run(id=RUN, kind="mandate", engine="claude", status="ok", hu_ref="HU-007", cost_usd=0.9)
    )
    events = [
        LedgerEvent(
            run_id=RUN,
            kind="api_request",
            session_id="s1",
            agent="main",
            model="claude-opus",
            ts="2026-01-05T10:00:00Z",
            input_tokens=4000,
            cache_read_tokens=20000,
            cache_write_tokens=3000,
            output_tokens=800,
            cost_usd=0.4,
        ),
        LedgerEvent(
            run_id=RUN,
            kind="tool_result",
            session_id="s1",
            agent="main",
            tool_name="Read",
            file_path="src/app.py",
            tool_result_bytes=16000,
            ts="2026-01-05T10:00:05Z",
        ),
        LedgerEvent(
            run_id=RUN,
            kind="api_request",
            session_id="s1",
            agent="main",
            model="claude-opus",
            ts="2026-01-05T10:01:00Z",
            input_tokens=1000,
            cache_read_tokens=26000,
            output_tokens=500,
            cost_usd=0.2,
        ),
        LedgerEvent(
            run_id=RUN,
            kind="tool_result",
            session_id="s1",
            agent="main",
            tool_name="Read",
            file_path="src/app.py",
            tool_result_bytes=16000,
            ts="2026-01-05T10:01:05Z",
        ),
        LedgerEvent(
            run_id=RUN,
            kind="tool_result",
            session_id="s1",
            agent="tester",
            tool_name="Bash",
            command="pytest -vv",
            tool_result_bytes=60000,
            ts="2026-01-05T10:02:00Z",
        ),
        LedgerEvent(
            run_id=RUN,
            kind="api_request",
            session_id="s1",
            agent="tester",
            model="claude-sonnet",
            ts="2026-01-05T10:03:00Z",
            input_tokens=2000,
            cache_read_tokens=10000,
            cache_write_tokens=500,
            output_tokens=300,
            cost_usd=0.2,
        ),
        LedgerEvent(
            run_id=RUN,
            kind="tool_result",
            session_id="s1",
            agent="main",
            tool_name="Edit",
            file_path="src/app.py",
            tool_input_bytes=2000,
            ts="2026-01-05T10:04:00Z",
        ),
        LedgerEvent(
            run_id=RUN,
            kind="api_request",
            session_id="s1",
            agent="main",
            model="claude-opus",
            ts="2026-01-05T10:05:00Z",
            input_tokens=500,
            cache_read_tokens=30000,
            output_tokens=200,
            cost_usd=0.1,
        ),
    ]
    ledger.add_events(events)
    ledger.add_snapshots(
        [Snapshot(RUN, "start", "src/app.py", "a"), Snapshot(RUN, "end", "src/app.py", "b")]
    )
    ledger.add_test_run(
        TestRunRecord(
            "T1", RUN, "pytest", "pytest", "red", 1, 1, 0, 0, 0.1, "cap:x", "2026-01-05T10:02:00Z"
        ),
        [SignatureRecord("T1", "sig", "E", "E", "t", "", 1)],
    )
    ledger.add_test_run(
        TestRunRecord(
            "T2", RUN, "pytest", "pytest", "green", 2, 0, 0, 0, 0.1, "cap:y", "2026-01-05T10:06:00Z"
        ),
        [],
    )
