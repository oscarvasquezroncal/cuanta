from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dev import gate_record
from dev.results import ROOT, Report, Step, python, test_environment


def static(report: Report) -> bool:
    commands = [
        ("ruff", python("-m", "ruff", "check", ".", "--output-format=json")),
        ("format", python("-m", "ruff", "format", "--check", ".")),
        ("mypy", python("-m", "mypy", "--strict")),
    ]
    privacy = ROOT / ".cuanta" / "privacy_scan.py"
    if privacy.is_file():
        commands.append(("privacy", python(str(privacy))))
    return all(report.run(name, command).code == 0 for name, command in commands)


def run(
    static_only: bool = False, no_perf: bool = False, twice: bool = False, npm: bool = False
) -> int:
    report = Report("gate")
    gated_tree = gate_record.worktree(ROOT) if not static_only and not no_perf else ""
    head = gate_record.git(ROOT, "rev-parse", "HEAD") if gated_tree else ""
    if not static(report) or static_only:
        return report.finish()
    environment = test_environment(report.directory)
    for repeat in range(2 if twice else 1):
        xml = report.directory / f"pytest-{repeat + 1}.xml"
        cov = report.directory / f"coverage-{repeat + 1}.json"
        parallel = report.run(
            f"pytest-{repeat + 1}",
            python(
                "-m",
                "pytest",
                "-n",
                "auto",
                "--maxprocesses=4",
                "-m",
                "not live and not perf",
                "--cov",
                f"--cov-report=json:{cov}",
                f"--junitxml={xml}",
            ),
            environment=environment,
            xml=xml,
            cov=cov,
        )
        if parallel.code != 0:
            break
        if not no_perf:
            perf_xml = report.directory / f"performance-{repeat + 1}.xml"
            perf_cov = report.directory / f"performance-coverage-{repeat + 1}.json"
            performance = report.run(
                f"performance-{repeat + 1}",
                python(
                    str(ROOT / "scripts" / "tests" / "performance.py"),
                    "--junitxml",
                    str(perf_xml),
                    "--coverage-json",
                    str(perf_cov),
                ),
                environment=environment,
                xml=perf_xml,
                cov=perf_cov,
            )
            if performance.code != 0:
                break
    if npm and all(step.code == 0 for step in report.steps):
        report.run("npm", python(str(ROOT / "scripts" / "npm" / "smoke.py")), timeout=900)
    if gated_tree and all(step.code == 0 for step in report.steps):
        record_gate(report, gated_tree, head)
    return report.finish()


def record_gate(report: Report, tree: str, head: str) -> None:
    started = time.monotonic()
    try:
        if gate_record.worktree(ROOT) != tree:
            raise ValueError("Working tree changed during the gate")
        counts = {
            "static": sum(
                step.name in {"ruff", "format", "mypy", "privacy"} for step in report.steps
            ),
            "functional": sum(
                step.tests.passed
                for step in report.steps
                if step.name.startswith("pytest-") and step.tests is not None
            ),
            "performance": sum(
                step.tests.passed
                for step in report.steps
                if step.name.startswith("performance-") and step.tests is not None
            ),
        }
        path = gate_record.write_green(ROOT, tree, head, counts)
        report.steps.append(
            Step("gate-record", [], 0, round(time.monotonic() - started, 2), str(path))
        )
    except (OSError, ValueError) as error:
        report.steps.append(
            Step("gate-record", [], 1, round(time.monotonic() - started, 2), "", problem=str(error))
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the milestone gate with a compact report.")
    parser.add_argument("--static", action="store_true")
    parser.add_argument("--no-perf", action="store_true")
    parser.add_argument("--twice", action="store_true")
    parser.add_argument("--npm", action="store_true", help="Build and smoke the local npm tarball")
    options = parser.parse_args()
    return run(options.static, options.no_perf, options.twice, options.npm)


if __name__ == "__main__":
    raise SystemExit(main())
