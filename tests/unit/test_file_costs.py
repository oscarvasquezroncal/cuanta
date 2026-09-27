from __future__ import annotations

import json
from dataclasses import asdict, replace

import pytest

from cuanta.domain.code_index import IndexHistory, IndexRow
from cuanta.domain.file_costs import FileCostReport, file_cost_report
from cuanta.domain.index_facts import history_prior, history_row
from cuanta.domain.ledger import Run
from cuanta.domain.real_costs import Attempt, attempts

NOW = "2026-01-31T00:00:00Z"
AT = "2026-01-01T00:00:00Z"


def _run(
    identity: str = "R",
    cost: float | None = 2.0,
    source: str = "reported",
    parent: str = "",
    kind: str = "mandate",
    engine: str = "claude",
) -> Run:
    return Run(
        identity,
        kind,
        engine=engine,
        model="sonnet",
        status="ok",
        started_at=AT,
        ended_at=AT,
        cost_usd=cost,
        cost_source="estimated"
        if source == "estimated"
        else "unknown"
        if source == "unknown"
        else "reported",
        parent_id=parent,
        task_type="bug",
    )


def _row(
    path: str = "a.py",
    run: str = "R",
    action: str = "read",
    at: str = AT,
    outcome: str = "",
    retries: int = 0,
    stale: bool = False,
) -> IndexRow:
    return replace(
        history_row(IndexHistory(path, run, "bug", action, at, outcome, retries), "hash"),
        stale=stale,
    )


def _report(rows: tuple[IndexRow, ...], runs: tuple[Run, ...]) -> FileCostReport:
    return file_cost_report(rows, attempts(runs, NOW), NOW)


def test_file_allocation_conserves_known_cost_and_reports_uncovered_attempts() -> None:
    report = _report(
        (_row(), _row("b.py")),
        (_run(), _run("NO_FILES", 3.0), _run("UNPRICED", None)),
    )
    assert [row.known_allocated_usd for row in report.rows] == [1.0, 1.0]
    assert report.known_cost_usd == 5.0
    assert report.allocated_known_usd == 2.0
    assert report.unallocated_known_usd == 3.0
    assert report.known_cost_usd == report.allocated_known_usd + report.unallocated_known_usd
    assert report.attempts == 3
    assert report.known_attempts == 2
    assert report.allocated_attempts == report.unallocated_attempts == report.unknown_attempts == 1
    assert "not measured or causal" in report.heuristic


def test_duplicate_actions_and_rows_do_not_multiply_weights_or_samples() -> None:
    read, edit, cite = _row(), _row(action="edit"), _row(action="cite")
    report = _report((read, read, edit, cite, _row("b.py")), (_run(),))
    assert report.rows[0].prior == history_prior((read, edit, cite), "bug", NOW) == 0.5
    assert report.rows[0].samples == 1
    assert report.rows[0].known_allocated_usd == 1.0
    assert report.rows[1].known_allocated_usd == 1.0


def test_cross_roles_and_root_are_one_attempt_and_one_path_prior() -> None:
    runs = (
        _run("ROOT", 0.0, kind="cross", engine=""),
        _run("CHILD", 3.0, parent="ROOT", kind="cross"),
        _run("CHILD2", 1.0, parent="ROOT", kind="cross", engine="codex"),
    )
    report = _report(
        (
            _row(run="ROOT"),
            _row(run="CHILD"),
            _row(action="edit", run="CHILD2"),
            _row("b.py", "ROOT"),
        ),
        runs,
    )
    assert report.attempts == 1
    assert report.known_cost_usd == 4.0
    assert report.rows[0].samples == 1
    assert report.rows[0].prior == 0.5
    assert all(row.mix == "cross-engine" for row in report.rows)
    assert all(row.allocated_cost_usd == 2.0 for row in report.rows)
    assert "model" not in asdict(report.rows[0])


def test_existing_outcome_retry_and_stale_decay_controls_normalized_share() -> None:
    first = _row(outcome="accepted", retries=1)
    second = _row("b.py", outcome="rejected", stale=True)
    report = _report((first, second), (_run(cost=4.0),))
    assert report.rows[0].prior == history_prior((first,), "bug", NOW) == 0.375
    assert report.rows[1].prior == history_prior((second,), "bug", NOW) == 0.125
    assert [row.known_allocated_usd for row in report.rows] == [3.0, 1.0]
    assert [row.stale_samples for row in report.rows] == [0, 1]


def test_unknown_attempt_never_appears_as_known_zero_or_complete_file_cost() -> None:
    report = _report((_row(), _row(run="UNKNOWN")), (_run(), _run("UNKNOWN", 0, "unknown")))
    (row,) = report.rows
    assert row.allocated_cost_usd is None
    assert row.known_allocated_usd == 2.0
    assert row.samples == 2
    assert row.known_samples == row.unknown_samples == 1
    assert report.known_cost_usd == 2
    assert report.unknown_attempts == 1


def test_priced_zero_remains_a_known_zero_allocation() -> None:
    report = _report((_row(),), (_run(cost=0),))
    assert report.rows[0].allocated_cost_usd == 0
    assert report.known_attempts == report.allocated_attempts == 1
    assert report.unknown_attempts == 0


@pytest.mark.parametrize("now", ["invalid", "", "0001-01-01T00:00:00Z"])
def test_invalid_now_and_underflow_weights_leave_spend_unallocated(now: str) -> None:
    at = "0001-01-01T00:00:00Z" if now == "0001-01-01T00:00:00Z" else AT
    actual_now = "9999-01-01T00:00:00Z" if now == "0001-01-01T00:00:00Z" else now
    report = file_cost_report((_row(at=at),), attempts((_run(),), NOW), actual_now)
    assert report.known_cost_usd == report.unallocated_known_usd == 2
    assert report.allocated_known_usd == 0
    assert report.rows[0].allocated_cost_usd is None
    assert report.rows[0].unallocated_samples == 1


@pytest.mark.parametrize(
    "mutation",
    [
        {"text": "invalid"},
        {"text": "[]"},
        {"provenance": "ledger:OTHER"},
        {"target": "feature"},
        {"relation": "edit"},
        {"path": "../a.py"},
        {"source_hash": ""},
        {"id": "history:other"},
    ],
)
def test_malformed_or_mismatched_history_does_not_claim_allocation(
    mutation: dict[str, str],
) -> None:
    row = _row()
    changed = replace(
        row,
        text=mutation.get("text", row.text),
        provenance=mutation.get("provenance", row.provenance),
        target=mutation.get("target", row.target),
        relation=mutation.get("relation", row.relation),
        path=mutation.get("path", row.path),
        source_hash=mutation.get("source_hash", row.source_hash),
        id=mutation.get("id", row.id),
    )
    report = _report((changed,), (_run(),))
    assert report.rows == ()
    assert report.invalid_rows == 1
    assert report.unallocated_known_usd == 2


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("run_id", "OTHER"),
        ("task_type", "feature"),
        ("path", "other.py"),
        ("at", "invalid"),
        ("retries", True),
        ("retries", -1),
        ("outcome", "fabricated"),
    ],
)
def test_invalid_record_fields_cannot_supply_evidence(key: str, value: object) -> None:
    row = _row()
    raw = json.loads(row.text)
    raw[key] = value
    report = _report((replace(row, text=json.dumps(raw)),), (_run(),))
    assert report.rows == ()
    assert report.invalid_rows == 1


def test_failure_or_outcome_without_an_observed_file_action_does_not_allocate() -> None:
    report = _report((_row(action="failure"), _row(action="outcome")), (_run(),))
    assert report.rows == ()
    assert report.unallocated_known_usd == 2


def test_unknown_run_and_unrelated_task_history_do_not_create_new_cost() -> None:
    unrelated = replace(_row(run="UNRELATED"), target="feature")
    report = _report((_row(run="MISSING"), unrelated), (_run(),))
    assert report.rows == ()
    assert report.unallocated_known_usd == 2


def test_estimates_and_multiple_attempt_priors_are_explicit() -> None:
    report = _report(
        (_row(), _row(run="SECOND", stale=True)),
        (_run(cost=1, source="estimated"), _run("SECOND", 2.0)),
    )
    (row,) = report.rows
    assert row.allocated_cost_usd == 3.0
    assert row.prior == 0.75
    assert row.samples == row.known_samples == 2
    assert row.estimated
    assert row.estimated_samples == row.stale_samples == 1


def test_same_grouped_attempt_passed_twice_is_never_billed_twice() -> None:
    (item,) = attempts((_run(),), NOW)
    report = file_cost_report((_row(),), (item, item), NOW)
    assert report.attempts == 1
    assert report.known_cost_usd == 2


def test_ambiguous_child_ownership_is_ignored_conservatively() -> None:
    first = Attempt(_run(), 2.0, False, None, "fix", "claude", ("R", "CHILD"))
    second = Attempt(_run("SECOND"), 3.0, False, None, "fix", "claude", ("SECOND", "CHILD"))
    report = file_cost_report((_row(run="CHILD"),), (first, second), NOW)
    assert report.rows == ()
    assert report.unallocated_known_usd == 5


def test_output_is_immutable_numeric_and_contains_no_raw_history() -> None:
    report = _report((_row(),), (_run(),))
    exported = asdict(report)
    assert exported["heuristic"] == report.heuristic
    assert all("text" not in row and "provenance" not in row for row in exported["rows"])
    assert "prompt" not in json.dumps(exported)


def test_empty_report_preserves_unavailable_defaults() -> None:
    assert file_cost_report((), (), NOW) == FileCostReport()
