from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from cuanta.adapters.system.process_tree import ProcessTree, creation_flags

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Tests:
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    speed_only: bool = False


def junit(path: Path) -> Tests:
    root = ET.parse(path).getroot()
    result = Tests()
    speed: list[bool] = []
    for case in root.iter("testcase"):
        failure = case.find("failure")
        error = case.find("error")
        problem = failure if failure is not None else error
        if problem is not None:
            result.failed += 1
            node = case.get("classname", "").replace(".", "/") + ".py::" + case.get("name", "")
            message = problem.get("message") or problem.text or "unspecified failure"
            result.failures.append((node, " ".join(message.split())[:240]))
            speed.append(
                failure is not None
                and bool(
                    re.search(r"assert (?:elapsed|timings\[0\]|min\(samples\))\s*<", message)
                    or re.search(
                        r"^>\s+assert (?:elapsed|timings\[0\]|min\(samples\))\s*<",
                        problem.text or "",
                        re.M,
                    )
                )
            )
        elif case.find("skipped") is not None:
            result.skipped += 1
        else:
            result.passed += 1
    result.speed_only = bool(speed) and all(speed)
    if result.passed + result.failed + result.skipped == 0:
        raise ValueError("Empty junit report")
    return result


def coverage(path: Path) -> float:
    value = json.loads(path.read_text(encoding="utf-8"))["totals"]["percent_covered"]
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 100:
        raise ValueError("Invalid coverage percentage")
    return float(value)


def ruff_count(path: Path) -> int:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("Expected ruff JSON diagnostics")
    return len(value)


def mypy_count(path: Path) -> int:
    return len(re.findall(r"^.*\.py(?::\d+){0,2}: error:", path.read_text(encoding="utf-8"), re.M))


@dataclass
class Step:
    name: str
    command: list[str] | str
    code: int
    seconds: float
    log: str
    tests: Tests | None = None
    coverage: float | None = None
    diagnostics: int | None = None
    problem: str = ""


class Report:
    def __init__(self, kind: str, root: Path = ROOT) -> None:
        self.root = root
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        self.directory = root / ".cuanta" / "gates" / f"{timestamp}-{kind}"
        self.directory.mkdir(parents=True)
        self.steps: list[Step] = []

    def run(
        self,
        name: str,
        command: list[str] | str,
        *,
        cwd: Path | None = None,
        timeout: int = 1800,
        environment: dict[str, str] | None = None,
        xml: Path | None = None,
        cov: Path | None = None,
    ) -> Step:
        log = self.directory / f"{len(self.steps) + 1:02d}-{name}.log"
        started = time.monotonic()
        problem = ""
        with log.open("w", encoding="utf-8") as stream:
            try:
                with subprocess.Popen(
                    command,
                    cwd=cwd or self.root,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    text=True,
                    creationflags=creation_flags(),
                    start_new_session=os.name != "nt",
                    shell=isinstance(command, str),
                ) as process:
                    tree: ProcessTree | None = None
                    try:
                        tree = ProcessTree(process, suspended=os.name == "nt")
                        code = process.wait(timeout=timeout)
                    finally:
                        if tree is not None:
                            tree.close()
                        elif process.poll() is None:
                            process.kill()
            except (OSError, subprocess.TimeoutExpired) as error:
                code = 1
                problem = str(error)
                stream.write(problem + "\n")
        step = Step(name, command, code, round(time.monotonic() - started, 2), str(log))
        step.problem = problem
        try:
            if xml is not None:
                step.tests = junit(xml)
            if cov is not None:
                step.coverage = coverage(cov)
            if name == "ruff":
                step.diagnostics = ruff_count(log)
            elif name == "mypy":
                step.diagnostics = mypy_count(log)
        except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as error:
            step.code = 1
            step.problem = f"Structured report unavailable: {error}"
        self.steps.append(step)
        return step

    def finish(self, code: int | None = None) -> int:
        result = exit_code(self.steps) if code is None else code
        (self.directory / "summary.json").write_text(
            json.dumps(
                {"exit_code": result, "steps": [asdict(step) for step in self.steps]}, indent=2
            ),
            encoding="utf-8",
        )
        failures: list[tuple[str, str]] = []
        for step in self.steps:
            detail = ""
            if step.tests is not None:
                tests = step.tests
                detail = f"; {tests.passed} passed, {tests.failed} failed, {tests.skipped} skipped"
                failures.extend(tests.failures)
            if step.coverage is not None:
                detail += f"; coverage {step.coverage:.2f}%"
            if step.diagnostics is not None:
                detail += f"; {step.diagnostics} diagnostics"
            if step.problem:
                detail += "; " + " ".join(step.problem.split())[:180]
            print(f"{'ok' if step.code == 0 else 'fail'} {step.name}: {step.seconds:.2f}s{detail}")
        for node, reason in failures[: max(0, min(10, 14 - len(self.steps)))]:
            print(f"  {node}: {reason}")
        print(f"Logs: {self.directory}")
        return result


def exit_code(steps: list[Step]) -> int:
    failed = [step for step in steps if step.code != 0]
    if not failed:
        return 0
    if all(
        step.name.startswith("performance")
        and step.code == 1
        and not step.problem
        and step.coverage is not None
        and step.coverage >= coverage_floor()
        and step.tests is not None
        and step.tests.speed_only
        for step in failed
    ):
        return 2
    return 1


def coverage_floor() -> float:
    import tomllib

    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return float(config["tool"]["coverage"]["report"]["fail_under"])


def python(*arguments: str) -> list[str]:
    return [sys.executable, *arguments]


def test_environment(directory: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONUTF8"] = "1"
    environment["COVERAGE_FILE"] = str(directory / "coverage-data")
    return environment
