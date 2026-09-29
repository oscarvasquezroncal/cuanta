from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

from cuanta.application.verification import Verifier, command_argv, first_errors
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
        "src/app/layout.tsx:12 error TS2322: Type 'string' is not assignable.",
        "src/app/page.tsx:40",
        "src/lib/seo.ts:7 Type error: Cannot find name 'site'.",
    )
    assert "Failed to compile." in errors
    assert len(first_errors("\n".join(f"a{n}.ts:{n} error" for n in range(20)), ROOT)) == 6


def test_long_noisy_lines_are_scanned_in_linear_time() -> None:
    noisy = "\n".join(("A" * 5000, "-" * 5000, "x." * 3000, "error " + "b/" * 4000))
    started = time.perf_counter()
    found = first_errors(noisy * 20, ROOT)
    assert time.perf_counter() - started < 2.0
    assert found and all(len(item) <= 220 for item in found)


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
    assert results[0].errors == ("src/a.ts:3 error TS1005: ';' expected.",)
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
