from __future__ import annotations

from pathlib import Path

from cuanta.adapters.testing.cargo_runner import parse_cargo_text
from cuanta.adapters.testing.generic_runner import parse_text
from cuanta.adapters.testing.go_runner import GoRunner, parse_go_json
from cuanta.adapters.testing.jest_runner import JestRunner, parse_jest_json
from cuanta.adapters.testing.junit import parse_junit
from cuanta.adapters.testing.pytest_runner import PytestRunner
from cuanta.adapters.testing.vitest_runner import VitestRunner
from cuanta.domain.testing import cluster
from tests.fakes import FakeRunner

RUNNERS = Path(__file__).parents[1] / "fixtures" / "runners"


def _read(name: str) -> str:
    return (RUNNERS / name).read_text(encoding="utf-8")


def test_junit() -> None:
    outcome = parse_junit(_read("junit.xml"), 1)
    assert outcome is not None
    assert (outcome.passed, outcome.failed, outcome.errored, outcome.skipped) == (2, 1, 1, 1)
    assert outcome.duration_s == 0.321
    first, second = outcome.failures
    assert first.test == "tests/test_api.py::test_post"
    assert first.error_type == "AssertionError"
    assert first.frames[-1].path == "tests/test_api.py"
    assert second.error_type == "ConnectionError"
    assert "refused" in second.message


def test_junit_garbage_returns_none() -> None:
    assert parse_junit("<not xml") is None


def test_jest() -> None:
    outcome = parse_jest_json(_read("jest.json"), 1, 0.0)
    assert outcome is not None
    assert (outcome.passed, outcome.failed, outcome.skipped) == (4, 3, 1)
    assert outcome.duration_s == 0.65
    signatures = cluster(outcome.failures, "/repo")
    assert [signature.tests for signature in signatures] == [2, 1]
    assert signatures[0].location == "src/sum.test.js:5"
    assert signatures[1].verbatim.startswith("TypeError: Cannot read properties")
    assert signatures[1].location == "src/user.js:12"


def test_vitest() -> None:
    outcome = parse_jest_json(_read("vitest.json"), 1, 0.0)
    assert outcome is not None
    assert (outcome.passed, outcome.failed) == (2, 1)
    assert outcome.failures[0].error_type == "AssertionError"


def test_go() -> None:
    outcome = parse_go_json(_read("go.jsonl"), 1, 0.0)
    assert (outcome.passed, outcome.failed, outcome.skipped) == (1, 1, 1)
    failure = outcome.failures[0]
    assert failure.test == "github.com/acme/api/cart::TestTotal"
    assert failure.message == "expected 30, got 31"
    assert outcome.duration_s == 0.52


def test_go_build_failure_is_an_error() -> None:
    outcome = parse_go_json("# pkg\n./main.go:3:2: undefined: x\nFAIL pkg [build failed]", 1, 0.2)
    assert outcome.errored == 1
    assert outcome.failures[0].error_type == "BuildError"


def test_cargo() -> None:
    outcome = parse_cargo_text(_read("cargo.txt"), 101, 0.0)
    assert (outcome.passed, outcome.failed, outcome.skipped) == (1, 1, 1)
    failure = outcome.failures[0]
    assert failure.test == "tests::divides"
    assert failure.message == "assertion `left == right` failed"
    assert failure.frames[0].path == "src/lib.rs"


def test_generic_regex_fallback() -> None:
    output = "running\nFAIL: test_login - boom\n  AssertionError: expected 1\n3 passed, 1 failed\n"
    outcome = parse_text(output, 1, 0.3)
    assert (outcome.passed, outcome.failed) == (3, 1)
    assert outcome.failures[0].test == "test_login"
    assert outcome.failures[0].error_type == "AssertionError"


def test_generic_nonzero_exit_without_counts_is_red() -> None:
    outcome = parse_text("segfault\n", 139, 0.1)
    assert outcome.red
    assert outcome.failures[0].message == "segfault"


def test_command_builders(tmp_path: Path) -> None:
    report = tmp_path / "r"
    runner = FakeRunner()
    assert PytestRunner(runner).build(["uv", "run", "pytest"], report) == [
        "uv",
        "run",
        "pytest",
        f"--junitxml={report}",
        "-q",
    ]
    assert JestRunner(runner).build(["npm", "test"], report) == [
        "npm",
        "test",
        "--",
        "--json",
        f"--outputFile={report}",
    ]
    assert VitestRunner(runner).build(["npx", "vitest"], report) == [
        "npx",
        "vitest",
        "run",
        "--reporter=json",
        f"--outputFile={report}",
    ]
    assert GoRunner(runner).build(["go", "test", "./..."], report) == [
        "go",
        "test",
        "-json",
        "./...",
    ]
