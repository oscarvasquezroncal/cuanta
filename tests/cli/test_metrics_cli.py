from __future__ import annotations

import json
from pathlib import Path

from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.run_reports import RunReports
from cuanta.application.trials import TRIAL_FILE, Trial, TrialChange, trial_payload
from cuanta.domain.ledger import Forecast, LedgerEvent, Run
from cuanta.domain.sandbox import ChangeKind, trial_folder
from tests.fakes import FakeRunner
from tests.support import invoke


def attempt(run_id: str, started: str, cost: float, **values: str) -> Run:
    return Run(
        run_id,
        "mandate",
        values.get("engine", "claude"),
        started_at=started,
        ended_at=started.replace("T10:00", "T10:04"),
        status="ok",
        cost_usd=cost,
        task_type=values.get("task_type", "feature"),
        cost_source="estimated" if values.get("engine") == "codex" else "reported",
        outcome=values.get("outcome", ""),
        cap_usd=1.0,
    )


def seed(root: Path) -> None:
    (root / ".cuanta").mkdir(parents=True, exist_ok=True)
    ledger = SqliteLedger(root / ".cuanta" / "ledger.db")
    try:
        ledger.add_run(attempt("A", "2025-12-20T10:00:00Z", 0.4, outcome="accepted"))
        ledger.add_run(attempt("B", "2025-12-21T10:00:00Z", 0.9, engine="codex", task_type="bug"))
        ledger.add_run(attempt("C", "2025-10-01T10:00:00Z", 0.3, outcome="accepted"))
        ledger.add_forecast(
            Forecast(
                "A",
                "2025-12-20T09:59:00Z",
                "claude",
                "feature",
                "normal",
                "pipeline",
                0.3,
                0.5,
                0.8,
                "comfortable",
                buckets=json.dumps({"start": 18_000, "writing": 900}),
            )
        )
        ledger.add_events(
            (
                LedgerEvent(
                    run_id="A",
                    source="claude_code",
                    kind="api_request",
                    ts="2025-12-20T10:00:01Z",
                    input_tokens=1_000,
                    cache_read_tokens=16_000,
                    cache_write_tokens=3_000,
                    output_tokens=1_000,
                ),
            )
        )
    finally:
        ledger.close()
    workspace = LocalWorkspace(root)
    RunReports(workspace).save_meta(
        "A", {"governor": {"reactions": [], "read_discipline": {"senior": "hooks"}}}
    )
    trial = Trial(
        "A",
        "feature",
        "add a cart",
        "claude",
        "feat: add a cart",
        (TrialChange("src/cart.ts", ChangeKind.ADDED, None, "a" * 64, added=50),),
        "",
        False,
    )
    workspace.write_text(f"{trial_folder('A')}/{TRIAL_FILE}", json.dumps(trial_payload(trial)))


def test_costs_metrics_list_each_attempt_with_unknowns_as_null(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    seed(tmp_path)
    result = invoke(["costs", "--metrics", "--json", "--project", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    rows = json.loads(result.stdout)["metrics"]
    assert [row["run_id"] for row in rows] == ["B", "A"]
    codex, accepted = rows
    assert accepted["forecast"]["p50_usd"] == 0.3 and accepted["cap_used"] == 0.5
    assert accepted["p90_minus_actual_usd"] == 0.1
    assert accepted["forecast"]["buckets"]["start"] == {
        "forecast_tokens": 18_000,
        "actual_tokens": 20_000,
    }
    assert accepted["cost_per_accepted_usd"] == 0.4
    assert accepted["tokens_per_accepted_line"] == 420.0
    assert accepted["blocked"] == {"reads": 0, "calls": 0, "tokens_estimate": 0}
    assert accepted["warm_share"] == {"first_requests": 0.8, "overall": 0.8}
    assert codex["provider"] == "codex" and codex["actual_estimated"] is True
    assert codex["forecast"]["p50_usd"] is None and codex["cost_per_accepted_usd"] is None
    assert codex["total_tokens"] is None and codex["warm_share"]["overall"] is None
    assert codex["blocked"]["reads"] is None and codex["finishes"]["count"] is None
    plain = invoke(["costs", "--metrics", "--project", str(tmp_path)])
    assert plain.exit_code == 0, plain.stdout
    assert "per-run cost since 2025-12-02 UTC" in plain.stdout
    assert "per-run exploration and cache" in plain.stdout
    assert "n/a" in plain.stdout


def test_costs_without_metrics_keep_their_output(tmp_path: Path, fake_runner: FakeRunner) -> None:
    seed(tmp_path)
    payload = json.loads(invoke(["costs", "--json", "--project", str(tmp_path)]).stdout)
    assert "metrics" not in payload
    assert "per-run" not in invoke(["costs", "--project", str(tmp_path)]).stdout


def test_spectrum_trend_shows_cost_per_accepted_change(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    seed(tmp_path)
    result = invoke(["spectrum", "--trend", "--json", "--project", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    trend = json.loads(result.stdout)["trend"]
    assert trend["limit"] == 20 and trend["runs"] == 3
    feature = next(row for row in trend["rows"] if row["task_type"] == "feature")
    assert feature["provider"] == "claude" and feature["series"] == [0.3, 0.35]
    fix = next(row for row in trend["rows"] if row["task_type"] == "fix")
    assert fix["cost_per_accepted_usd"] is None and fix["series"] == [None]
    last = json.loads(
        invoke(["spectrum", "--trend", "--last", "1", "--json", "--project", str(tmp_path)]).stdout
    )["trend"]
    assert last["limit"] == 1 and last["runs"] == 2
    plain = invoke(["spectrum", "--trend", "--plain", "--project", str(tmp_path)])
    assert plain.exit_code == 0, plain.stdout
    assert "cost per accepted change, last 20 runs by provider and type" in plain.stdout
    assert "$0.35" in plain.stdout


def test_spectrum_without_trend_keeps_its_payload(tmp_path: Path, fake_runner: FakeRunner) -> None:
    seed(tmp_path)
    assert "trend" not in json.loads(
        invoke(["spectrum", "--json", "--project", str(tmp_path)]).stdout
    )
    refused = invoke(["spectrum", "--last", "3", "--project", str(tmp_path)])
    assert refused.exit_code == 1
    assert "--last needs --trend" in refused.stdout + refused.stderr
