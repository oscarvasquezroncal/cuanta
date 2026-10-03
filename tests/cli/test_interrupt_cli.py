from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import NoReturn

import pytest
from typer.testing import Result

from cuanta.application.forge import VerifyStage
from cuanta.application.init_project import GraphStage
from cuanta.application.loop import FixLoop, LoopReport
from cuanta.application.mandate import MandateService
from cuanta.application.mandate_flow import MandateFlow
from cuanta.application.sandbox import SandboxResult
from cuanta.bootstrap import Container
from cuanta.cli.commands import queue as queue_command
from cuanta.cli.presenters.json_presenter import JsonPresenter
from cuanta.domain.cache import PrefixWindow
from cuanta.domain.ledger import Run, TestRunRecord
from cuanta.domain.loop import LoopGate, StopReason
from tests.cli.test_engine_guarantees import codex_ready, cross_args, forge
from tests.cli.test_init_e2e import fake_claude
from tests.fakes import FakeRunner, FakeStream, copy_repo
from tests.support import invoke, strip_ansi

__all__ = ["fake_claude"]

INIT = '{"type":"system","subtype":"init","session_id":"s","model":"m"}'
SUCCESS = (
    '{"type":"result","subtype":"success","total_cost_usd":0.001,"is_error":false,'
    '"result":"## SUMMARY\\nok"}'
)
PAID = (
    '{"type":"result","subtype":"success","total_cost_usd":0.25,"is_error":false,'
    '"result":"## SUMMARY\\nok"}'
)
CODEX_DONE = (
    '{"type":"thread.started","thread_id":"fixture"}',
    '{"type":"item.completed","item":{"type":"agent_message","text":"done"}}',
    '{"type":"turn.completed","usage":{"input_tokens":1000,"cached_input_tokens":0,"output_tokens":100}}',
)
CODEX_TEAM = ("--engine", "codex", "--route", "fixed", "--shape", "scout", "--json")
INTERRUPTED = 130
STOPPED = "interrupted"
NINE_LIVES = "nine lives: re-run to resume"
UNFINISHED = "interrupted before the run finished"


def asked(what: str) -> list[str]:
    return [
        "--simple",
        "--type",
        "investigation",
        "--what",
        what,
        "--why",
        "How does it work?",
        "--out-of-scope",
        "No edits",
    ]


def cuanta(root: Path, *args: str) -> Result:
    return invoke([*args, "--project", str(root)])


def error_of(result: Result) -> dict[str, object]:
    assert result.exit_code == INTERRUPTED, result.stdout + result.stderr
    error = json.loads(result.stdout)["error"]
    assert isinstance(error, dict)
    assert error["exit_code"] == INTERRUPTED
    return error


def ctrl_c(*_: object, **__: object) -> None:
    raise KeyboardInterrupt


def recorded(root: Path) -> list[dict[str, object]]:
    rows = json.loads(cuanta(root, "runs", "list", "--json").stdout)["runs"]
    assert isinstance(rows, list)
    return rows


def interrupting() -> FakeStream:
    return FakeStream([INIT], interrupt=True)


def ledger_runs(root: Path) -> list[Run]:
    container = Container.for_project(root)
    try:
        return list(container.shared_ledger().runs())
    finally:
        container.close()


def queue_add(root: Path, what: str) -> str:
    added = cuanta(root, "queue", "add", *asked(what), "--json")
    assert added.exit_code == 0, added.stdout + added.stderr
    return str(json.loads(added.stdout)["added"]["id"])


def queued(root: Path) -> list[str]:
    entries = json.loads(cuanta(root, "queue", "list", "--json").stdout)["queue"]
    return [str(entry["id"]) for entry in entries]


def red_suite(root: Path) -> None:
    forge(root)
    failing = TestRunRecord(
        id="T1",
        run_id="",
        runner="pytest",
        command="pytest",
        status="red",
        passed=0,
        failed=1,
        errored=0,
        skipped=0,
        duration_s=1.0,
        capsule_id="",
        started_at="2026-01-01T00:00:00Z",
    )
    container = Container.for_project(root)
    try:
        container.shared_ledger().add_test_run(failing, [])
    finally:
        container.close()


def one_fix_then_ctrl_c(
    self: FixLoop, loop_id: str, max_iterations: int, budget_usd: float
) -> NoReturn:
    self._fix(loop_id, 1)
    raise KeyboardInterrupt


def open_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cuanta.domain.loop.loop_gate", lambda *_: LoopGate(True, ""))


def test_ctrl_c_in_a_dry_run_says_only_interrupted(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MandateFlow, "prepare", ctrl_c)
    error = error_of(cuanta(tmp_path, "mandate", *asked("Explain the cart"), "--dry-run", "--json"))
    assert (error["message"], error["hint"]) == (STOPPED, "")
    plain = cuanta(tmp_path, "mandate", *asked("Explain the cart"), "--dry-run", "--plain")
    assert plain.exit_code == INTERRUPTED
    assert STOPPED in plain.stderr
    assert "runs show" not in plain.stderr and "nine lives" not in plain.stderr


def test_ctrl_c_after_prepare_but_before_the_launch_names_no_run(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MandateService, "run", ctrl_c)
    error = error_of(cuanta(tmp_path, "mandate", *asked("Explain the cart"), "--json"))
    assert (error["message"], error["hint"]) == (STOPPED, "")
    assert recorded(tmp_path) == []
    assert not fake_runner.stdins


def test_ctrl_c_during_the_run_names_it_and_how_to_see_it(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    fake_runner.streams["claude -p"] = interrupting()
    error = error_of(cuanta(tmp_path, "mandate", *asked("Explain the cart"), "--json"))
    [run] = recorded(tmp_path)
    run_id = str(run["id"])
    assert run["status"] == "interrupted"
    assert error["message"] == f"interrupted · run {run_id}"
    assert error["hint"] == f"see it: cuanta runs show {run_id}"
    shown = json.loads(cuanta(tmp_path, "runs", "show", "--json").stdout)
    assert (shown["run_id"], shown["status"]) == (run_id, "interrupted")
    plain = cuanta(tmp_path, "fix", "The total adds the shipping twice", "--simple", "--plain")
    assert plain.exit_code == INTERRUPTED
    [second] = [str(row["id"]) for row in recorded(tmp_path) if row["id"] != run_id]
    assert f"interrupted · run {second}" in plain.stderr
    assert f"see it: cuanta runs show {second}" in plain.stderr
    assert "nine lives" not in plain.stderr


def test_the_run_named_is_the_one_launched_even_when_a_role_was_interrupted(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    container = Container.for_project(tmp_path)
    try:
        assert container.recorded_run() == ""
        root = container.new_run_id()
        role = container.sandbox_container(tmp_path / "copy").new_run_id()
        container.new_run_id()
        ledger = container.shared_ledger()
        ledger.add_run(Run(root, "cross", "claude", status="ok"))
        ledger.add_run(Run(role, "cross", "claude", status="interrupted", parent_id=root))
        ledger.add_run(Run("01ZZZZZZZZOTHERPROCESS0000", "mandate", "claude"))
        assert container.recorded_run() == root
    finally:
        container.close()
    fresh = Container.for_project(tmp_path)
    try:
        fresh.new_run_id()
        assert fresh.recorded_run() == ""
    finally:
        fresh.close()


def test_init_stopped_after_a_finished_stage_offers_nine_lives_and_resumes(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app.py").write_text("total = 1\n", encoding="utf-8")
    arguments = ("init", str(tmp_path), "--skip-forge", "--skip-telemetry", "--yes", "--json")
    verify = VerifyStage.__call__
    monkeypatch.setattr(VerifyStage, "__call__", ctrl_c)
    error = error_of(cuanta(tmp_path, *arguments))
    assert (error["message"], error["hint"]) == (STOPPED, NINE_LIVES)
    monkeypatch.setattr(VerifyStage, "__call__", verify)
    resumed = json.loads(cuanta(tmp_path, *arguments).stdout)
    assert resumed["resumed_from"] == "verify"


def test_init_stopped_in_its_first_stage_offers_no_nine_lives(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app.py").write_text("total = 1\n", encoding="utf-8")
    monkeypatch.setattr(GraphStage, "__call__", ctrl_c)
    arguments = ("init", str(tmp_path), "--skip-forge", "--skip-telemetry", "--yes", "--json")
    error = error_of(cuanta(tmp_path, *arguments))
    assert (error["message"], error["hint"]) == (STOPPED, "")


def test_a_loop_stopped_names_the_loop_and_settles_it(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cuanta.domain.loop.loop_gate", lambda *_: LoopGate(True, ""))
    monkeypatch.setattr(FixLoop, "run", ctrl_c)
    error = error_of(cuanta(tmp_path, "loop", "--json"))
    [run] = recorded(tmp_path)
    assert (run["kind"], run["status"]) == ("loop", "interrupted")
    assert error["message"] == f"interrupted · run {run['id']}"
    assert error["hint"] == f"see it: cuanta runs show {run['id']}"


def test_a_queue_stopped_mid_run_names_the_run_and_resumes_on_a_re_run(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    for what in ("one", "two"):
        assert cuanta(tmp_path, "queue", "add", *asked(what)).exit_code == 0
    fake_runner.queued["claude -p"] = [interrupting()]
    error = error_of(cuanta(tmp_path, "queue", "run", "--yes", "--json"))
    [run] = recorded(tmp_path)
    run_id = str(run["id"])
    assert error["message"] == f"interrupted · run {run_id}"
    assert error["hint"] == f"see it: cuanta runs show {run_id} · {NINE_LIVES}"
    queued = json.loads(cuanta(tmp_path, "queue", "list", "--json").stdout)["queue"]
    assert len(queued) == 2
    fake_runner.queued["claude -p"] = [FakeStream([SUCCESS]), FakeStream([SUCCESS])]
    resumed = cuanta(tmp_path, "queue", "run", "--yes", "--json")
    assert resumed.exit_code == 0, resumed.stdout + resumed.stderr
    assert json.loads(resumed.stdout)["left"] == 0


def test_ctrl_c_during_the_start_snapshot_settles_the_run_it_names(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = MandateService.snapshot

    def stopped_at_start(self: MandateService, run_id: str, phase: str) -> dict[str, str]:
        if phase == "start":
            raise KeyboardInterrupt
        return snapshot(self, run_id, phase)

    monkeypatch.setattr(MandateService, "snapshot", stopped_at_start)
    error = error_of(cuanta(tmp_path, "mandate", *asked("Explain the cart"), "--json"))
    [run] = ledger_runs(tmp_path)
    assert (run.status, bool(run.ended_at)) == ("interrupted", True)
    assert error["message"] == f"interrupted · run {run.id}"
    shown = json.loads(cuanta(tmp_path, "runs", "show", "--json").stdout)
    assert (shown["run_id"], shown["stop_reason"]) == (run.id, UNFINISHED)
    assert not fake_runner.stdins


def test_an_error_during_the_start_snapshot_settles_the_run_as_failed(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = MandateService.snapshot

    def denied_at_start(self: MandateService, run_id: str, phase: str) -> dict[str, str]:
        if phase == "start":
            raise PermissionError(13, "Permission denied", ".cuanta/blobs/x")
        return snapshot(self, run_id, phase)

    monkeypatch.setattr(MandateService, "snapshot", denied_at_start)
    result = cuanta(tmp_path, "mandate", *asked("Explain the cart"), "--json")
    assert result.exit_code == 2, result.stdout + result.stderr
    assert "PermissionError" in json.loads(result.stdout)["error"]["message"]
    [run] = ledger_runs(tmp_path)
    assert (run.status, bool(run.ended_at), run.end_reason) == ("failed", True, "error_raised")
    shown = json.loads(cuanta(tmp_path, "runs", "show", "--json").stdout)
    assert (shown["run_id"], shown["status"]) == (run.id, "failed")
    assert shown["stop_reason"] == "stopped by an error before the engine returned a result"
    assert not fake_runner.stdins


def test_ctrl_c_in_a_later_role_marks_the_team_it_names_as_interrupted(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    codex_ready(fake_runner)
    fake_runner.queued["codex exec"] = [
        FakeStream(list(CODEX_DONE)),
        FakeStream(list(CODEX_DONE[:1]), interrupt=True),
    ]
    error = error_of(invoke(cross_args(tmp_path, *CODEX_TEAM)))
    runs = ledger_runs(tmp_path)
    [root] = [run for run in runs if not run.parent_id]
    assert [run.status for run in runs if run.parent_id == root.id] == ["interrupted"]
    assert error["message"] == f"interrupted · run {root.id}"
    shown = json.loads(cuanta(tmp_path, "runs", "show", root.id, "--json").stdout)
    assert (shown["stop_reason"], shown["completion"]) == (UNFINISHED, "partial")


def test_ctrl_c_in_the_first_role_marks_the_team_as_failed_and_interrupted(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    codex_ready(fake_runner)
    fake_runner.queued["codex exec"] = [FakeStream(list(CODEX_DONE[:1]), interrupt=True)]
    error = error_of(invoke(cross_args(tmp_path, *CODEX_TEAM)))
    [root] = ledger_runs(tmp_path)
    assert (root.status, error["message"]) == ("interrupted", f"interrupted · run {root.id}")
    shown = json.loads(cuanta(tmp_path, "runs", "show", root.id, "--json").stdout)
    assert (shown["stop_reason"], shown["completion"]) == (UNFINISHED, "failed")


def test_an_aborted_prompt_ends_as_interrupted(tmp_path: Path, fake_runner: FakeRunner) -> None:
    (tmp_path / "app.py").write_text("total = 1\n", encoding="utf-8")
    queue_add(tmp_path, "one")
    for arguments in (("mandate",), ("init", str(tmp_path), "--skip-forge"), ("queue", "run")):
        result = invoke([*arguments, "--project", str(tmp_path)], pretty=True, input_text="")
        output = strip_ansi(result.stdout + result.stderr)
        assert result.exit_code == INTERRUPTED, output
        assert STOPPED in output, output
        assert "unexpected Abort" not in output, output
    assert queued(tmp_path) and not fake_runner.stdins


def test_a_queue_stopped_after_an_entry_finished_drops_it_and_names_its_run(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue_add(tmp_path, "one")
    second = queue_add(tmp_path, "two")
    fake_runner.queued["claude -p"] = [FakeStream([SUCCESS]), FakeStream([SUCCESS])]
    warm = queue_command._window
    calls: list[str] = []

    def stopped_on_the_warm_line(container: Container, engine: str) -> PrefixWindow:
        calls.append(engine)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return warm(container, engine)

    with monkeypatch.context() as scoped:
        scoped.setattr(queue_command, "_window", stopped_on_the_warm_line)
        error = error_of(cuanta(tmp_path, "queue", "run", "--yes", "--json"))
    [run] = recorded(tmp_path)
    assert (run["status"], error["message"]) == ("ok", f"interrupted · run {run['id']}")
    assert error["hint"] == f"see it: cuanta runs show {run['id']} · {NINE_LIVES}"
    assert queued(tmp_path) == [second]
    resumed = cuanta(tmp_path, "queue", "run", "--yes", "--json")
    assert resumed.exit_code == 0, resumed.stdout + resumed.stderr
    assert [item["id"] for item in json.loads(resumed.stdout)["results"]] == [second]


@pytest.mark.parametrize("stop_at", [2, 3], ids=["entry_warm_line", "closing_warm_line"])
def test_a_queue_stopped_after_its_last_entry_finished_offers_no_nine_lives(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, stop_at: int
) -> None:
    queue_add(tmp_path, "one")
    fake_runner.queued["claude -p"] = [FakeStream([SUCCESS])]
    warm = queue_command._window
    calls: list[str] = []

    def stopped_on_the_warm_line(container: Container, engine: str) -> PrefixWindow:
        calls.append(engine)
        if len(calls) == stop_at:
            raise KeyboardInterrupt
        return warm(container, engine)

    with monkeypatch.context() as scoped:
        scoped.setattr(queue_command, "_window", stopped_on_the_warm_line)
        error = error_of(cuanta(tmp_path, "queue", "run", "--yes", "--json"))
    [run] = recorded(tmp_path)
    assert (len(calls), error["message"]) == (stop_at, f"interrupted · run {run['id']}")
    assert error["hint"] == f"see it: cuanta runs show {run['id']}"
    assert queued(tmp_path) == []


def test_an_interrupted_dry_run_init_writes_nothing(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app.py").write_text("total = 1\n", encoding="utf-8")
    monkeypatch.setattr(GraphStage, "__call__", ctrl_c)
    error = error_of(cuanta(tmp_path, "init", str(tmp_path), "--dry-run", "--json"))
    assert (error["message"], error["hint"]) == (STOPPED, "")
    assert not (tmp_path / ".cuanta").exists()


def test_looking_up_the_run_never_creates_a_ledger(tmp_path: Path, fake_runner: FakeRunner) -> None:
    container = Container.for_project(tmp_path)
    try:
        container.new_run_id()
        assert container.recorded_run() == ""
    finally:
        container.close()
    assert not (tmp_path / ".cuanta").exists()


def test_a_loop_stopped_after_a_paid_fix_keeps_that_cost_as_partial(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    red_suite(tmp_path)
    open_gate(monkeypatch)
    monkeypatch.setattr(FixLoop, "run", one_fix_then_ctrl_c)
    fake_runner.streams["claude -p"] = FakeStream([PAID])
    error = error_of(cuanta(tmp_path, "loop", "--json"))
    runs = ledger_runs(tmp_path)
    [loop] = [run for run in runs if run.kind == "loop"]
    [fix] = [run for run in runs if run.parent_id == loop.id]
    assert error["message"] == f"interrupted · run {loop.id}"
    assert (fix.status, fix.cost_usd) == ("ok", 0.25)
    assert (loop.status, loop.cost_usd, loop.partial) == ("interrupted", 0.25, True)


def test_a_loop_whose_fix_fails_is_settled_as_failed(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    red_suite(tmp_path)
    open_gate(monkeypatch)
    monkeypatch.setattr(FixLoop, "run", one_fix_then_ctrl_c)
    del fake_runner.binaries["claude"]
    result = cuanta(tmp_path, "loop", "--json")
    assert result.exit_code == 3, result.stdout + result.stderr
    assert "claude" in json.loads(result.stdout)["error"]["message"]
    [loop] = ledger_runs(tmp_path)
    assert (loop.kind, loop.status, bool(loop.ended_at)) == ("loop", "failed", True)
    assert (loop.cost_usd, loop.partial) == (0.0, True)


def test_ctrl_c_while_a_finished_run_prints_names_it(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_runner.streams["claude -p"] = FakeStream([SUCCESS])
    with monkeypatch.context() as scoped:
        scoped.setattr(JsonPresenter, "render", ctrl_c)
        error = error_of(cuanta(tmp_path, "mandate", *asked("Explain the cart"), "--json"))
    [run] = recorded(tmp_path)
    assert run["status"] == "ok"
    assert error["message"] == f"interrupted · run {run['id']}"
    assert error["hint"] == f"see it: cuanta runs show {run['id']}"


def test_ctrl_c_while_the_queue_result_prints_names_its_last_run(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    for what in ("one", "two"):
        queue_add(tmp_path, what)
    fake_runner.queued["claude -p"] = [FakeStream([SUCCESS]), FakeStream([PAID])]
    with monkeypatch.context() as scoped:
        scoped.setattr(JsonPresenter, "render", ctrl_c)
        error = error_of(cuanta(tmp_path, "queue", "run", "--yes", "--json"))
    runs = ledger_runs(tmp_path)
    [last] = [run for run in runs if run.cost_usd == 0.25]
    assert [run.status for run in runs] == ["ok", "ok"]
    assert error["message"] == f"interrupted · run {last.id}"
    assert error["hint"] == f"see it: cuanta runs show {last.id}"
    assert queued(tmp_path) == []


def test_ctrl_c_while_the_init_result_prints_names_its_forge_run(
    tmp_path: Path, fake_claude: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = copy_repo("python_strong", tmp_path)
    with monkeypatch.context() as scoped:
        scoped.setattr(JsonPresenter, "render", ctrl_c)
        error = error_of(cuanta(root, "init", str(root), "--yes", "--skip-telemetry", "--json"))
    [run] = recorded(root)
    assert (run["kind"], run["status"]) == ("init", "ok")
    assert error["message"] == f"interrupted · run {run['id']}"
    assert error["hint"] == f"see it: cuanta runs show {run['id']}"


def test_ctrl_c_while_a_finished_team_prints_names_its_root(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_ready(fake_runner)
    with monkeypatch.context() as scoped:
        scoped.setattr(JsonPresenter, "render", ctrl_c)
        error = error_of(invoke(cross_args(tmp_path, *CODEX_TEAM)))
    [root] = [run for run in ledger_runs(tmp_path) if not run.parent_id]
    assert error["message"] == f"interrupted · run {root.id}"


def test_ctrl_c_while_an_isolated_copy_is_cleaned_names_its_run(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch = tmp_path / "temp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_bytes(b"print('broken')\n")
    fake_runner.streams["claude -p"] = FakeStream([SUCCESS])
    monkeypatch.setattr(SandboxResult, "shown", ctrl_c)
    fixing = ("--simple", "--type", "bug", "--what", "Fix it", "--why", "It breaks")
    stopped = cuanta(root, "mandate", *fixing, "--out-of-scope", "Nothing", "--sandbox", "--json")
    assert stopped.exit_code == INTERRUPTED, stopped.stdout + stopped.stderr
    error = json.loads(stopped.stderr.splitlines()[-1])["error"]
    [run] = recorded(root)
    assert json.loads(stopped.stdout)["run_id"] == run["id"]
    assert error["message"] == f"interrupted · run {run['id']}"
    assert error["hint"] == f"see it: cuanta runs show {run['id']}"


def test_ctrl_c_while_a_finished_loop_prints_names_it(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_gate(monkeypatch)

    def green(self: FixLoop, loop_id: str, max_iterations: int, budget_usd: float) -> LoopReport:
        return LoopReport(loop_id, (), StopReason.GREEN, 0.0, "green")

    monkeypatch.setattr(FixLoop, "run", green)
    with monkeypatch.context() as scoped:
        scoped.setattr(JsonPresenter, "render", ctrl_c)
        error = error_of(cuanta(tmp_path, "loop", "--json"))
    [loop] = ledger_runs(tmp_path)
    assert (loop.status, error["message"]) == ("green", f"interrupted · run {loop.id}")
