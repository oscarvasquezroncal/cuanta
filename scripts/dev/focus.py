from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dev.results import ROOT, Report, python


def changed(root: Path) -> list[Path]:
    output = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=root
    )
    records = iter(output.decode("utf-8").split("\0"))
    files: list[Path] = []
    for record in records:
        if not record:
            continue
        files.append(root / record[3:])
        if "R" in record[:2] or "C" in record[:2]:
            files.append(root / next(records))
    return files


def module(path: Path, root: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    parts = relative.parts
    if parts[0] == "src" or parts[:2] == ("scripts", "dev"):
        parts = parts[1:]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imports(path: Path, root: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = module(path, root).split(".")[:-1]
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = package[: len(package) - node.level + 1] if node.level else []
            base = ".".join([*prefix, *((node.module or "").split("."))]).strip(".")
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


def related(files: list[Path], root: Path) -> list[str]:
    modules = {module(path, root) for path in files if path.suffix == ".py"}
    all_tests = sorted((root / "tests").rglob("test_*.py"))
    broad = any(
        path.name in {"pyproject.toml", "conftest.py"} or path.suffix in {".tcss", ".toml"}
        for path in files
    )
    selected: set[str] = set()
    for path in all_tests:
        if "fixtures" in path.relative_to(root).parts:
            continue
        if (
            broad
            or path in files
            or any(
                name == target or name.startswith(target + ".")
                for name in imports(path, root)
                for target in modules
            )
        ):
            selected.add(path.relative_to(root).as_posix())
    return sorted(selected)


def run(paths: list[str], snapshot_update: bool = False) -> int:
    report = Report("focus")
    try:
        if snapshot_update and (
            not paths or any(not path.replace("\\", "/").startswith("tests/tui/") for path in paths)
        ):
            raise ValueError("Snapshot updates require explicit TUI test paths")
        files = [ROOT / path for path in paths] if paths else changed(ROOT)
        expanded: list[Path] = []
        for path in files:
            expanded.extend(path.rglob("*.py") if path.is_dir() else [path])
        files = [path.resolve() for path in expanded]
        if any(not path.is_relative_to(ROOT) for path in files):
            raise ValueError("Focus paths must be inside the repository")
        targets = [str(path) for path in files if path.is_file() and path.suffix == ".py"]
        selected = related(files, ROOT)
    except (OSError, ValueError, SyntaxError, subprocess.SubprocessError) as error:
        (report.directory / "selection-error.log").write_text(str(error), encoding="utf-8")
        print(f"fail focus selection: {error}")
        return report.finish(1)
    (report.directory / "selection.json").write_text(
        json.dumps({"files": [str(path) for path in files], "tests": selected}, indent=2),
        encoding="utf-8",
    )
    commands: list[tuple[str, list[str]]] = []
    if targets:
        commands.extend(
            [
                ("ruff", python("-m", "ruff", "check", "--output-format=json", *targets)),
                ("format", python("-m", "ruff", "format", "--check", *targets)),
            ]
        )
    commands.append(("mypy", python("-m", "mypy", "--strict", "--incremental")))
    for name, command in commands:
        if report.run(name, command).code != 0:
            return report.finish()
    for name, tests in [("architecture", ["tests/architecture"]), ("related", selected)]:
        targets = (
            [test for test in tests if not test.startswith("tests/architecture/")]
            if name == "related"
            else tests
        )
        if not targets:
            continue
        xml = report.directory / f"{name}.xml"
        if (
            report.run(
                name,
                python(
                    "-m",
                    "pytest",
                    "-n",
                    "auto",
                    "--maxprocesses=4",
                    "-m",
                    "not live and not perf",
                    f"--junitxml={xml}",
                    *(["--snapshot-update"] if snapshot_update and name == "related" else []),
                    *targets,
                ),
                xml=xml,
            ).code
            != 0
        ):
            return report.finish()
    return report.finish()


def main() -> int:
    parser = argparse.ArgumentParser(description="Check changed files and their importing tests.")
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--snapshot-update", action="store_true")
    options = parser.parse_args()
    return run(options.paths, options.snapshot_update)


if __name__ == "__main__":
    raise SystemExit(main())
