from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path

from cuanta.domain.testing import TestOutcome
from cuanta.ports.system import ProcessRunner
from cuanta.ports.test_runner import RunnerResult

TEST_TIMEOUT_S = 3600.0


def split_command(command: str) -> tuple[str, ...]:
    return tuple(shlex.split(command, posix=True))


def join_command(args: Sequence[str]) -> str:
    return " ".join(shlex.quote(arg) if " " in arg else arg for arg in args)


def with_script_separator(base: Sequence[str], extra: Sequence[str]) -> list[str]:
    command = list(base)
    if command and command[0] in {"npm"} and "--" not in command:
        command.append("--")
    return [*command, *extra]


def read_report(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


class ReportRunner:
    runner_name = "generic"
    report_name = "report"

    def __init__(self, process: ProcessRunner) -> None:
        self._process = process

    @property
    def name(self) -> str:
        return self.runner_name

    def default_command(self) -> tuple[str, ...]:
        return ()

    def build(self, base: Sequence[str], report: Path) -> list[str]:
        return list(base)

    def parse(
        self, report: str | None, output: str, exit_code: int, duration_s: float
    ) -> TestOutcome:
        raise NotImplementedError

    def run(
        self,
        base_command: Sequence[str],
        root: Path,
        scratch: Path,
        env: Mapping[str, str] | None = None,
    ) -> RunnerResult:
        scratch.mkdir(parents=True, exist_ok=True)
        report = scratch / self.report_name
        if report.exists():
            report.unlink()
        command = self.build(base_command or self.default_command(), report)
        completed = self._process.run(command, cwd=root, env=env, timeout=TEST_TIMEOUT_S)
        output = completed.stdout
        if completed.stderr:
            output = f"{output}\n{completed.stderr}" if output else completed.stderr
        outcome = self.parse(
            read_report(report), output, completed.returncode, completed.duration_s
        )
        return RunnerResult(outcome=outcome, output=output, command=join_command(command))
