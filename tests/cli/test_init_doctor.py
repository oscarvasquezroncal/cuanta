from __future__ import annotations

import json
from pathlib import Path

from tests.fakes import FakeRunner, copy_repo
from tests.support import invoke


def test_init_dry_run_json(tmp_path: Path, fake_runner: FakeRunner) -> None:
    root = copy_repo("go_strong", tmp_path)
    result = invoke(["init", str(root), "--dry-run", "--json"])
    assert result.exit_code == 0, result.stdout
    document = json.loads(result.stdout)
    assert document["dry_run"] is True
    assert document["detection"]["verify_tier"] == "strong"
    assert document["detection"]["engines"] == [
        {"name": "claude", "version": "2.1.280", "path": "/bin/claude"}
    ]
    assert not (root / ".claude").exists()


def test_init_plain_prints_nine_line_summary(tmp_path: Path, fake_runner: FakeRunner) -> None:
    root = copy_repo("ts_moderate", tmp_path)
    result = invoke(["--plain", "init", str(root), "--dry-run"])
    lines = result.stdout.splitlines()
    keys = [line.split()[0] for line in lines if line.split() and line.split()[0].endswith(":")]
    assert keys[:11] == [
        "stack:",
        "files:",
        "docs:",
        "forge:",
        "graph:",
        "verify:",
        "vcs:",
        "entry:",
        "run:",
        "engines:",
        "telemetry:",
    ]
    assert "VERIFY_TIER=moderate" in result.stdout


def test_init_rejects_other_engines(tmp_path: Path, fake_runner: FakeRunner) -> None:
    result = invoke(["init", str(tmp_path), "--engine", "codex", "--json"])
    assert result.exit_code == 3
    assert json.loads(result.stdout)["error"]["kind"] == "NotAvailable"


def test_init_missing_folder_is_environment_error(tmp_path: Path, fake_runner: FakeRunner) -> None:
    result = invoke(["init", str(tmp_path / "nope"), "--plain"])
    assert result.exit_code == 2


def test_doctor_json(tmp_path: Path, fake_runner: FakeRunner) -> None:
    root = copy_repo("python_strong", tmp_path)
    result = invoke(["doctor", "--json", "--project", str(root)])
    assert result.exit_code == 0
    document = json.loads(result.stdout)
    names = [check["name"] for check in document["checks"]]
    assert names[:2] == ["python", "engine claude"]
    assert document["healthy"] is True


def test_doctor_json_reports_home_agents_md_size(
    tmp_path: Path, isolated_user_dirs: Path, fake_runner: FakeRunner
) -> None:
    (isolated_user_dirs / "AGENTS.md").write_bytes(b"x" * 230)
    root = copy_repo("python_strong", tmp_path)
    result = invoke(["doctor", "--json", "--project", str(root)])
    assert result.exit_code == 0, result.stdout + result.stderr
    checks = {check["name"]: check for check in json.loads(result.stdout)["checks"]}
    assert checks["agents-md"]["status"] == "info"
    assert "~/AGENTS.md: 230 bytes" in checks["agents-md"]["detail"]
    assert "unavailable" in checks["agents-md"]["detail"]


def test_purr_alias_pretty(tmp_path: Path, fake_runner: FakeRunner) -> None:
    root = copy_repo("python_strong", tmp_path)
    result = invoke(["purr", "--project", str(root), "--no-emoji"], pretty=True)
    assert result.exit_code == 0
    assert "doctor" in result.stdout


def test_doctor_flags_corrupt_state(tmp_path: Path, fake_runner: FakeRunner) -> None:
    root = copy_repo("python_strong", tmp_path)
    (root / ".claude").mkdir()
    (root / ".claude" / "forge-state.json").write_text("{", encoding="utf-8")
    result = invoke(["doctor", "--json", "--project", str(root)])
    assert result.exit_code == 1
    checks = {check["name"]: check for check in json.loads(result.stdout)["checks"]}
    assert checks["forge-state"]["status"] == "fail"
