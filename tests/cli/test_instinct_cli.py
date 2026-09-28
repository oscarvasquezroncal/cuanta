from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuanta.adapters.instinct.jev import KEY_ENV
from cuanta.adapters.system import config_files
from cuanta.bootstrap import Container
from cuanta.domain.ledger import Forecast, Run
from tests.fakes import FakeRunner
from tests.support import invoke


def test_instinct_cli_show_use_probe(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(KEY_ENV, raising=False)
    shown = json.loads(invoke(["instinct", "show", "--json", "--project", str(tmp_path)]).stdout)
    assert shown["backend"] == "heuristic"
    assert shown["source"] == "default"
    refused = invoke(["instinct", "use", "jev", "--json", "--project", str(tmp_path)])
    assert refused.exit_code == 1
    accepted = invoke(["instinct", "use", "jev", "--yes", "--json", "--project", str(tmp_path)])
    assert accepted.exit_code == 0
    assert json.loads(accepted.stdout)["consent"] == ["jev"]
    config = (tmp_path / ".cuanta" / "config.toml").read_text(encoding="utf-8")
    assert 'backend = "jev"' in config
    shown_after = json.loads(
        invoke(["instinct", "show", "--json", "--project", str(tmp_path)]).stdout
    )
    assert shown_after["source"] == "project"
    probe = json.loads(invoke(["instinct", "probe", "--json", "--project", str(tmp_path)]).stdout)
    assert probe["backend"] == "heuristic"
    assert len(probe["answers"]) == 3
    back = invoke(["instinct", "use", "heuristic", "--plain", "--project", str(tmp_path)])
    assert back.exit_code == 0


def test_global_scope_resolves_below_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_dir = tmp_path / "user-config"
    monkeypatch.setattr(config_files, "config_dir", lambda: global_dir)
    monkeypatch.delenv("CUANTA_INSTINCT", raising=False)
    monkeypatch.setattr(Container, "instinct_connection", lambda self: None)
    first = tmp_path / "first"
    second = tmp_path / "second"
    used = invoke(
        ["instinct", "use", "jev", "--global", "--yes", "--json", "--project", str(first)]
    )
    assert used.exit_code == 0
    assert json.loads(used.stdout)["scope"] == "global"
    assert not (first / ".cuanta" / "config.toml").exists()
    assert 'backend = "jev"' in (global_dir / "config.toml").read_text(encoding="utf-8")
    shown = json.loads(invoke(["instinct", "show", "--json", "--project", str(second)]).stdout)
    assert (shown["backend"], shown["source"]) == ("jev", "global")
    invoke(["instinct", "use", "heuristic", "--json", "--project", str(second)])
    overridden = json.loads(invoke(["instinct", "show", "--json", "--project", str(second)]).stdout)
    assert (overridden["backend"], overridden["source"]) == ("heuristic", "project")


@pytest.mark.parametrize(
    ("key", "base", "warning"),
    [
        ("apikey_example", "https://openrouter.ai", "apikey_ key with OpenRouter URL"),
        ("sk-or-example", "https://api.typesafe.ai", "sk-or- key with TypeSafe URL"),
    ],
)
def test_show_warns_for_key_url_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    base: str,
    warning: str,
) -> None:
    monkeypatch.setenv(KEY_ENV, key)
    monkeypatch.setenv("TYPESAFE_BASE_URL", base)
    monkeypatch.setattr(Container, "instinct_connection", lambda self: None)
    config_files.set_value(tmp_path / ".cuanta" / "config.toml", "instinct.backend", "jev")
    shown = json.loads(invoke(["instinct", "show", "--json", "--project", str(tmp_path)]).stdout)
    assert warning in shown["warning"]


def test_instinct_calibration_reports_error_and_coverage_per_provider_and_type(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    empty = invoke(["instinct", "calibration", "--json", "--project", str(tmp_path)])
    assert empty.exit_code == 0, empty.stdout
    assert json.loads(empty.stdout) == {"groups": []}
    assert not (tmp_path / ".cuanta" / "ledger.db").exists()
    container = Container.for_project(tmp_path)
    ledger = container.ledger()
    try:
        for run_id, task_type, p50, actual in (
            ("R1", "bug", 0.4, 0.5),
            ("R2", "fix", 0.5, 0.5),
            ("R3", "feature", 1.0, None),
        ):
            ledger.add_forecast(
                Forecast(
                    run_id,
                    "2026-09-28T10:00:00Z",
                    "claude",
                    task_type,
                    "normal",
                    "pipeline",
                    p50,
                    p50 * 1.6,
                    2.0,
                    "comfortable",
                )
            )
            ledger.add_run(Run(run_id, "mandate", "claude", status="ok", cost_usd=actual))
    finally:
        container.close()
    result = invoke(["instinct", "calibration", "--json", "--project", str(tmp_path)])
    groups = json.loads(result.stdout)["groups"]
    assert [(row["task_type"], row["samples"], row["unknown"]) for row in groups] == [
        ("feature", 0, 1),
        ("fix", 2, 0),
    ]
    fix = groups[1]
    assert fix["mae_usd"] == pytest.approx(0.05)
    assert fix["mape"] == pytest.approx(0.1)
    assert fix["p90_coverage"] == 1.0
    assert groups[0]["mae_usd"] is None
    plain = invoke(["instinct", "calibration", "--plain", "--project", str(tmp_path)])
    assert "forecast calibration" in plain.stdout
    assert "n/a" in plain.stdout
    assert "$0.0500" in plain.stdout
