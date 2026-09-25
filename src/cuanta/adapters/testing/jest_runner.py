from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from cuanta.adapters.testing.base import ReportRunner, with_script_separator
from cuanta.adapters.testing.generic_runner import parse_text
from cuanta.domain.testing import TestFailure, TestOutcome, frames_from_text, split_error


def _int(data: dict[str, Any], key: str) -> int:
    value = data.get(key, 0)
    return value if isinstance(value, int) else 0


def parse_jest_json(text: str, exit_code: int, duration_s: float) -> TestOutcome | None:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    failures: list[TestFailure] = []
    duration_ms = 0.0
    suite_errors = 0
    for suite in data.get("testResults") or []:
        if not isinstance(suite, dict):
            continue
        file = str(suite.get("name") or suite.get("testFilePath") or "")
        start, end = suite.get("startTime"), suite.get("endTime")
        if isinstance(start, int | float) and isinstance(end, int | float):
            duration_ms += max(float(end) - float(start), 0.0)
        assertions = suite.get("assertionResults") or []
        failed_here = False
        for assertion in assertions:
            if not isinstance(assertion, dict) or assertion.get("status") != "failed":
                continue
            failed_here = True
            messages = assertion.get("failureMessages") or []
            text_message = str(messages[0]) if messages else "failed"
            error_type, message = split_error(text_message, "AssertionError")
            name = str(assertion.get("fullName") or assertion.get("title") or "")
            failures.append(
                TestFailure(
                    test=f"{file}::{name}" if file else name,
                    error_type=error_type,
                    message=message,
                    frames=frames_from_text(text_message),
                )
            )
        suite_message = suite.get("message") or suite.get("failureMessage")
        if suite.get("status") == "failed" and not failed_here and isinstance(suite_message, str):
            suite_errors += 1
            error_type, message = split_error(suite_message, "SuiteError")
            failures.append(
                TestFailure(
                    test=file,
                    error_type=error_type,
                    message=message,
                    frames=frames_from_text(suite_message),
                )
            )
    return TestOutcome(
        passed=_int(data, "numPassedTests"),
        failed=_int(data, "numFailedTests"),
        errored=_int(data, "numRuntimeErrorTestSuites") or suite_errors,
        skipped=_int(data, "numPendingTests") + _int(data, "numTodoTests"),
        duration_s=duration_ms / 1000 if duration_ms else duration_s,
        failures=tuple(failures),
        exit_code=exit_code,
    )


class JestRunner(ReportRunner):
    runner_name = "jest"
    report_name = "jest-report.json"

    def default_command(self) -> tuple[str, ...]:
        return ("npx", "jest")

    def build(self, base: Sequence[str], report: Path) -> list[str]:
        return with_script_separator(base, ["--json", f"--outputFile={report}"])

    def parse(
        self, report: str | None, output: str, exit_code: int, duration_s: float
    ) -> TestOutcome:
        if report is not None:
            parsed = parse_jest_json(report, exit_code, duration_s)
            if parsed is not None:
                return parsed
        return parse_text(output, exit_code, duration_s)
