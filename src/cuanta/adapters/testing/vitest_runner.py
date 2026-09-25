from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from cuanta.adapters.testing.base import ReportRunner, with_script_separator
from cuanta.adapters.testing.generic_runner import parse_text
from cuanta.adapters.testing.jest_runner import parse_jest_json
from cuanta.domain.testing import TestOutcome


class VitestRunner(ReportRunner):
    runner_name = "vitest"
    report_name = "vitest-report.json"

    def default_command(self) -> tuple[str, ...]:
        return ("npx", "vitest", "run")

    def build(self, base: Sequence[str], report: Path) -> list[str]:
        command = list(base)
        if command[-1:] == ["vitest"]:
            command.append("run")
        return with_script_separator(command, ["--reporter=json", f"--outputFile={report}"])

    def parse(
        self, report: str | None, output: str, exit_code: int, duration_s: float
    ) -> TestOutcome:
        if report is not None:
            parsed = parse_jest_json(report, exit_code, duration_s)
            if parsed is not None:
                return parsed
        return parse_text(output, exit_code, duration_s)
