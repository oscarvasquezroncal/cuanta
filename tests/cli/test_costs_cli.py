from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.domain.ledger import Run
from tests.fakes import FakeRunner
from tests.support import invoke


def _run(run_id: str, task_type: str, cost: float | None, **changes: str | float) -> Run:
    values: dict[str, str | float | None] = {
        "engine": "claude",
        "started_at": "2025-12-20T10:00:00Z",
        "ended_at": "2025-12-20T10:02:00Z",
        "status": "ok",
        "cost_usd": cost,
        "task_type": task_type,
        "cost_source": "reported",
        **changes,
    }
    return Run(
        run_id,
        str(changes.get("kind", "mandate")),
        str(values["engine"]),
        started_at=str(values["started_at"]),
        ended_at=str(values["ended_at"]),
        status=str(values["status"]),
        cost_usd=cost,
        task_type=task_type,
        cost_source="estimated" if changes.get("cost_source") == "estimated" else "reported",
        parent_id=str(changes.get("parent_id", "")),
        estimate_low=0.2,
        estimate_high=0.4,
        estimate_source="history",
        estimate_samples=3,
        cap_usd=1.0,
    )


def _seed(root: Path, *runs: Run) -> None:
    (root / ".cuanta").mkdir(parents=True, exist_ok=True)
    ledger = SqliteLedger(root / ".cuanta" / "ledger.db")
    try:
        for run in runs:
            ledger.add_run(run)
    finally:
        ledger.close()


def _row(payload: dict[str, list[dict[str, object]]], table: str, key: str) -> dict[str, object]:
    return next(row for row in payload[table] if row["key"] == key)


def test_costs_without_a_ledger_show_empty_rows_and_create_nothing(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    result = invoke(["costs", "--json", "--project", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["since"] == "2025-12-02T00:00:00Z"
    fix = _row(payload, "by_type", "fix")
    assert fix["runs"] == 0 and fix["spend_usd"] is None
    assert not (tmp_path / ".cuanta").exists()
    plain = invoke(["costs", "--project", str(tmp_path)])
    assert "no mandates in this window" in plain.stdout


def test_costs_count_every_attempt_and_never_show_unknown_as_zero(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    _seed(
        tmp_path,
        _run("A", "bug", 0.3),
        _run("B", "bug", None),
        _run("C", "bug", 0.9, cost_source="estimated"),
        _run("D", "feature", 1.0, kind="cross"),
        _run("E", "", 0.5, kind="cross", parent_id="D"),
        _run("F", "investigation", 0.1, started_at="2025-10-01T00:00:00Z"),
    )
    assert invoke(["runs", "accept", "A", "--project", str(tmp_path)]).exit_code == 0
    result = invoke(["costs", "--json", "--project", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    fix = _row(payload, "by_type", "fix")
    assert fix["runs"] == 3 and fix["accepted"] == 1
    assert fix["spend_usd"] == 1.2 and fix["spend_lower_bound"] is True
    assert fix["runs_without_cost"] == 1 and fix["runs_with_estimated_cost"] == 1
    assert fix["cost_per_accepted_usd"] == 1.2 and fix["cost_per_accepted_lower_bound"] is True
    assert fix["cost_per_accepted_includes_estimated"] is True
    assert fix["median_cost_includes_estimated"] is True
    feature = _row(payload, "by_type", "feature")
    assert feature["spend_usd"] == 1.5 and feature["cost_per_accepted_usd"] is None
    assert _row(payload, "by_engine_mix", "cross-engine")["runs"] == 1
    assert _row(payload, "by_type", "investigation")["runs"] == 0
    older = json.loads(
        invoke(["costs", "--json", "--since", "2025-09-01", "--project", str(tmp_path)]).stdout
    )
    assert _row(older, "by_type", "investigation")["runs"] == 1
    table = invoke(["costs", "--project", str(tmp_path)]).stdout
    assert "≥$1.20*" in table
    bad = invoke(["costs", "--since", "yesterday", "--project", str(tmp_path)])
    assert bad.exit_code == 1


def test_runs_accept_and_reject_record_outcomes_once(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    _seed(
        tmp_path,
        _run("01JA", "bug", 0.3),
        _run("01JB", "bug", 0.5),
        _run("01JC", "feature", 1.0, kind="cross"),
        _run("01JD", "", 0.2, kind="cross", parent_id="01JC"),
    )
    accepted = invoke(["runs", "accept", "01JA", "--json", "--project", str(tmp_path)])
    assert accepted.exit_code == 0, accepted.stdout
    assert json.loads(accepted.stdout)["outcome"] == "accepted"
    again = invoke(["runs", "reject", "01JA", "--project", str(tmp_path)])
    assert again.exit_code == 1
    assert "already accepted" in again.stdout + again.stderr
    rejected = invoke(
        [
            "runs",
            "reject",
            "01JB",
            "--reason",
            "fixed the wrong function",
            "--json",
            "--project",
            str(tmp_path),
        ]
    )
    assert json.loads(rejected.stdout)["reason"] == "fixed the wrong function"
    _seed(tmp_path, _run("01JF", "feature", 1.0, kind="cross"))
    _seed(tmp_path, _run("01JG", "", 0.2, kind="cross", parent_id="01JF"))
    hinted = invoke(["runs", "accept", "01JG", "--project", str(tmp_path)])
    assert "01JG is a role of the cross-engine run 01JF" in hinted.stdout
    role = invoke(["runs", "accept", "01JD", "--json", "--project", str(tmp_path)])
    assert json.loads(role.stdout)["run_id"] == "01JC"
    shown = json.loads(
        invoke(["runs", "show", "01JB", "--json", "--project", str(tmp_path)]).stdout
    )
    assert shown["outcome"] == "rejected"
    assert shown["outcome_reason"] == "fixed the wrong function"
    assert shown["estimate"]["source"] == "history"
    assert shown["estimate"]["error"] == pytest.approx(0.25)
    assert shown["cap_usd"] == 1.0
    cross = json.loads(
        invoke(["runs", "show", "01JC", "--json", "--project", str(tmp_path)]).stdout
    )
    assert cross["actual_usd"] == 1.2
    listed = json.loads(invoke(["runs", "list", "--json", "--project", str(tmp_path)]).stdout)
    outcomes = {row["id"]: row["outcome"] for row in listed["runs"]}
    assert {key: outcomes[key] for key in ("01JA", "01JB", "01JC", "01JD")} == {
        "01JA": "accepted",
        "01JB": "rejected",
        "01JC": "accepted",
        "01JD": None,
    }
    _seed(tmp_path, _run("01JE", "bug", 0.1))
    fresh = json.loads(
        invoke(["runs", "show", "01JE", "--json", "--project", str(tmp_path)]).stdout
    )
    assert fresh["outcome"] == "pending"
    plain_role = invoke(["runs", "accept", "01JD", "--project", str(tmp_path)])
    assert plain_role.exit_code == 1
    assert "already accepted" in plain_role.stdout + plain_role.stderr
    text = invoke(["runs", "show", "01JB", "--project", str(tmp_path)]).stdout
    assert "rejected" in text and "fixed the wrong function" in text
    assert "$0.2000–$0.4000 · from history (n=3)" in text
