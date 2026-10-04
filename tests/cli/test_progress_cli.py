from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from cuanta.application.code_index import IndexService
from cuanta.application.mandate_flow import MandateOptions
from cuanta.application.progress import SlowSteps
from cuanta.application.route_apply import RouteOptions
from cuanta.bootstrap import Container
from cuanta.domain.code_index import IndexStatus
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.progress import ProgressEvent, StepFinished, StepStarted
from cuanta.tui.services import ContainerServices
from tests.cli.test_engine_guarantees import codex_ready, cross_args, forge
from tests.fakes import FakeRunner, FakeStream, VirtualTime
from tests.support import invoke, strip_ansi

RESULT = (
    '{"type":"result","subtype":"success","total_cost_usd":0.01,"is_error":false,'
    '"result":"## SUMMARY\\nok"}'
)


def project(root: Path) -> Path:
    forge(root)
    (root / "cart.py").write_text(
        "def total(a: int, b: int) -> int:\n    return a - b\n", encoding="utf-8"
    )
    return root


@pytest.fixture
def virtual(monkeypatch: pytest.MonkeyPatch) -> VirtualTime:
    time = VirtualTime()

    def slow_steps(self: Container) -> SlowSteps:
        return SlowSteps(self.progress, time.monotonic, time.schedule)

    monkeypatch.setattr(Container, "slow_steps", slow_steps)
    return time


def slow_index(
    monkeypatch: pytest.MonkeyPatch,
    time: VirtualTime,
    seconds: float,
    stop: Callable[[], None] | None = None,
    then: float = 0.0,
) -> list[IndexStatus]:
    original = IndexService.update
    seen: list[IndexStatus] = []

    def update(self: IndexService) -> IndexStatus:
        first = not seen
        time.advance(seconds if first else then)
        if first and stop is not None:
            stop()
        status = original(self)
        seen.append(status)
        return status

    monkeypatch.setattr(IndexService, "update", update)
    return seen


def files_line(seen: list[IndexStatus]) -> str:
    files = seen[0].files
    return "1 file" if files == 1 else f"{files} files"


def dry_run(root: Path, *extra: str) -> list[str]:
    return cross_args(root, "--route", "fixed", "--dry-run", *extra)


def test_a_slow_index_is_the_first_plain_line_of_a_dry_run(
    tmp_path: Path,
    fake_runner: FakeRunner,
    virtual: VirtualTime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = slow_index(monkeypatch, virtual, 52.0)
    result = invoke([*dry_run(project(tmp_path)), "--plain"])
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == f"+ index {files_line(seen)} · 52 s"
    assert [line for line in lines if line.startswith("+ ")] == [lines[0]]
    assert len(seen) > 1
    assert not fake_runner.stdins


def test_the_pretty_dry_run_shows_the_index_step_before_the_prompt(
    tmp_path: Path,
    fake_runner: FakeRunner,
    virtual: VirtualTime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = slow_index(monkeypatch, virtual, 52.0)
    result = invoke(dry_run(project(tmp_path)), pretty=True)
    assert result.exit_code == 0, result.output
    text = strip_ansi(result.stdout)
    shown = f"✓ index  {files_line(seen)} · 52 s"
    assert shown in text
    assert text.index(shown) < text.index("=== REQUEST ===")
    assert text.count(shown) == 1


def test_a_json_dry_run_streams_the_index_step_with_its_seconds_on_stderr(
    tmp_path: Path,
    fake_runner: FakeRunner,
    virtual: VirtualTime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = slow_index(monkeypatch, virtual, 52.0)
    result = invoke([*dry_run(project(tmp_path)), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["dry_run"] is True
    events = [json.loads(line) for line in result.stderr.splitlines() if line.strip()]
    assert [(event["event"], event["key"]) for event in events] == [
        ("StepStarted", "index"),
        ("StepFinished", "index"),
    ]
    assert events[1]["status"] == "ok"
    assert events[1]["seconds"] == 52.0
    assert events[1]["detail"] == f"{files_line(seen)} · 52 s"


@pytest.mark.parametrize("mode", ["--plain", "--json", ""])
def test_a_fast_dry_run_prints_no_progress(
    tmp_path: Path,
    fake_runner: FakeRunner,
    virtual: VirtualTime,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    slow_index(monkeypatch, virtual, 1.5)
    flags = [mode] if mode else []
    result = invoke([*dry_run(project(tmp_path)), *flags], pretty=not mode)
    assert result.exit_code == 0, result.output
    text = strip_ansi(result.output)
    assert "index" not in text.split("=== REQUEST ===")[0]
    assert "Step" not in result.stderr


def test_cuanta_index_prints_the_index_step_when_it_is_slow(
    tmp_path: Path,
    fake_runner: FakeRunner,
    virtual: VirtualTime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = slow_index(monkeypatch, virtual, 52.6)
    result = invoke(["index", "--rebuild", "--plain", "--project", str(project(tmp_path))])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0] == f"+ index {files_line(seen)} · 53 s"
    status = invoke(["index", "--status", "--plain", "--project", str(tmp_path)])
    assert "+ index" not in status.stdout


def test_ctrl_c_during_a_shown_index_step_closes_it_before_interrupted(
    tmp_path: Path,
    fake_runner: FakeRunner,
    virtual: VirtualTime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupt() -> None:
        raise KeyboardInterrupt

    slow_index(monkeypatch, virtual, 5.0, interrupt)
    result = invoke([*dry_run(project(tmp_path)), "--plain"])
    assert result.exit_code == 130
    assert result.stdout.splitlines() == ["x index 5 s"]
    assert "interrupted" in result.stderr


def test_the_app_run_receives_the_index_step_of_a_slow_first_index(
    tmp_path: Path,
    fake_runner: FakeRunner,
    virtual: VirtualTime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = project(tmp_path)
    seen = slow_index(monkeypatch, virtual, 52.0)
    fake_runner.streams["claude -p"] = FakeStream([RESULT])
    events: list[ProgressEvent] = []
    ContainerServices(root).run_mandate(
        MandateRequest(
            "bug", "Fix incorrect addition", "The sum is wrong", out_of_scope="Documentation"
        ),
        0,
        MandateOptions(profile="balanced", route=RouteOptions(mode="fixed")),
        lambda event: None,
        events.append,
    )
    steps = [event for event in events if isinstance(event, StepStarted | StepFinished)]
    indexed = [event for event in steps if event.key == "index"]
    assert [type(event) for event in indexed] == [StepStarted, StepFinished]
    finished = indexed[1]
    assert isinstance(finished, StepFinished)
    assert (finished.detail, finished.seconds) == (f"{files_line(seen)} · 52 s", 52.0)


def test_a_dry_run_whose_updates_are_all_slow_lists_index_then_plan(
    tmp_path: Path,
    fake_runner: FakeRunner,
    virtual: VirtualTime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = slow_index(monkeypatch, virtual, 52.0, then=3.0)
    result = invoke([*dry_run(project(tmp_path)), "--plain"])
    assert result.exit_code == 0, result.output
    steps = [line for line in result.stdout.splitlines() if line.startswith(("+ ", "x "))]
    assert steps[0] == f"+ index {files_line(seen)} · 52 s"
    assert len(steps) == 2
    assert re.fullmatch(r"\+ plan \d+ s", steps[1])


def test_a_per_role_dry_run_gets_the_index_step_only(
    tmp_path: Path,
    fake_runner: FakeRunner,
    virtual: VirtualTime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_ready(fake_runner)
    seen = slow_index(monkeypatch, virtual, 52.0, then=52.0)
    result = invoke([*dry_run(project(tmp_path), "--engine", "codex"), "--plain"])
    assert result.exit_code == 0, result.output
    steps = [line for line in result.stdout.splitlines() if line.startswith(("+ ", "x "))]
    assert steps == [f"+ index {files_line(seen)} · 52 s"]
    assert len(seen) > 1
    assert not fake_runner.stdins
