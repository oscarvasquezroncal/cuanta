from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path

from cuanta.adapters.testing.base import ReportRunner
from cuanta.domain.testing import TestFailure, TestOutcome, frames_from_text

_LOCATION = re.compile(r"^\s*(\S+\.go):(\d+):\s*(.*)$")
_PANIC = re.compile(r"^panic:\s*(.*)$")


def _failure(package: str, test: str, lines: list[str]) -> TestFailure:
    text = "".join(lines)
    message, error_type = "", "TestFailure"
    for raw in lines:
        line = raw.rstrip()
        panic = _PANIC.match(line.strip())
        if panic:
            error_type, message = "panic", panic.group(1)
            break
        located = _LOCATION.match(line)
        if located and located.group(3):
            message = located.group(3)
            break
    if not message:
        message = next(
            (
                line.strip()
                for line in lines
                if line.strip() and not line.strip().startswith(("=== RUN", "--- FAIL"))
            ),
            "failed",
        )
    frames = frames_from_text(text)
    prefixed = tuple(
        frame
        if "/" in frame.path or not package
        else type(frame)(f"{package}/{frame.path}", frame.line)
        for frame in frames
    )
    return TestFailure(
        test=f"{package}::{test}", error_type=error_type, message=message, frames=prefixed
    )


def parse_go_json(output: str, exit_code: int, duration_s: float) -> TestOutcome:
    outputs: dict[tuple[str, str], list[str]] = {}
    passed = failed = skipped = 0
    elapsed = 0.0
    failures: list[TestFailure] = []
    build_errors: list[str] = []
    for line in output.splitlines():
        raw = line.strip()
        if not raw.startswith("{"):
            if raw:
                build_errors.append(raw)
            continue
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        action = event.get("Action")
        package = str(event.get("Package") or "")
        test = event.get("Test")
        key = (package, str(test or ""))
        if action == "output":
            outputs.setdefault(key, []).append(str(event.get("Output") or ""))
            continue
        if not test:
            if action in {"pass", "fail"} and isinstance(event.get("Elapsed"), int | float):
                elapsed += float(event["Elapsed"])
            continue
        if "/" in str(test) and action in {"pass", "fail", "skip"}:
            continue
        if action == "pass":
            passed += 1
        elif action == "skip":
            skipped += 1
        elif action == "fail":
            failed += 1
            failures.append(_failure(package, str(test), outputs.get(key, [])))
    errored = 0
    if exit_code != 0 and not failures and build_errors:
        errored = 1
        failures.append(
            TestFailure(
                test="build",
                error_type="BuildError",
                message=build_errors[0],
                frames=frames_from_text("\n".join(build_errors)),
            )
        )
    return TestOutcome(
        passed=passed,
        failed=failed,
        errored=errored,
        skipped=skipped,
        duration_s=elapsed or duration_s,
        failures=tuple(failures),
        exit_code=exit_code,
    )


class GoRunner(ReportRunner):
    runner_name = "go"
    report_name = "go-test.json"

    def default_command(self) -> tuple[str, ...]:
        return ("go", "test", "./...")

    def build(self, base: Sequence[str], report: Path) -> list[str]:
        command = list(base)
        if "-json" not in command:
            command.insert(2 if command[:2] == ["go", "test"] else len(command), "-json")
        return command

    def parse(
        self, report: str | None, output: str, exit_code: int, duration_s: float
    ) -> TestOutcome:
        return parse_go_json(output, exit_code, duration_s)
