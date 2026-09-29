from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuanta.application.forecast import PlannedForecast
from cuanta.bootstrap import Container
from tests.cli.test_engine_guarantees import codex_ready, cross_args, forge, mandate_args
from tests.fakes import FakeRunner
from tests.support import invoke

ENVELOPE_KEYS = {
    "p50_usd",
    "p90_usd",
    "margin_usd",
    "cap_usd",
    "verdict",
    "buckets",
    "roles",
    "suggestions",
    "cache",
    "source",
    "lines",
}


def test_the_claude_dry_run_shows_the_forecast_line_and_an_envelope_object(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    result = invoke(cross_args(tmp_path, "--route", "fixed", "--dry-run", "--json"))
    assert result.exit_code == 0, result.stdout
    envelope = json.loads(result.stdout)["envelope"]
    assert set(envelope) >= ENVELOPE_KEYS
    assert (envelope["provider"], envelope["shape"], envelope["task_type"]) == (
        "claude",
        "pipeline",
        "bug",
    )
    assert envelope["source"] == "envelope"
    assert envelope["cache"] == "unknown"
    assert envelope["p90_usd"] >= envelope["p50_usd"] > 0
    assert {row["role"] for row in envelope["roles"]} >= {"analyst", "senior", "tester"}
    assert envelope["roles"][0]["role"] == "orchestrator"
    assert not any(row["repair"] for row in envelope["roles"])
    assert envelope["lines"][0].startswith("Forecast $")
    assert "cache unknown" in envelope["lines"][0]
    text = invoke(cross_args(tmp_path, "--route", "fixed", "--dry-run"))
    assert "Forecast $" in text.stdout
    assert not fake_runner.stdins


def test_the_gpt_team_preview_forecasts_the_per_role_pipeline(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    codex_ready(fake_runner)
    args = cross_args(tmp_path, "--engine", "codex", "--route", "fixed", "--dry-run", "--json")
    result = invoke(args)
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    envelope = data["envelope"]
    assert (envelope["provider"], envelope["shape"]) == ("codex", "pipeline")
    assert envelope["cap_usd"] == data["budget_usd"]
    roles = envelope["roles"]
    assert data["verify"] == []
    assert [row["role"] for row in roles] == ["analyst", "senior", "tester"]
    assert not any(row["repair"] for row in roles)
    assert {row["model"] for row in roles} == {
        row["model"] for row in data["roles"] if row["model"] is not None
    }
    assert all(row["fixed_tokens"] == 20_000 and not row["fixed_measured"] for row in roles)
    assert envelope["margin_usd"] == data["budget_usd"] - envelope["p90_usd"]
    assert envelope["lines"][0].startswith("Forecast $")
    assert "margin $" in envelope["lines"][0]
    assert not fake_runner.stdins


def test_a_simple_mandate_has_no_routed_team_to_forecast(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    result = invoke([*mandate_args(tmp_path, "claude"), "--dry-run", "--json"])
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["envelope"] is None
    assert not fake_runner.stdins


def test_the_claude_team_preview_forecasts_under_the_turn_rail_of_the_launch(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    flags = ("--cross-engine", "--engine", "claude", "--route", "fixed", "--dry-run", "--json")
    result = invoke(cross_args(tmp_path, *flags, "--max-turns", "3"))
    assert result.exit_code == 0, result.stdout
    envelope = json.loads(result.stdout)["envelope"]
    assert envelope["provider"] == "claude"
    assert all(row["requests"] <= 3 and row["max_turns"] <= 3 for row in envelope["roles"])
    assert not fake_runner.stdins


def test_a_failed_team_preview_forecast_is_a_warning_and_the_preview_still_shows(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(self: Container, *args: object, **kwargs: object) -> PlannedForecast:
        raise ValueError("the code index is being written")

    monkeypatch.setattr(Container, "team_forecast", broken)
    codex_ready(fake_runner)
    args = cross_args(tmp_path, "--engine", "codex", "--route", "fixed", "--dry-run")
    result = invoke([*args, "--json"])
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["envelope"] is None
    text = invoke(args)
    assert text.exit_code == 0, text.stdout
    assert "Forecast unavailable" in text.stdout
    assert "the code index is being written" in " ".join(text.stdout.split())
    assert not fake_runner.stdins
