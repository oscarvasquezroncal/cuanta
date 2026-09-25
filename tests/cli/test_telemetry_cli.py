from __future__ import annotations

import json
from pathlib import Path

from tests.fakes import FakeRunner
from tests.support import invoke


def test_env_snippet(tmp_path: Path, fake_runner: FakeRunner) -> None:
    result = invoke(["telemetry", "env", "--shell", "pwsh", "--json", "--project", str(tmp_path)])
    assert result.exit_code == 0
    snippet = json.loads(result.stdout)["snippet"]
    assert "$env:OTEL_EXPORTER_OTLP_PROTOCOL = 'http/json'" in snippet
    bad = invoke(["telemetry", "env", "--shell", "tcsh", "--plain", "--project", str(tmp_path)])
    assert bad.exit_code == 1


def test_on_status_off_roundtrip(tmp_path: Path, fake_runner: FakeRunner) -> None:
    on = invoke(["telemetry", "on", "--engine", "claude", "--json", "--project", str(tmp_path)])
    assert on.exit_code == 0, on.stdout
    assert json.loads(on.stdout)["engines"][0]["state"] == "on"
    status = json.loads(
        invoke(["telemetry", "status", "--json", "--project", str(tmp_path)]).stdout
    )
    states = {item["engine"]: item["state"] for item in status["engines"]}
    assert states == {"claude": "on", "codex": "unavailable"}
    assert status["listener"]["running"] is False
    off = invoke(["telemetry", "off", "--engine", "claude", "--plain", "--project", str(tmp_path)])
    assert off.exit_code == 0
    assert not (tmp_path / ".claude" / "settings.local.json").exists()


def test_unknown_engine(tmp_path: Path, fake_runner: FakeRunner) -> None:
    result = invoke(["telemetry", "on", "--engine", "gemini", "--json", "--project", str(tmp_path)])
    assert result.exit_code == 1


def test_listen_status_and_stop_when_idle(tmp_path: Path, fake_runner: FakeRunner) -> None:
    status = invoke(["listen", "--status", "--json", "--project", str(tmp_path)])
    assert json.loads(status.stdout)["running"] is False
    stop = invoke(["listen", "--stop", "--plain", "--project", str(tmp_path)])
    assert "nap" in stop.stdout
    both = invoke(["listen", "--stop", "--status", "--json", "--project", str(tmp_path)])
    assert both.exit_code == 1
