from __future__ import annotations

import re

from cuanta.adapters.testing.base import ReportRunner
from cuanta.domain.testing import Frame, TestFailure, TestOutcome

_RESULT = re.compile(
    r"test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored; \d+ measured; \d+ filtered out"
    r"(?:; finished in ([\d.]+)s)?"
)
_SECTION = re.compile(r"^---- (\S+) stdout ----$")
_PANIC_NEW = re.compile(r"panicked at ([^\s:]+):(\d+):\d+:?$")
_PANIC_OLD = re.compile(r"panicked at '(.*)', ([^\s:]+):(\d+):\d+")


def _section_failure(test: str, lines: list[str]) -> TestFailure:
    for index, line in enumerate(lines):
        old = _PANIC_OLD.search(line)
        if old:
            return TestFailure(
                test, "panic", old.group(1), (Frame(old.group(2), int(old.group(3))),)
            )
        new = _PANIC_NEW.search(line)
        if new:
            message = next(
                (item.strip() for item in lines[index + 1 :] if item.strip()), "panicked"
            )
            return TestFailure(test, "panic", message, (Frame(new.group(1), int(new.group(2))),))
    message = next((line.strip() for line in lines if line.strip()), "failed")
    return TestFailure(test, "TestFailure", message)


def parse_cargo_text(output: str, exit_code: int, duration_s: float) -> TestOutcome:
    passed = failed = skipped = 0
    elapsed = 0.0
    sections: dict[str, list[str]] = {}
    current: str | None = None
    failed_names: list[str] = []
    for line in output.splitlines():
        result = _RESULT.search(line)
        if result:
            passed += int(result.group(1))
            failed += int(result.group(2))
            skipped += int(result.group(3))
            if result.group(4):
                elapsed += float(result.group(4))
            current = None
            continue
        section = _SECTION.match(line.strip())
        if section:
            current = section.group(1)
            sections[current] = []
            continue
        if line.strip() == "failures:":
            current = None
            continue
        if current is not None:
            sections[current].append(line)
        stripped = line.strip()
        if stripped.startswith("test ") and stripped.endswith("... FAILED"):
            failed_names.append(stripped[5:-10].strip())
    names = list(dict.fromkeys([*sections, *failed_names]))
    failures = tuple(_section_failure(name, sections.get(name, [])) for name in names)
    errored = 1 if exit_code != 0 and not failures and passed == 0 else 0
    if errored:
        first = next(
            (line for line in output.splitlines() if line.startswith("error")), "build failed"
        )
        failures = (TestFailure("build", "BuildError", first),)
    return TestOutcome(
        passed=passed,
        failed=failed,
        errored=errored,
        skipped=skipped,
        duration_s=elapsed or duration_s,
        failures=failures,
        exit_code=exit_code,
    )


class CargoRunner(ReportRunner):
    runner_name = "cargo"
    report_name = "cargo.txt"

    def default_command(self) -> tuple[str, ...]:
        return ("cargo", "test")

    def parse(
        self, report: str | None, output: str, exit_code: int, duration_s: float
    ) -> TestOutcome:
        return parse_cargo_text(output, exit_code, duration_s)
