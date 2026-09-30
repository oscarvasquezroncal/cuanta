from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import pytest

from cuanta.application.verification import (
    DIRECT_LOCATION,
    SUMMARY,
    Diagnostics,
    Verifier,
    command_argv,
    first_errors,
)
from cuanta.domain.implementation import diagnostic_identity, without_duration
from tests.fakes import FakeRunner, FakeStream

ROOT = "C:/work/copy"


def test_typescript_and_next_errors_become_relative_path_line_entries() -> None:
    output = "\n".join(
        [
            "src/app/layout.tsx(12,5): error TS2322: Type 'string' is not assignable.",
            "Failed to compile.",
            "C:\\work\\copy\\src\\app\\page.tsx:40:3",
            "./src/lib/seo.ts:7:1 Type error: Cannot find name 'site'.",
            "info  - Linting and checking validity of types",
        ]
    )
    errors = first_errors(output, ROOT)
    assert errors[:3] == (
        "src/app/layout.tsx:12:5 error TS2322: Type 'string' is not assignable.",
        "src/app/page.tsx:40:3",
        "src/lib/seo.ts:7:1 Type error: Cannot find name 'site'.",
    )
    assert "Failed to compile." in errors
    assert len(first_errors("\n".join(f"a{n}.ts:{n} error" for n in range(20)), ROOT)) == 6


def test_long_noisy_lines_are_scanned_in_linear_time() -> None:
    noisy = "\n".join(("A" * 5000, "-" * 5000, "x." * 3000, "error " + "b/" * 4000))
    started = time.perf_counter()
    found = first_errors(noisy * 20, ROOT)
    assert time.perf_counter() - started < 2.0
    assert found and all(len(item) <= 220 for item in found)


@pytest.mark.parametrize(
    "frame",
    [
        "    at Object.<anonymous> (src/a.test.ts:10:15)",
        "    at processTicksAndRejections (node:internal/process/task_queues:95:5)",
        "> 10 |   expect(total).toBe(3);",
        "  11 |   foo.bar(2)",
        "10     const y = x.map(1);",
    ],
)
def test_stack_and_code_frames_never_become_line_keyed_paths(frame: str) -> None:
    shifted = frame.replace("10", "25").replace("11", "26")
    assert DIRECT_LOCATION.match(frame.strip()) is None
    assert DIRECT_LOCATION.match(shifted.strip()) is None
    before = first_errors(frame, ROOT, maximum_chars=None)
    after = first_errors(shifted, ROOT, maximum_chars=None)
    assert [diagnostic_identity(error) for error in before] == [
        diagnostic_identity(error) for error in after
    ]


@pytest.mark.parametrize(
    "line",
    [
        "==== 1 failed, 5 passed in 0.12s ====",
        "1 failed, 5 passed in 0.12s",
        "tests/test_a.py::test_x FAILED                          [ 16%]",
        "✖ 1 problem (1 error, 0 warnings)",
        "  1 error and 0 warnings potentially fixable with the `--fix` option.",
        "Found 1 error in 1 file (checked 10 source files)",
        "Found 2 errors in the same file, starting at: src/a.ts:10",
        "Tests:       1 failed, 5 passed, 6 total",
        " Test Files  1 failed | 3 passed (4)",
        " ⎯⎯⎯ Failed Tests 2 ⎯⎯⎯",
        "FAILED (failures=1)",
        "test result: FAILED. 1 passed; 1 failed; 0 ignored; finished in 0.01s",
        "2 errors, 0 warnings, 0 informations",
        " ❯ src/a.test.ts (6 tests | 1 failed) 5ms",
        " ❯ |web| src/a.test.ts (6 tests | 1 failed | 2 skipped) 1.2s",
    ],
)
def test_tool_summaries_are_recognized(line: str) -> None:
    assert SUMMARY.fullmatch(line.strip()) is not None


@pytest.mark.parametrize(
    "line",
    [
        "FAILED tests/test_a.py::test_x[1] - assert 1 == 2",
        "ERROR tests/test_b.py - ModuleNotFoundError: No module named 'x'",
        "src/a.ts(10,5): error TS2322: Type 'string' is not assignable.",
        "  10:5  error  'x' is not defined  no-undef",
        "10:5  Error: 'x' is not defined.  no-undef",
        "Error: Cannot find module './b'",
        "tests/test_a.py:10: AssertionError",
        "FAILED tests/test_a.py::test_count - AssertionError: expected (2 tests)",
        " ❯ src/a.test.ts:4:21",
    ],
)
def test_diagnostics_are_never_summaries(line: str) -> None:
    assert SUMMARY.fullmatch(line.strip()) is None


def test_failure_titles_become_entries_but_console_and_file_headers_do_not() -> None:
    diagnostics = Diagnostics(ROOT)
    for line in (
        "  ● Console",
        " FAIL  src/a.test.js",
        "  ● math › adds",
        " FAIL  src/a.test.ts > math > adds",
        "FAIL: test_c (tests.test_a.T.test_c)",
        "    2) fails a",
        "Tests:       1 failed, 5 passed, 6 total",
    ):
        diagnostics.collect(line)
    assert diagnostics.found() == (
        "src/a.test.js ● math › adds",
        "FAIL src/a.test.ts > math > adds",
        "FAIL: test_c (tests.test_a.T.test_c)",
        "2) fails a",
    )
    assert diagnostic_identity("2) fails a") == diagnostic_identity("1) fails a") == "fails a"


def test_jest_titles_carry_their_file_and_count_while_pass_lines_are_dropped() -> None:
    diagnostics = Diagnostics(ROOT)
    for line in (
        " FAIL  C:\\work\\copy\\src\\App.test.js (5.123 s)",
        "  ● renders without crashing",
        " PASS  src/ok.test.js",
        "    ✓ returns an error object (1 ms)",
        "    √ shows an error message (2 ms)",
        "    ✔ handles a failure",
        " FAIL  src/Header.test.js",
        "  ● renders without crashing",
        "",
        "  ● renders without crashing",
        " FAIL  src/App.test.js (6.2 s, 120 MB heap size)",
        "  ● renders without crashing",
    ):
        diagnostics.collect(line)
    assert diagnostics.found() == (
        "src/App.test.js ● renders without crashing",
        "src/App.test.js ● renders without crashing",
        "src/Header.test.js ● renders without crashing",
        "src/Header.test.js ● renders without crashing",
    )


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("× throws an error on bad input 3ms", "× throws an error on bad input 14ms"),
        ("✕ returns error object (1 ms)", "✕ returns error object (12 ms)"),
        ("FAIL src/error.test.js (5.123 s)", "FAIL src/error.test.js (6 s)"),
        ("(fail) handles an error [0.12ms]", "(fail) handles an error [1.5ms]"),
    ],
)
def test_a_trailing_duration_never_changes_a_general_line(before: str, after: str) -> None:
    assert without_duration(before) == without_duration(after)
    assert without_duration(before) != without_duration(after.replace("error", "failure"))
    assert diagnostic_identity("FAIL src/a.test.ts > waits 5s") != diagnostic_identity(
        "FAIL src/a.test.ts > waits 60s"
    )


def test_direct_locations_keep_compiler_shapes_and_paths_with_spaces() -> None:
    expected = {
        "C:/work/copy/Old Folder/a.ts(4,2): error TS2322: bad": (
            "Old Folder/a.ts:4:2 error TS2322: bad"
        ),
        "C:/work/copy/My App/b.ts:7:3 - error TS2304: missing": (
            "My App/b.ts:7:3 error TS2304: missing"
        ),
        'src/c.py:9: error: Name "x" is not defined  [name-defined]': (
            'src/c.py:9 error: Name "x" is not defined [name-defined]'
        ),
        "C:/work/copy/My App/page.tsx:40:3": "My App/page.tsx:40:3",
    }
    for line, entry in expected.items():
        assert first_errors(line, ROOT) == (entry,)
    moved = first_errors("C:/work/copy/My App/page.tsx:42:3", ROOT)
    assert diagnostic_identity(moved[0]) == diagnostic_identity("My App/page.tsx:40:3")


def test_commands_split_for_each_platform() -> None:
    assert command_argv("npx tsc --noEmit", windows=True) == ["npx", "tsc", "--noEmit"]
    assert command_argv('npm run "lint fix"', windows=False) == ["npm", "run", "lint fix"]
    assert command_argv('npm run "lint fix"', windows=True) == ["npm", "run", "lint fix"]


def test_the_verifier_runs_each_command_and_keeps_failures_explicit(tmp_path: Path) -> None:
    runner = FakeRunner(
        streams={
            "npx tsc": FakeStream(["src/a.ts(3,1): error TS1005: ';' expected."], 2),
            "npm run lint": FakeStream(["ok"], 0),
        }
    )
    results = Verifier(runner, tmp_path, windows=True, env={"A": "1"}).run(
        ("npx tsc --noEmit", "npm run lint", "")
    )
    assert [result.command for result in results] == ["npx tsc --noEmit", "npm run lint"]
    assert [result.exit_code for result in results] == [2, 0]
    assert results[0].errors == ("src/a.ts:3:1 error TS1005: ';' expected.",)
    assert results[1].passed and results[1].errors == ()
    assert runner.cwds == [tmp_path] * 2
    assert runner.envs == [{"A": "1"}] * 2
    assert runner.calls[0] == ("npx", "tsc", "--noEmit")


class Hanging(FakeStream):
    def __init__(self) -> None:
        super().__init__(["building"], 1)
        self.stop = threading.Event()

    def lines(self) -> Iterator[str]:
        yield from self.output
        self.stop.wait(10)

    def close(self) -> None:
        self.closed = True
        self.stop.set()


class Missing(FakeRunner):
    def stream(
        self,
        args: Sequence[str],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        stdin_text: str | None = None,
        unset: Sequence[str] = (),
        keep_stdin: bool = False,
    ) -> FakeStream:
        raise FileNotFoundError("[WinError 2] not found")


def test_a_hung_command_is_closed_as_a_tree_at_its_timeout(tmp_path: Path) -> None:
    hanging = Hanging()
    runner = FakeRunner(streams={"npm run build": hanging})
    result = Verifier(runner, tmp_path, windows=False, timeout_s=0.1).run(("npm run build",))[0]
    assert result.timed_out and not result.passed and hanging.closed
    missing = Verifier(Missing(), tmp_path, windows=True).run(("nothing here",))[0]
    assert missing.exit_code is None and missing.errors == ("[WinError 2] not found",)


def test_a_stop_closes_the_running_command_and_no_later_command_starts(tmp_path: Path) -> None:
    hanging = Hanging()
    runner = FakeRunner(streams={"npm run build": hanging})
    requested = threading.Event()
    press = threading.Timer(0.1, requested.set)
    press.daemon = True
    press.start()
    started = time.monotonic()
    results = Verifier(runner, tmp_path, windows=False).run(
        ("npm run build", "npm run lint"), requested.is_set
    )
    assert time.monotonic() - started < 5.0
    assert hanging.closed
    assert runner.calls == [("npm", "run", "build")]
    assert [result.command for result in results] == ["npm run build"]
    assert results[0].exit_code is None and not results[0].timed_out and not results[0].passed
    assert results[0].errors == ("stopped before it finished",)


def test_a_stop_before_the_checks_runs_nothing_and_no_stop_runs_everything(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(streams={"npm run": FakeStream(["ok"], 0)})
    verifier = Verifier(runner, tmp_path, windows=False)
    assert verifier.run(("npm run build", "npm run lint"), lambda: True) == ()
    assert runner.calls == []
    results = verifier.run(("npm run build", "npm run lint"), lambda: False)
    assert [result.passed for result in results] == [True, True]
    assert len(runner.calls) == 2


def test_an_unparseable_command_is_reported_without_running(tmp_path: Path) -> None:
    runner = FakeRunner()
    results = Verifier(runner, tmp_path, windows=False).run(('npm run "unclosed',))
    assert results[0].exit_code is None
    assert results[0].errors == ("could not parse the command",)
    assert runner.calls == []
