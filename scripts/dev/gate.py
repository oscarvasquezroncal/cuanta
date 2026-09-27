from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dev.results import ROOT, Report, python, test_environment


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


def run(static_only: bool = False, no_perf: bool = False, twice: bool = False) -> int:
    report = Report("gate")
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
    return report.finish()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the milestone gate with a compact report.")
    parser.add_argument("--static", action="store_true")
    parser.add_argument("--no-perf", action="store_true")
    parser.add_argument("--twice", action="store_true")
    options = parser.parse_args()
    return run(options.static, options.no_perf, options.twice)


if __name__ == "__main__":
    raise SystemExit(main())
