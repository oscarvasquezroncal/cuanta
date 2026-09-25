from __future__ import annotations

import re
from xml.etree import ElementTree

from cuanta.domain.testing import TestFailure, TestOutcome, frames_from_text, split_error

_TRAILER = re.compile(r":\d+: ([A-Za-z_]\w*(?:Error|Exception|Failure|Exit))\s*$", re.MULTILINE)


def _int(value: str | None) -> int:
    try:
        return int(float(value or 0))
    except ValueError:
        return 0


def _float(value: str | None) -> float:
    try:
        return float(value or 0)
    except ValueError:
        return 0.0


def _test_name(case: ElementTree.Element) -> str:
    classname = case.get("classname", "")
    name = case.get("name", "")
    file = case.get("file", "")
    if file:
        return f"{file}::{name}"
    if classname:
        module = classname.replace(".", "/")
        return (
            f"{module}.py::{name}"
            if "/" in module or classname.islower()
            else f"{classname}::{name}"
        )
    return name


def parse_junit(
    text: str, exit_code: int = 0, fallback_duration: float = 0.0
) -> TestOutcome | None:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return None
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    tests = failed = errored = skipped = 0
    duration = 0.0
    failures: list[TestFailure] = []
    for suite in suites:
        tests += _int(suite.get("tests"))
        failed += _int(suite.get("failures"))
        errored += _int(suite.get("errors"))
        skipped += _int(suite.get("skipped"))
        duration += _float(suite.get("time"))
        for case in suite.iter("testcase"):
            for tag in ("failure", "error"):
                node = case.find(tag)
                if node is None:
                    continue
                body = node.text or ""
                message = node.get("message") or body
                error_type = node.get("type") or ""
                if not error_type or error_type in {"pytest.fail", "failure"}:
                    trailer = _TRAILER.findall(body)
                    fallback = (
                        trailer[-1] if trailer else ("Failure" if tag == "failure" else "Error")
                    )
                    error_type, message = split_error(message, fallback)
                else:
                    error_type = error_type.rsplit(".", 1)[-1]
                    parsed_type, parsed_message = split_error(message, error_type)
                    if parsed_type == error_type:
                        message = parsed_message
                failures.append(
                    TestFailure(
                        test=_test_name(case),
                        error_type=error_type,
                        message=message,
                        frames=frames_from_text(body),
                    )
                )
    passed = max(tests - failed - errored - skipped, 0)
    return TestOutcome(
        passed=passed,
        failed=failed,
        errored=errored,
        skipped=skipped,
        duration_s=duration or fallback_duration,
        failures=tuple(failures),
        exit_code=exit_code,
    )
