from __future__ import annotations

import re

from cuanta.adapters.testing.base import ReportRunner
from cuanta.domain.testing import TestFailure, TestOutcome, frames_from_text, split_error

_COUNTS = {
    "passed": re.compile(r"(\d+)\s+(?:passed|passing|pass)\b", re.IGNORECASE),
    "failed": re.compile(r"(\d+)\s+(?:failed|failing|failures?)\b", re.IGNORECASE),
    "errored": re.compile(r"(\d+)\s+errors?\b", re.IGNORECASE),
    "skipped": re.compile(r"(\d+)\s+(?:skipped|pending|ignored)\b", re.IGNORECASE),
}
_FAIL_LINE = re.compile(r"^\s*(?:FAIL|FAILED|ERROR|✕|×)[:\s]+(.+?)\s*$")


def _last_count(pattern: re.Pattern[str], text: str) -> int:
    matches = pattern.findall(text)
    return int(matches[-1]) if matches else 0


def parse_text(output: str, exit_code: int, duration_s: float) -> TestOutcome:
    counts = {key: _last_count(pattern, output) for key, pattern in _COUNTS.items()}
    lines = output.splitlines()
    failures: list[TestFailure] = []
    for index, line in enumerate(lines):
        match = _FAIL_LINE.match(line)
        if not match:
            continue
        subject = match.group(1)
        context = "\n".join(lines[index : index + 8])
        detail = next(
            (
                item.strip()
                for item in lines[index + 1 : index + 8]
                if re.search(r"Error|assert|expected", item, re.IGNORECASE)
            ),
            subject,
        )
        error_type, message = split_error(detail, "Failure")
        failures.append(
            TestFailure(
                test=subject.split(" - ")[0].strip(),
                error_type=error_type,
                message=message,
                frames=frames_from_text(context),
            )
        )
    failed = counts["failed"]
    if exit_code != 0 and not failed and not counts["errored"]:
        failed = max(len(failures), 1)
        if not failures:
            tail = next(
                (line.strip() for line in reversed(lines) if line.strip()), "command failed"
            )
            failures.append(TestFailure(test="suite", error_type="Failure", message=tail))
    return TestOutcome(
        passed=counts["passed"],
        failed=failed,
        errored=counts["errored"],
        skipped=counts["skipped"],
        duration_s=duration_s,
        failures=tuple(failures),
        exit_code=exit_code,
    )


class GenericRunner(ReportRunner):
    runner_name = "generic"
    report_name = "generic.txt"

    def parse(
        self, report: str | None, output: str, exit_code: int, duration_s: float
    ) -> TestOutcome:
        return parse_text(output, exit_code, duration_s)
