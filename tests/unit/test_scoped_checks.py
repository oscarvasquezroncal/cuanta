from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from cuanta.application.scoped_checks import ScopedChecks
from cuanta.application.verification import Verifier
from cuanta.domain.change_plan import ChangePlan, EditTarget
from cuanta.domain.scoped_checks import (
    DMYPY_STATUS,
    affected_tests,
    changed_paths,
    check_workers,
    daemon_command,
    plan_sources,
    runner_of,
    scoped_test_command,
)
from tests.fakes import FakeRunner, FakeStream


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("uv run mypy", f"uv run dmypy --status-file {DMYPY_STATUS} run --"),
        ("uv run mypy src tests", f"uv run dmypy --status-file {DMYPY_STATUS} run -- src tests"),
        ("mypy --strict", f"dmypy --status-file {DMYPY_STATUS} run -- --strict"),
        ("python -m mypy", f"python -m mypy.dmypy --status-file {DMYPY_STATUS} run --"),
        ("npx tsc --noEmit", None),
        ("uv run dmypy run", None),
        ("uv run pytest", None),
    ],
)
def test_only_mypy_has_a_daemon_variant(command: str, expected: str | None) -> None:
    assert daemon_command(command) == expected


@pytest.mark.parametrize(
    ("command", "runner"),
    [
        ("uv run pytest", "pytest"),
        ("python -m pytest -q", "pytest"),
        ("npx vitest run", "vitest"),
        ("npx jest", "jest"),
        ("go test ./...", "go"),
        ("npm test", ""),
        ("uv run mypy", ""),
    ],
)
def test_the_runner_is_read_from_the_command(command: str, runner: str) -> None:
    assert runner_of(command) == runner


def test_a_pytest_selection_stops_at_the_first_failure_without_cache() -> None:
    tests = ("tests/test_cart.py", "tests/test_total.py")
    assert scoped_test_command("uv run pytest", "pytest", tests, False) == (
        "uv run pytest -x -q -p no:cacheprovider tests/test_cart.py tests/test_total.py"
    )
    assert scoped_test_command("uv run pytest", "pytest", tests, True) == (
        "uv run pytest -x -q -p no:cacheprovider -n auto tests/test_cart.py tests/test_total.py"
    )
    assert scoped_test_command("npx vitest run", "vitest", tests, True) == (
        "npx vitest run tests/test_cart.py tests/test_total.py"
    )
    assert scoped_test_command("npm test", "", tests, False) is None
    spaced = scoped_test_command("uv run pytest", "pytest", ("tests/my cart/test_a.py",), False)
    assert spaced == 'uv run pytest -x -q -p no:cacheprovider "tests/my cart/test_a.py"'


def test_the_sources_are_the_strong_edit_targets_or_the_best_weak_ones() -> None:
    plan = ChangePlan(
        (
            EditTarget("src/cart.py", 1.0),
            EditTarget("src/other.py", 0.75),
            EditTarget("tests/test_linked.py", 0.7),
        ),
    )
    assert plan_sources(plan) == ("src/cart.py",)
    weak = ChangePlan(
        (
            EditTarget("src/a.py", 0.55),
            EditTarget("src/b.py", 0.75),
            EditTarget("tests/test_b.py", 0.7),
            EditTarget("src/c.py", 0.6),
            EditTarget("src/d.py", 0.55),
        ),
    )
    assert plan_sources(weak) == ("src/b.py", "src/c.py", "src/a.py")
    assert plan_sources(ChangePlan()) == ()


def test_affected_tests_follow_the_stem_and_the_graph_and_cap_at_two_hundred() -> None:
    files = ("src/cart.py", "tests/test_cart.py", "tests/test_total.py", "src/total.py")
    neighbours = {"src/cart.py": frozenset({"tests/test_total.py"})}
    assert affected_tests("pytest", ("src/cart.py",), files, neighbours) == (
        ("tests/test_cart.py", "tests/test_total.py"),
        False,
    )
    assert affected_tests("pytest", ("README.md",), files, {}) == ((), False)
    assert affected_tests("pytest", (), files, {}) == ((), False)
    assert affected_tests("go", ("src/cart.py",), files, {}) == ((), False)
    many = tuple(f"tests/test_cart_{index}.py" for index in range(201))
    changed = tuple(test for test in many)
    assert affected_tests("pytest", changed, (*files, *many), {}) == ((), True)


def test_changed_paths_are_the_new_and_edited_files() -> None:
    before = {"tests/test_cart.py": "a", "src/cart.py": "c"}
    after = {"tests/test_cart.py": "a", "tests/test_new.py": "d", "src/cart.py": "e"}
    assert changed_paths(before, after) == ("src/cart.py", "tests/test_new.py")


def test_checks_share_the_cores_they_have() -> None:
    assert check_workers(3, 8) == 3
    assert check_workers(3, 2) == 2
    assert check_workers(0, 4) == 1
    assert check_workers(3, None) == 1


def test_all_checks_overlap_and_a_mapped_command_reports_under_its_own_name(
    tmp_path: Path,
) -> None:
    barrier = threading.Barrier(3)

    class Overlapping(FakeStream):
        def lines(self) -> Iterator[str]:
            barrier.wait(timeout=2)
            yield "ok"

    runner = FakeRunner(
        streams={
            "uv run dmypy": Overlapping([]),
            "uv run pytest": Overlapping([]),
            "uv build": Overlapping([]),
        }
    )
    verifier = Verifier(runner, tmp_path, False, cores=4)
    actual = {"uv run mypy": "uv run dmypy --status-file x run --"}
    results = verifier.run_checks(("uv run mypy", "uv run pytest", "uv build"), actual=actual)
    assert [result.command for result in results] == ["uv run mypy", "uv run pytest", "uv build"]
    assert all(result.passed for result in results)
    assert ("uv", "run", "dmypy", "--status-file", "x", "run", "--") in runner.calls


class Snapshots:
    def __init__(self, states: list[Mapping[str, str]]) -> None:
        self._states = states

    def __call__(self) -> Mapping[str, str]:
        return self._states.pop(0) if len(self._states) > 1 else self._states[0]


def test_session_checks_use_the_daemon_and_the_selection_and_the_final_check_uses_mypy(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    verifier = Verifier(runner, tmp_path, False, cores=4)
    before = {"src/cart.py": "a", "tests/test_cart.py": "b"}
    after = {"src/cart.py": "z", "tests/test_cart.py": "b", "tests/test_new.py": "n"}
    scoped = ScopedChecks(
        verifier,
        ("uv run mypy", "uv build", "uv run pytest"),
        ("src/cart.py",),
        Snapshots([before, after]),
        parallel=False,
    )
    scoped.baseline(("uv run mypy", "uv build", "uv run pytest"), lambda: False)
    status = ("--status-file", DMYPY_STATUS)
    assert ("uv", "run", "dmypy", *status, "run", "--") in runner.calls
    assert (
        "uv",
        "run",
        "pytest",
        "-x",
        "-q",
        "-p",
        "no:cacheprovider",
        "tests/test_cart.py",
    ) in runner.calls
    runner.calls.clear()
    scoped(("uv run mypy", "uv build", "uv run pytest"), lambda: False)
    assert (
        "uv",
        "run",
        "pytest",
        "-x",
        "-q",
        "-p",
        "no:cacheprovider",
        "tests/test_cart.py",
        "tests/test_new.py",
    ) in runner.calls
    runner.calls.clear()
    final = scoped.final(("uv run mypy", "uv build", "uv run pytest"), lambda: False)
    assert [result.command for result in final] == ["uv run mypy", "uv build", "uv run pytest"]
    assert ("uv", "run", "mypy") in runner.calls
    runner.calls.clear()
    scoped.close()
    assert ("uv", "run", "dmypy", *status, "stop") in runner.calls


def test_without_selected_tests_the_session_skips_the_test_command_but_keeps_the_others(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    verifier = Verifier(runner, tmp_path, False, cores=2)
    scoped = ScopedChecks(
        verifier,
        ("uv run mypy", "uv run pytest"),
        ("src/cart.py",),
        Snapshots([{"src/cart.py": "a"}]),
        parallel=False,
    )
    results = scoped.baseline(("uv run mypy", "uv run pytest"), lambda: False)
    assert [result.command for result in results] == ["uv run mypy", "uv run pytest"]
    assert all(result.passed for result in results)
    assert not any("pytest" in call for call in runner.calls)


def test_a_broken_daemon_is_killed_and_run_once_more(tmp_path: Path) -> None:
    broken = FakeStream(["Daemon crashed!", "Traceback (most recent call last):", "OSError: x"], 2)
    key = f"uv run dmypy --status-file {DMYPY_STATUS} run"
    runner = FakeRunner(queued={key: [broken, FakeStream(["Success: no issues found"], 0)]})
    verifier = Verifier(runner, tmp_path, False, cores=2)
    scoped = ScopedChecks(verifier, ("uv run mypy",), (), Snapshots([{}]), parallel=False)
    results = scoped.baseline(("uv run mypy",), lambda: False)
    assert results[0].passed
    status = ("--status-file", DMYPY_STATUS)
    assert ("uv", "run", "dmypy", *status, "kill") in runner.calls


def test_a_green_session_check_is_confirmed_by_the_final_check_and_a_red_one_is_repaired() -> None:
    from cuanta.application.implementer import ImplementationSession
    from cuanta.domain.engine import EngineOutcome
    from tests.unit.test_implementer import COMMANDS, NEW_ERROR, CheckSequence, checks, result

    session_checks = CheckSequence(checks(), checks())
    final_checks = CheckSequence(checks(NEW_ERROR), checks())
    closed: list[bool] = []
    session = ImplementationSession(
        COMMANDS,
        checks(),
        session_checks,
        lambda: 0.0,
        final_verify=final_checks,
        close=lambda: closed.append(True),
    )
    turns: list[str] = []

    def send(text: str) -> bool:
        turns.append(text)
        return True

    session.on_result(result(), send)
    assert len(turns) == 1 and NEW_ERROR in turns[0]
    session.on_result(result(0.3), send)
    assert session.report.passed
    assert len(session_checks.calls) == 2 and len(final_checks.calls) == 2
    outcome = EngineOutcome(0, result(0.3), 2)
    assert session.settle(outcome) is outcome
    assert closed == [True]
    session.settle(outcome)
    assert closed == [True]


def test_a_daemon_that_stays_broken_falls_back_to_plain_mypy_for_the_session(
    tmp_path: Path,
) -> None:
    broken = FakeStream(["Traceback (most recent call last):", "OSError: pipe"], 2)
    key = f"uv run dmypy --status-file {DMYPY_STATUS} run"
    runner = FakeRunner(queued={key: [broken, broken]})
    verifier = Verifier(runner, tmp_path, False, cores=2)
    scoped = ScopedChecks(verifier, ("uv run mypy",), (), Snapshots([{}]), parallel=False)
    assert scoped.baseline(("uv run mypy",), lambda: False)[0].passed
    assert runner.calls[-1] == ("uv", "run", "mypy")
    runner.calls.clear()
    scoped(("uv run mypy",), lambda: False)
    assert runner.calls == [("uv", "run", "mypy")]
