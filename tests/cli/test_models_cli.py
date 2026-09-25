from __future__ import annotations

import json
import tomllib
from pathlib import Path

from cuanta.ports.system import Completed
from tests.fakes import FakeRunner
from tests.support import invoke

CODEX_MODELS = {
    "models": [
        {"slug": "gpt-5.6-sol", "display_name": "GPT-5.6-Sol", "visibility": "list"},
        {"slug": "gpt-5.6-luna", "display_name": "GPT-5.6-Luna", "visibility": "list"},
    ]
}


def _with_codex(runner: FakeRunner) -> None:
    runner.binaries["codex"] = "/bin/codex"
    runner.responses["codex debug models"] = Completed(0, json.dumps(CODEX_MODELS), "")


def test_models_list_caches_and_filters(tmp_path: Path, fake_runner: FakeRunner) -> None:
    _with_codex(fake_runner)
    project = ["--project", str(tmp_path)]
    listed = json.loads(invoke(["models", "list", "--json", *project]).stdout)
    assert listed["engines"] == ["claude", "codex"]
    assert not listed["cached"]
    ids = {(item["engine"], item["id"]): item["tier"] for item in listed["models"]}
    assert ids[("claude", "opus")] == "premium"
    assert ids[("codex", "gpt-5.6-luna")] == "economy"
    assert (tmp_path / ".cuanta" / "models.json").is_file()
    calls = len(fake_runner.calls)
    again = json.loads(invoke(["models", "list", "--engine", "codex", "--json", *project]).stdout)
    assert again["cached"]
    assert {item["engine"] for item in again["models"]} == {"codex"}
    assert len(fake_runner.calls) == calls


def test_models_tier_override_is_saved_and_applied(tmp_path: Path, fake_runner: FakeRunner) -> None:
    _with_codex(fake_runner)
    project = ["--project", str(tmp_path)]
    changed = invoke(["models", "tier", "gpt-5.6-luna", "standard", "--json", *project])
    assert changed.exit_code == 0, changed.stdout
    config = tomllib.loads((tmp_path / ".cuanta" / "config.toml").read_text(encoding="utf-8"))
    assert config["models"]["tiers"]["codex:gpt-5.6-luna"] == "standard"
    listed = json.loads(invoke(["models", "list", "--tier", "standard", "--json", *project]).stdout)
    luna = next(item for item in listed["models"] if item["id"] == "gpt-5.6-luna")
    assert luna["tier_source"] == "override"
    bad = invoke(["models", "tier", "gpt-5.6-luna", "gold", "--json", *project])
    assert bad.exit_code != 0
    assert "economy, standard, premium, frontier" in json.loads(bad.stdout)["error"]["hint"]


def test_models_probe_shows_the_cost_before_spending(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    shown = invoke(["models", "probe", "haiku", "--json", "--project", str(tmp_path)])
    assert shown.exit_code == 0, shown.stdout
    document = json.loads(shown.stdout)
    assert document["probed"] is False
    assert 0 < document["estimate_usd"] < 0.001
    assert not any("-p" in call for call in fake_runner.calls)


def test_models_stats_without_runs(tmp_path: Path, fake_runner: FakeRunner) -> None:
    result = invoke(["models", "stats", "--json", "--project", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["models"] == []
    assert json.loads(result.stdout)["roles"] == []


def test_route_dry_run_explains_every_role(tmp_path: Path, fake_runner: FakeRunner) -> None:
    _with_codex(fake_runner)
    project = ["--project", str(tmp_path)]
    result = invoke(
        [
            "route",
            "--dry-run",
            "--type",
            "bug",
            "--what",
            "fix typo in README",
            "--role-model",
            "docs=gpt-5.6-luna",
            "--json",
            *project,
        ]
    )
    assert result.exit_code == 0, result.stdout
    plan = json.loads(result.stdout)
    assert plan["mode"] == "auto"
    assert plan["scope"] == "trivial"
    roles = {route["role"]: route for route in plan["routes"]}
    assert set(roles) == {"orchestrator", "analyst", "senior", "tester", "docs"}
    assert all(route["reason"] for route in plan["routes"])
    assert roles["docs"]["model"] == "gpt-5.6-luna"
    assert "pinned" in roles["docs"]["reason"]
    fixed = json.loads(
        invoke(["route", "--route", "fixed", "--preset", "save", "--json", *project]).stdout
    )
    assert {route["role"]: route["tier"] for route in fixed["routes"]}["senior"] == "standard"
    bad = invoke(["route", "--preset", "cheap", "--json", *project])
    assert bad.exit_code != 0
