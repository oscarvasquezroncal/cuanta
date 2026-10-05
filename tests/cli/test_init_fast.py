from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.application.forge import ForgeStage
from cuanta.application.init_project import InitContext, StageResult
from cuanta.domain.telemetry import claude_env
from tests.cli.test_init_e2e import fake_bin, fake_claude
from tests.fakes import FakeRunner, copy_repo
from tests.real_run import forged_bytes, forged_project, forged_rulebook
from tests.support import invoke

__all__ = ["fake_claude"]

KEPT = "kept: FORGE_STATE=initialized · --refresh-forge runs Forge again"
SCHEDULED = "updating in the background · .cuanta/graph-refresh.log"
STAGES = ("detect", "graph", "telemetry", "forge", "verify")
INIT_BUDGET_S = 120.0


def _stages(document: dict[str, object]) -> dict[str, dict[str, object]]:
    rows = document["stages"]
    assert isinstance(rows, list)
    return {str(row["stage"]): row for row in rows}


def _runs(root: Path) -> list[str]:
    ledger = SqliteLedger(root / ".cuanta" / "ledger.db")
    try:
        return [run.kind for run in ledger.runs()]
    finally:
        ledger.close()


def _capture_spawn(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    spawned: list[list[str]] = []
    original = subprocess.Popen

    def spawn(command: list[str], **kwargs: Any) -> subprocess.Popen[Any]:
        if "cuanta.adapters.graph.index_graph" not in command:
            return original(command, **kwargs)
        spawned.append(command)
        return cast("subprocess.Popen[bytes]", SimpleNamespace(pid=424242))

    monkeypatch.setattr("cuanta.adapters.graph.index_graph.subprocess.Popen", spawn)
    return spawned


def test_graph_spawn_double_keeps_other_processes_real(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    spawned = _capture_spawn(monkeypatch)
    with subprocess.Popen(
        [sys.executable, "-c", "print('ready')"], stdout=subprocess.PIPE, text=True
    ) as probe:
        output, _ = probe.communicate(timeout=5)
    assert output == "ready\n"
    assert spawned == []


def _sources(root: Path, count: int) -> Path:
    for index in range(count):
        package = root / "app" / f"pkg_{index // 100}"
        package.mkdir(parents=True, exist_ok=True)
        (package / f"module_{index}.py").write_text(f"value = {index}\n", encoding="utf-8")
    return root


def test_init_on_an_initialized_project_calls_no_model(tmp_path: Path, fake_claude: None) -> None:
    root = copy_repo("python_strong", tmp_path)
    prompt = tmp_path / "prompt.txt"
    env = {"CUANTA_CLAUDE_BIN": fake_bin(), "FAKE_CLAUDE_PROMPT_OUT": str(prompt)}
    command = ["init", str(root), "--yes", "--json", "--skip-telemetry"]
    first = invoke(command, env=env)
    assert first.exit_code == 0, first.stdout + first.stderr
    assert prompt.is_file()
    prompt.unlink()
    (root / "CLAUDE.md").write_text(forged_rulebook(), encoding="utf-8")
    before = forged_bytes(root)
    second = invoke(command, env=env)
    assert second.exit_code == 0, second.stdout + second.stderr
    document = json.loads(second.stdout)
    assert not prompt.exists()
    forge = _stages(document)["forge"]
    assert (forge["status"], forge["detail"]) == ("skip", KEPT)
    assert document["cost_usd"] == 0.0
    assert forged_bytes(root) == before
    assert [path for path in root.rglob("*") if ".new." in path.name] == []
    assert _runs(root) == ["init"]


def _stopped(stage: ForgeStage, context: InitContext) -> StageResult:
    raise KeyboardInterrupt


def test_a_kept_init_stopped_in_its_forge_stage_resumes_without_the_model(
    tmp_path: Path, fake_claude: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = copy_repo("python_strong", tmp_path)
    prompt = tmp_path / "prompt.txt"
    env = {"CUANTA_CLAUDE_BIN": fake_bin(), "FAKE_CLAUDE_PROMPT_OUT": str(prompt)}
    command = ["init", str(root), "--yes", "--json", "--skip-telemetry"]
    assert invoke(command, env=env).exit_code == 0
    prompt.unlink()
    with monkeypatch.context() as scoped:
        scoped.setattr(ForgeStage, "_keep", _stopped)
        stopped = invoke(command, env=env)
    assert stopped.exit_code == 130, stopped.stdout
    resumed = invoke(command, env=env)
    assert resumed.exit_code == 0, resumed.stdout
    document = json.loads(resumed.stdout)
    assert document["resumed_from"] == "forge"
    assert (_stages(document)["forge"]["status"], _stages(document)["forge"]["detail"]) == (
        "skip",
        KEPT,
    )
    assert not prompt.exists()
    assert _runs(root) == ["init"]


def test_init_prints_each_stage_time_and_a_total(tmp_path: Path, fake_runner: FakeRunner) -> None:
    root = forged_project(copy_repo("python_strong", tmp_path))
    plain = invoke(["--plain", "init", str(root), "--skip-telemetry", "--yes"])
    assert plain.exit_code == 0, plain.stdout
    lines = plain.stdout.splitlines()
    for key in STAGES:
        timed = [line for line in lines if re.fullmatch(rf"\S+ {key}( .*)? · \d[\d,]* s", line)]
        assert timed, (key, plain.stdout)
    assert [line for line in lines if re.fullmatch(r"\S+ init complete · \d[\d,]* s", line)]
    document = json.loads(invoke(["init", str(root), "--skip-telemetry", "--yes", "--json"]).stdout)
    for key, row in _stages(document).items():
        assert isinstance(row["seconds"], float), key
        assert not str(row["detail"]).endswith(" s"), key
    assert isinstance(document["seconds"], float)
    assert document["seconds"] >= sum(cast(float, row["seconds"]) for row in document["stages"])


def test_refresh_forge_runs_forge_again(tmp_path: Path, fake_claude: None) -> None:
    root = copy_repo("python_strong", tmp_path)
    prompt = tmp_path / "prompt.txt"
    env = {"CUANTA_CLAUDE_BIN": fake_bin(), "FAKE_CLAUDE_PROMPT_OUT": str(prompt)}
    assert (
        invoke(["init", str(root), "--yes", "--json", "--skip-telemetry"], env=env).exit_code == 0
    )
    prompt.unlink()
    again = invoke(
        ["--plain", "init", str(root), "--yes", "--skip-telemetry", "--refresh-forge"], env=env
    )
    assert again.exit_code == 0, again.stdout + again.stderr
    assert "running refresh semantics" in again.stdout
    assert prompt.is_file()
    assert "Resume from .claude/forge-state.json" in prompt.read_text(encoding="utf-8")
    assert _runs(root) == ["init", "init"]


def test_skip_graph_skips_the_graph_stage_but_writes_the_handoff(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _sources(copy_repo("python_strong", tmp_path), 150)
    (root / ".git").mkdir()
    fake_runner.binaries["graphify"] = "/bin/graphify"
    spawned = _capture_spawn(monkeypatch)
    command = ["init", str(root), "--json", "--yes", "--skip-forge", "--skip-telemetry"]
    result = invoke([*command, "--skip-graph"])
    assert result.exit_code == 0, result.stdout
    document = json.loads(result.stdout)
    graph = _stages(document)["graph"]
    assert (graph["status"], graph["detail"]) == ("skip", "skipped (--skip-graph)")
    assert document["graph"] == "skipped (--skip-graph)"
    assert document["detection"]["graph_mode"] == "cli"
    assert ("graphify", "update", ".") not in fake_runner.calls
    assert spawned == []
    assert ".claude/forge-state.json" in (root / ".gitignore").read_text(encoding="utf-8")
    assert (root / ".cuanta" / ".gitignore").is_file()
    state = json.loads((root / ".claude" / "forge-state.json").read_text(encoding="utf-8"))
    assert state["graph_mode"] == "cli"


def test_refresh_forge_with_skip_forge_is_refused(tmp_path: Path, fake_runner: FakeRunner) -> None:
    root = forged_project(copy_repo("python_strong", tmp_path))
    result = invoke(["init", str(root), "--refresh-forge", "--skip-forge", "--json"])
    assert result.exit_code == 1
    error = json.loads(result.stdout)["error"]
    assert error["message"] == "--refresh-forge runs Forge and --skip-forge skips it"
    assert error["hint"] == "pass only one of them"
    assert not (root / ".cuanta").exists()


def test_init_does_not_ask_again_when_telemetry_is_wired(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CUANTA_PORT", "47320")
    root = forged_project(copy_repo("python_strong", tmp_path))
    settings = root / ".claude" / "settings.local.json"
    wired = {"env": claude_env(47320, root.name)}
    settings.write_text(json.dumps(wired, indent=2) + "\n", encoding="utf-8")
    before = settings.read_bytes()
    result = invoke(
        ["init", str(root)], env={"CUANTA_PORT": "47320"}, pretty=True, input_text="n\n"
    )
    assert result.exit_code == 0, result.stdout
    assert "Wire local telemetry" not in result.stdout
    assert "port 47320 · already wired" in result.stdout
    assert "127.0.0.1:47320 · claude on · already wired" in result.stdout
    assert settings.read_bytes() == before


@pytest.mark.perf
def test_initialized_init_of_a_thousand_files_under_two_minutes(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _sources(forged_project(tmp_path / "backend", graph_mode="cli"), 1000)
    fake_runner.binaries["graphify"] = "/bin/graphify"
    spawned = _capture_spawn(monkeypatch)
    started = time.perf_counter()
    result = invoke(["init", str(root), "--yes", "--json"])
    elapsed = time.perf_counter() - started
    assert result.exit_code == 0, result.stdout
    document = json.loads(result.stdout)
    assert document["detection"]["file_count"] >= 1000
    assert elapsed < INIT_BUDGET_S
    assert document["seconds"] < INIT_BUDGET_S
    assert fake_runner.stdins == []
    assert [command[-1] for command in spawned] == ["bootstrap"]
    stages = _stages(document)
    assert (stages["forge"]["status"], stages["forge"]["detail"]) == ("skip", KEPT)
    assert (stages["graph"]["status"], stages["graph"]["detail"]) == ("ok", SCHEDULED)
