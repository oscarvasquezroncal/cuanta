from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.application.timing import PhaseRecorder
from cuanta.domain.ledger import LedgerEvent, Run
from cuanta.domain.time_anatomy import PHASES, analyze_time
from cuanta.ports.ledger import EventQuery


class ManualClock:
    def __init__(self) -> None:
        self.elapsed = 0.0

    def now_iso(self) -> str:
        return "2026-01-01T00:00:00Z"

    def now_ms(self) -> int:
        return 1_767_225_600_000 + round(self.elapsed * 1000)

    def monotonic(self) -> float:
        return self.elapsed

    def sleep(self, seconds: float) -> None:
        self.elapsed += seconds


def test_overlap_is_preserved_without_calling_service_totals_wall_time() -> None:
    run = Run("R", "mandate", started_at="2026-01-01T00:00:00Z", ended_at="2026-01-01T00:00:10Z")
    events = [
        LedgerEvent(
            run_id="R", kind="phase_timing", raw='{"phase":"run_wall","duration_ms":10000}'
        ),
        LedgerEvent(
            kind="api_request", agent="senior", model="opus", duration_ms=8000, ttft_ms=500
        ),
        LedgerEvent(kind="api_request", agent="analyst", model="sonnet", duration_ms=7000),
        LedgerEvent(kind="api_request", query_source="generate_session_title", duration_ms=1000),
        LedgerEvent(kind="tool_result", agent="senior", tool_name="Read", duration_ms=50),
        LedgerEvent(kind="tool_result", agent="senior", tool_name="Bash", duration_ms=9000),
    ]
    report = analyze_time(run, events)
    phases = {row.phase: row for row in report.phases}
    assert report.wall_seconds == 10
    assert phases["api_requests"].seconds == 15
    assert phases["session_title"].seconds == 1
    assert phases["tool:Read"].seconds == 0.05
    assert phases["tools"].seconds == 9.05
    assert report.requests[0].ttft_seconds == 0.5
    assert report.requests[1].ttft_seconds is None
    assert {row.role for row in report.roles} == {"senior", "analyst", "main"}


def test_missing_durations_stay_unknown_while_explicit_zero_is_measured() -> None:
    report = analyze_time(
        Run("R", "mandate"),
        [
            LedgerEvent(kind="api_request", raw='{"attributes":{"duration_ms":0,"ttft_ms":0}}'),
            LedgerEvent(kind="api_request"),
            LedgerEvent(kind="tool_result", tool_name="Edit"),
        ],
    )
    phases = {row.phase: row for row in report.phases}
    assert phases["api_requests"].seconds is None
    assert phases["api_requests"].samples == 2
    assert phases["tool:Edit"].seconds is None
    assert phases["forecast_plan"].seconds is None
    assert report.requests[0].duration_seconds == 0
    assert report.requests[0].ttft_seconds == 0
    assert report.wall_seconds is None


def test_hook_total_duration_survives_the_zero_legacy_column() -> None:
    event = LedgerEvent(
        kind="hook_execution_complete",
        raw=json.dumps(
            {"attributes": [{"key": "total_duration_ms", "value": {"stringValue": "235"}}]}
        ),
    )
    report = analyze_time(Run("R", "mandate"), [event])
    assert next(row for row in report.phases if row.phase == "hooks").seconds == 0.235


def test_tool_durations_prefer_provider_and_skip_pending_and_delegation_totals() -> None:
    provider = LedgerEvent(
        run_id="R",
        source="claude_code",
        kind="tool_result",
        tool_name="mcp_tool",
        tool_use_id="provider-1",
        duration_ms=2000,
        raw=json.dumps(
            {
                "attributes": {
                    "tool_parameters": json.dumps(
                        {"mcp_server_name": "cuanta", "mcp_tool_name": "card"}
                    )
                }
            }
        ),
    )
    owned = LedgerEvent(
        run_id="R",
        source="cuanta_mcp",
        kind="index_call",
        tool_name="card",
        tool_use_id="server-1",
        duration_ms=1900,
    )
    pending = LedgerEvent(
        run_id="R", kind="mcp_tool_call", tool_name="mcp__cuanta__card", raw='{"status":"started"}'
    )
    delegation = LedgerEvent(run_id="R", kind="tool_result", tool_name="Agent", duration_ms=10000)
    report = analyze_time(Run("R", "mandate"), [provider, owned, pending, provider, delegation])
    phases = {row.phase: row for row in report.phases}
    assert phases["tools"].seconds == 2
    assert phases["tools"].samples == 1
    assert phases["tool:mcp__cuanta__card"].seconds == 2
    assert phases["tool:Agent"].seconds == 10
    fallback = analyze_time(Run("R", "mandate"), [owned])
    assert next(row for row in fallback.phases if row.phase == "tools").seconds == 1.9


def test_child_run_role_labels_do_not_collapse_into_main() -> None:
    report = analyze_time(
        Run("A", "cross", started_at="2026-01-01T00:00:00Z", ended_at="2026-01-01T00:00:01Z"),
        [
            LedgerEvent(run_id="A", kind="run_role", agent="analyst"),
            LedgerEvent(run_id="B", kind="run_role", agent="senior"),
            LedgerEvent(run_id="A", kind="api_request", agent="main", duration_ms=1000),
            LedgerEvent(run_id="B", kind="api_request", agent="main", duration_ms=9000),
        ],
    )
    assert {request.role for request in report.requests} == {"analyst", "senior"}
    assert report.wall_seconds is None


def test_legacy_process_dates_do_not_claim_a_complete_lifecycle_wall() -> None:
    run = Run("R", "mandate", started_at="2026-01-01T00:00:00Z", ended_at="2026-01-01T00:01:00Z")
    assert analyze_time(run, []).wall_seconds is None


@pytest.mark.parametrize("raw", ["{", "[]", '{"duration_ms":true}', '{"duration_ms":"NaN"}'])
def test_malformed_observations_never_become_zero(raw: str) -> None:
    report = analyze_time(Run("R", "mandate"), [LedgerEvent(kind="api_request", raw=raw)])
    assert report.requests[0].duration_seconds is None


def test_phase_recorder_persists_precise_durations_and_exception_boundaries(tmp_path: Path) -> None:
    clock = ManualClock()
    ledger = SqliteLedger(tmp_path / "ledger.db")
    run = Run("R", "mandate")
    ledger.add_run(run)
    recorder = PhaseRecorder(clock, ledger)
    with recorder.measure("index_refresh"):
        clock.sleep(0.0004)
    recorder.flush(run.id)

    def fail() -> None:
        with recorder.measure("verification", run.id, "senior"):
            clock.sleep(3.25)
            raise ValueError("failed check")

    with pytest.raises(ValueError, match="failed check"):
        fail()
    recorder.flush(run.id)
    ledger.close()
    reopened = SqliteLedger(tmp_path / "ledger.db")
    events = reopened.events(EventQuery(run_id=run.id))
    report = analyze_time(run, events)
    reopened.close()
    phases = {row.phase: row for row in report.phases}
    assert len(events) == 2
    assert phases["index_refresh"].seconds == pytest.approx(0.0004)
    assert phases["verification"].seconds == pytest.approx(3.25)
    assert next(row for row in report.roles if row.role == "senior")


def test_preview_reset_cannot_leak_its_measurement_into_the_run() -> None:
    clock, ledger = ManualClock(), MemoryLedger()
    recorder = PhaseRecorder(clock, ledger)
    with recorder.measure("forecast_plan"):
        clock.sleep(100)
    recorder.reset()
    recorder.flush("R")
    assert not ledger.events(EventQuery())
    with PhaseRecorder().measure("api_requests"):
        clock.sleep(1)
    recorder.record("run_wall", None, "R")
    assert not ledger.events(EventQuery())


def test_outer_wall_covers_sandbox_lifecycle_but_is_not_summed_with_inner_wall() -> None:
    event = LedgerEvent(
        run_id="R", kind="phase_timing", raw='{"phase":"run_wall","duration_ms":100}'
    )
    report = analyze_time(
        Run("R", "mandate"),
        [event, replace(event, duration_ms=200, raw='{"phase":"run_wall","duration_ms":200}')],
    )
    assert report.wall_seconds == 0.2
    assert analyze_time(Run("", "selection"), [event]).wall_seconds is None
    assert {row.phase for row in report.phases} == set(PHASES)
