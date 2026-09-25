from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from cuanta.adapters.testing.base import ReportRunner
from cuanta.adapters.testing.generic_runner import parse_text
from cuanta.adapters.testing.junit import parse_junit
from cuanta.domain.testing import TestOutcome


class PytestRunner(ReportRunner):
    runner_name = "pytest"
    report_name = "pytest-junit.xml"

    def default_command(self) -> tuple[str, ...]:
        return ("pytest",)

    def build(self, base: Sequence[str], report: Path) -> list[str]:
        return [*base, f"--junitxml={report}", "-q"]

    def parse(
        self, report: str | None, output: str, exit_code: int, duration_s: float
    ) -> TestOutcome:
        if report is not None:
            parsed = parse_junit(report, exit_code, duration_s)
            if parsed is not None:
                return parsed
        return parse_text(output, exit_code, duration_s)
