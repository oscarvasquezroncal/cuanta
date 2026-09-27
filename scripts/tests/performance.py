from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

SELECTION = ("-n", "0", "-m", "not live and perf")


class GateError(Exception):
    pass


def nodeids(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(node, str) or not node or "\0" in node for node in value)
        or len(set(value)) != len(value)
    ):
        raise GateError("Performance selection must contain nonempty, unique test node IDs.")
    return tuple(value)


class Selection:
    def __init__(self, expected: tuple[str, ...] | None = None) -> None:
        self.expected = expected
        self.collected: tuple[str, ...] = ()
        self.started: list[str] = []

    def validate(self, session: pytest.Session) -> None:
        import pytest

        try:
            selected = nodeids([item.nodeid for item in session.items])
        except GateError as error:
            raise pytest.UsageError(str(error)) from error
        if any(
            item.get_closest_marker("perf") is None or item.get_closest_marker("live") is not None
            for item in session.items
        ):
            raise pytest.UsageError(
                "Performance selection includes a live or non-performance test."
            )
        if self.expected is not None and selected != self.expected:
            raise pytest.UsageError(
                "Performance selection changed between discovery and execution."
            )
        self.collected = selected

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.validate(session)

    def pytest_runtestloop(self, session: pytest.Session) -> None:
        self.validate(session)

    def pytest_runtest_logstart(self, nodeid: str) -> None:
        self.started.append(nodeid)


def collect(manifest: Path) -> int:
    import pytest

    selection = Selection()
    result = int(pytest.main([*SELECTION, "--collect-only"], plugins=[selection]))
    if result == 0:
        manifest.write_text(json.dumps(list(selection.collected)), encoding="utf-8")
    return result


def execute(manifest: Path, reports: Sequence[str] = (), standalone: bool = False) -> int:
    import pytest

    expected = nodeids(json.loads(manifest.read_text(encoding="utf-8")))
    selection = Selection(expected)
    coverage_options = ["--cov-fail-under=0"] if standalone else ["--cov-append"]
    result = int(
        pytest.main(
            [
                *SELECTION,
                "--cov",
                *coverage_options,
                "--cov-report=term",
                *reports,
                "--",
                *expected,
            ],
            plugins=[selection],
        )
    )
    if result == 0 and tuple(selection.started) != expected:
        raise GateError("Performance tests did not execute exactly once in the discovered order.")
    return result


def child(mode: str, manifest: Path, reports: Sequence[str] = ()) -> int:
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), mode, str(manifest), *reports], check=False
    ).returncode


def run(reports: Sequence[str] = ()) -> int:
    with tempfile.TemporaryDirectory(prefix="cuanta-perf-") as directory:
        manifest = Path(directory) / "nodeids.json"
        result = child("--collect", manifest)
        if result != 0:
            return result
        nodeids(json.loads(manifest.read_text(encoding="utf-8")))
        return child("--run", manifest, reports)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discover and run isolated performance tests.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--collect", type=Path)
    modes.add_argument("--run", type=Path)
    parser.add_argument("--junitxml", type=Path)
    parser.add_argument("--coverage-json", type=Path)
    parser.add_argument("--standalone", action="store_true")
    options = parser.parse_args(argv)
    reports: list[str] = []
    child_reports: list[str] = []
    if options.standalone:
        child_reports.append("--standalone")
    if options.junitxml is not None:
        reports.append(f"--junitxml={options.junitxml}")
        child_reports.extend(["--junitxml", str(options.junitxml)])
    if options.coverage_json is not None:
        reports.append(f"--cov-report=json:{options.coverage_json}")
        child_reports.extend(["--coverage-json", str(options.coverage_json)])
    try:
        if options.collect is not None:
            return collect(options.collect)
        if options.run is not None:
            return execute(options.run, reports, options.standalone)
        return run(child_reports)
    except (OSError, ValueError, GateError) as error:
        print(f"Performance gate failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
