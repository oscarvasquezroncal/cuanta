from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.costs import CostsQuery
from cuanta.application.results import ResultQuery
from cuanta.application.run_reports import RunReports
from cuanta.application.trials import TRIAL_FILE, Trial, TrialChange, trial_payload
from cuanta.domain.calibration import ForecastActual
from cuanta.domain.governor_report import (
    HOOKS,
    BlockedCalls,
    GovernorEntry,
    GovernorSummary,
    parse_governor,
)
from cuanta.domain.ledger import Forecast, LedgerEvent, Run
from cuanta.domain.real_costs import attempts
from cuanta.domain.run_metrics import (
    BUCKETS,
    Reactions,
    RunMetrics,
    metrics_payload,
    run_metrics,
    senior_input,
)
from cuanta.domain.sandbox import ChangeKind, trial_folder
from cuanta.domain.scout_report import ScoutSummary

NOW = "2026-09-29T12:00:00+00:00"
RUN = Run(
    "R",
    "mandate",
    "claude",
    model="claude-sonnet-5",
    started_at="2026-09-28T10:00:00+00:00",
    ended_at="2026-09-28T10:05:00+00:00",
    status="ok",
    cost_usd=0.40,
    task_type="feature",
    cost_source="reported",
    outcome="accepted",
    cap_usd=2.0,
)
FORECAST = Forecast(
    run_id="R",
    created_at="2026-09-28T09:59:00+00:00",
    provider="claude",
    task_type="feature",
    depth="normal",
    shape="pipeline",
    p50_usd=0.30,
    p90_usd=0.48,
    cap_usd=1.0,
    verdict="comfortable",
    buckets=json.dumps(
        {"start": 20_000, "exploration": 4_000, "writing": 1_200, "verification": 2_000}
    ),
)
EVENTS = (
    LedgerEvent(
        run_id="R",
        kind="api_request",
        ts="2026-09-28T10:00:00+00:00",
        input_tokens=1_000,
        cache_read_tokens=15_000,
        cache_write_tokens=5_000,
        output_tokens=300,
        id=1,
    ),
    LedgerEvent(
        run_id="R",
        kind="tool_result",
        tool_name="Read",
        ts="2026-09-28T10:00:05+00:00",
        id=2,
    ),
    LedgerEvent(
        run_id="R",
        kind="api_request",
        ts="2026-09-28T10:00:10+00:00",
        input_tokens=200,
        cache_read_tokens=21_000,
        cache_write_tokens=3_000,
        output_tokens=150,
        id=3,
    ),
    LedgerEvent(
        run_id="R",
        kind="tool_result",
        tool_name="Edit",
        ts="2026-09-28T10:00:15+00:00",
        id=4,
    ),
    LedgerEvent(
        run_id="R",
        kind="api_request",
        ts="2026-09-28T10:00:20+00:00",
        input_tokens=50,
        cache_read_tokens=24_000,
        cache_write_tokens=100,
        output_tokens=900,
        id=5,
    ),
    LedgerEvent(
        run_id="R",
        kind="result_usage",
        ts="2026-09-28T10:00:21+00:00",
        input_tokens=1_250,
        output_tokens=1_350,
        id=6,
    ),
)


def entry(kind: str, sent: bool, saved: float | None, outcome: str = "") -> GovernorEntry:
    return GovernorEntry(kind, "senior", "share", 12.0, "R", sent, 0.2, 0.3, False, saved, outcome)


GOVERNOR = GovernorSummary(
    (
        entry("finish_now", True, None),
        entry("codex_stop", True, 0.12, "resumed"),
        entry("codex_stop", False, 0.5),
        entry("rotate", True, 0.2, "rotated"),
        entry("rotate", True, None, "skipped"),
    ),
    BlockedCalls(reads=2, searches=1, tokens=9_000),
    (("senior", HOOKS),),
)
SCOUT = ScoutSummary(mode="launch", tokens=4_800, budget=6_000)


def metrics_for(
    run: Run = RUN,
    events: tuple[LedgerEvent, ...] = EVENTS,
    forecast: Forecast | None = FORECAST,
    governor: GovernorSummary = GOVERNOR,
    scout: ScoutSummary = SCOUT,
    lines: int | None = 101,
) -> RunMetrics:
    found = attempts((run,), NOW)
    return run_metrics(found[0] if found else None, (), events, forecast, governor, scout, lines)


def test_forecast_meets_actual_by_bucket_with_the_margin_used() -> None:
    metrics = metrics_for()
    assert metrics.shown and metrics.provider == "claude" and metrics.task_type == "feature"
    assert [item.name for item in metrics.buckets] == list(BUCKETS)
    pairs = {item.name: (item.forecast, item.actual) for item in metrics.buckets}
    assert pairs["start"] == (20_000, 21_000)
    assert pairs["exploration"] == (4_000, 3_350)
    assert pairs["writing"] == (1_200, 1_350)
    assert pairs["verification"] == (2_000, None)
    assert pairs["handoff"] == (None, 0)
    assert metrics.cap_usd == 1.0 and metrics.cap_used == pytest.approx(0.4)
    assert metrics.p90_left_usd == pytest.approx(0.08)


def test_accepted_changes_price_their_cost_and_their_changed_lines() -> None:
    metrics = metrics_for()
    assert metrics.per_accepted_usd == pytest.approx(0.40)
    assert metrics.total_tokens == 70_700
    assert metrics.tokens_per_line == pytest.approx(700.0)
    pending = metrics_for(replace(RUN, outcome=""))
    assert pending.per_accepted_usd is None and pending.tokens_per_line is None
    rejected = metrics_for(replace(RUN, outcome="rejected"))
    assert rejected.per_accepted_usd is None and rejected.tokens_per_line is None
    unknown = metrics_for(replace(RUN, cost_usd=0.0, cost_source="unknown"))
    assert unknown.actual_usd is None and unknown.per_accepted_usd is None
    assert unknown.cap_used is None and unknown.p90_left_usd is None
    in_place = metrics_for(lines=None)
    assert in_place.per_accepted_usd == pytest.approx(0.40) and in_place.tokens_per_line is None
    assert metrics_for(lines=0).tokens_per_line is None


def test_blocked_reads_finishes_and_rotations_come_from_the_governor() -> None:
    metrics = metrics_for()
    assert metrics.blocked is not None
    assert (metrics.blocked.reads, metrics.blocked.tokens) == (2, 9_000)
    assert metrics.finishes is not None and metrics.finishes.count == 2
    assert metrics.finishes.saved_usd == pytest.approx(0.12)
    assert metrics.rotations is not None and metrics.rotations.count == 1
    assert metrics.rotations.saved_usd == pytest.approx(0.2)
    quiet = metrics_for(governor=GovernorSummary((), BlockedCalls(), (("senior", HOOKS),)))
    assert quiet.blocked == BlockedCalls()
    assert quiet.finishes is not None and quiet.finishes.count == 0
    assert quiet.finishes.saved_usd is None
    ungoverned = metrics_for(governor=GovernorSummary((), BlockedCalls(), ()))
    assert ungoverned.blocked is None
    assert ungoverned.finishes is None and ungoverned.rotations is None


def test_a_governed_run_without_reactions_counts_zero_finishes() -> None:
    quiet = parse_governor({"reactions": [], "read_discipline": {}}, BlockedCalls())
    governed = metrics_for(governor=quiet)
    assert governed.finishes == Reactions(0, None) and governed.rotations == Reactions(0, None)
    assert governed.blocked is None
    hooked = parse_governor({"reactions": [], "read_discipline": {}, HOOKS: True}, BlockedCalls())
    assert metrics_for(governor=hooked).blocked == BlockedCalls()
    missing = metrics_for(governor=parse_governor(None, BlockedCalls()))
    assert missing.finishes is None and missing.blocked is None
    payload = metrics_payload(governed)
    assert payload["finishes"] == {"count": 0, "saved_usd": None}


def test_a_forecast_without_a_cap_keeps_the_runs_cap() -> None:
    metrics = metrics_for(forecast=replace(FORECAST, cap_usd=None))
    assert metrics.cap_usd == 2.0 and metrics.cap_used == pytest.approx(0.2)


def test_warm_shares_read_the_first_requests_and_every_request() -> None:
    metrics = metrics_for()
    assert metrics.first_warm_share == pytest.approx(15_000 / 21_000)
    assert metrics.warm_share == pytest.approx(60_000 / 69_350)


def test_a_codex_run_keeps_unknown_buckets_and_first_requests_na() -> None:
    codex = replace(RUN, engine="codex", cost_source="estimated", outcome="")
    usage = (
        LedgerEvent(
            run_id="R",
            kind="result_usage",
            ts="2026-09-28T10:04:00+00:00",
            input_tokens=40_000,
            cache_read_tokens=160_000,
            output_tokens=2_000,
            id=9,
        ),
    )
    metrics = metrics_for(codex, usage, None, GovernorSummary(), ScoutSummary(), None)
    assert metrics.provider == "codex" and metrics.estimated
    assert all(item.forecast is None and item.actual is None for item in metrics.buckets)
    assert metrics.p50_usd is None and metrics.p90_left_usd is None
    assert metrics.cap_usd == 2.0 and metrics.cap_used == pytest.approx(0.2)
    assert metrics.first_warm_share is None
    assert metrics.warm_share == pytest.approx(0.8)
    assert metrics.total_tokens == 202_000
    assert metrics.pack_tokens is None and metrics.senior_input_tokens is None
    assert metrics.blocked is None and metrics.finishes is None


def test_without_telemetry_every_token_metric_stays_na() -> None:
    metrics = metrics_for(events=(), lines=None)
    assert metrics.total_tokens is None and metrics.warm_share is None
    assert metrics.first_warm_share is None
    assert all(item.actual is None for item in metrics.buckets)


def test_the_scout_pack_and_the_senior_input_are_measured_per_team() -> None:
    root = Run("T", "cross", "claude", scope="scout", started_at="2026-09-28T10:00:00+00:00")
    senior = Run("T2", "cross", "claude", scope="senior", parent_id="T")
    usage = (
        LedgerEvent(run_id="T", kind="api_request", input_tokens=900, cache_read_tokens=1, id=1),
        LedgerEvent(
            run_id="T2",
            kind="api_request",
            input_tokens=100,
            cache_read_tokens=30_000,
            cache_write_tokens=2_000,
            output_tokens=700,
            id=2,
        ),
    )
    assert senior_input(usage, (root, senior)) == 32_100
    native = (
        LedgerEvent(run_id="N", kind="api_request", agent="main", input_tokens=5_000, id=3),
        LedgerEvent(
            run_id="N", kind="api_request", agent="frontend-senior", input_tokens=7_000, id=4
        ),
    )
    assert senior_input(native, (Run("N", "mandate"),)) == 7_000
    assert senior_input(native[:1], (Run("N", "mandate"),)) is None
    assert metrics_for().pack_tokens == 4_800


def test_runs_that_are_not_attempts_show_no_metrics() -> None:
    child = Run("C", "cross", "claude", parent_id="T", status="ok")
    assert not metrics_for(child).shown
    running = replace(RUN, status="running", started_at="2026-09-29T11:00:00+00:00", ended_at="")
    assert not metrics_for(running).shown
    assert not RunMetrics().shown


def test_the_payload_keeps_unknown_values_null() -> None:
    payload = metrics_payload(metrics_for())
    assert payload["forecast"] == {
        "p50_usd": 0.30,
        "p90_usd": 0.48,
        "cap_usd": 1.0,
        "buckets": {
            "start": {"forecast_tokens": 20_000, "actual_tokens": 21_000},
            "exploration": {"forecast_tokens": 4_000, "actual_tokens": 3_350},
            "writing": {"forecast_tokens": 1_200, "actual_tokens": 1_350},
            "verification": {"forecast_tokens": 2_000, "actual_tokens": None},
            "handoff": {"forecast_tokens": None, "actual_tokens": 0},
        },
    }
    assert payload["cap_used"] == 0.4 and payload["p90_minus_actual_usd"] == 0.08
    assert payload["cost_per_accepted_usd"] == 0.40
    assert payload["tokens_per_accepted_line"] == 700.0
    assert payload["blocked"] == {"reads": 2, "calls": 3, "tokens_estimate": 9_000}
    assert payload["finishes"] == {"count": 2, "saved_usd": 0.12}
    assert payload["rotations"] == {"count": 1, "saved_usd": 0.2}
    assert payload["warm_share"] == {"first_requests": 0.7143, "overall": 0.8652}
    assert payload["scout_pack_tokens"] == 4_800
    empty = metrics_payload(metrics_for(replace(RUN, outcome="")))
    assert empty["outcome"] is None and empty["cost_per_accepted_usd"] is None
    ungoverned = metrics_payload(metrics_for(governor=GovernorSummary()))
    assert ungoverned["blocked"] == {"reads": None, "calls": None, "tokens_estimate": None}
    assert ungoverned["finishes"] == {"count": None, "saved_usd": None}


def seeded_ledger(ledger: MemoryLedger | None = None) -> MemoryLedger:
    ledger = ledger if ledger is not None else MemoryLedger()
    ledger.add_run(RUN)
    ledger.add_run(Run("C", "cross", "claude", parent_id="T", status="ok"))
    ledger.add_forecast(FORECAST)
    ledger.add_events(EVENTS)
    return ledger


class OneForecastLedger(MemoryLedger):
    def forecasts(
        self, provider: str = "", task_type: str = "", limit: int = 0
    ) -> tuple[ForecastActual, ...]:
        raise AssertionError("the Result screen scanned every forecast")


def test_the_result_view_carries_the_run_metrics(tmp_path: Path) -> None:
    ledger = seeded_ledger(OneForecastLedger())
    query = ResultQuery(LocalWorkspace(tmp_path), ledger, date.today, lambda: NOW)
    view = query.load("R")
    assert view is not None and view.metrics.shown
    assert view.metrics.p50_usd == 0.30 and view.metrics.per_accepted_usd == pytest.approx(0.4)
    assert view.metrics.changed_lines is None and view.metrics.tokens_per_line is None
    child = query.load("C")
    assert child is not None and not child.metrics.shown


def write_trial(root: Path) -> None:
    trial = Trial(
        "R",
        "feature",
        "add a cart",
        "claude",
        "feat: add a cart",
        (
            TrialChange("src/cart.ts", ChangeKind.ADDED, None, "a" * 64, added=90),
            TrialChange("src/app.ts", ChangeKind.MODIFIED, "b" * 64, "c" * 64, 6, 5),
        ),
        "",
        False,
    )
    LocalWorkspace(root).write_text(
        f"{trial_folder('R')}/{TRIAL_FILE}", json.dumps(trial_payload(trial))
    )


def test_costs_query_lists_metrics_per_attempt_from_meta_and_trials(tmp_path: Path) -> None:
    ledger = seeded_ledger()
    RunReports(LocalWorkspace(tmp_path)).save_meta(
        "R",
        {
            "governor": {"reactions": [], "read_discipline": {"senior": HOOKS}},
            "scout": {"mode": "native", "tokens": 5_100, "budget": 6_000},
        },
    )
    write_trial(tmp_path)
    query = CostsQuery(
        lambda: ledger, lambda: True, lambda: NOW, workspace=LocalWorkspace(tmp_path)
    )
    found = query.metrics()
    assert [item.run_id for item in found] == ["R"]
    metrics = found[0]
    assert metrics.changed_lines == 101 and metrics.tokens_per_line == pytest.approx(700.0)
    assert metrics.pack_tokens == 5_100
    assert metrics.blocked == BlockedCalls()
    assert metrics.finishes is not None and metrics.finishes.count == 0
    assert metrics.p90_usd == 0.48
    assert query.metrics("2026-09-29T00:00:00Z") == ()


def test_metrics_need_the_state_workspace() -> None:
    query = CostsQuery(MemoryLedger, lambda: True, lambda: NOW)
    with pytest.raises(ValueError, match="state workspace"):
        query.metrics()
    missing = CostsQuery(MemoryLedger, lambda: False, lambda: NOW, workspace=LocalWorkspace(Path()))
    assert missing.metrics() == ()
