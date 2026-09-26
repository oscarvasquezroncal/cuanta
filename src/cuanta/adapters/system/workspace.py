from __future__ import annotations

import hashlib
import os
from pathlib import Path

from cuanta.domain.detection import is_excluded_dir, is_source_file
from cuanta.ports.workspace import ScanResult

MAX_ENTRY_CANDIDATES = 12


def _test_kind(name: str, in_tests_dir: bool) -> str | None:
    lowered = name.lower()
    if lowered.endswith("_test.go"):
        return "go"
    if lowered.endswith(".py") and (lowered.startswith("test_") or lowered.endswith("_test.py")):
        return "python"
    if any(marker in lowered for marker in (".test.", ".spec.")):
        return "js"
    if lowered.endswith(".rs") and in_tests_dir:
        return "rust"
    if lowered.endswith(("test.java", "tests.java", "test.kt")):
        return "java"
    if lowered.endswith(("_spec.rb", "_test.rb")):
        return "ruby"
    if lowered.endswith("test.php"):
        return "php"
    return None


def _extension_key(name: str) -> str | None:
    dot = name.rfind(".")
    return f"ext:{name[dot + 1 :].lower()}" if dot > 0 else None


class LocalWorkspace:
    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def _path(self, relative: str) -> Path:
        return self._root / relative

    def read_text(self, relative: str) -> str | None:
        path = self._path(relative)
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            return None

    def exists(self, relative: str) -> bool:
        return self._path(relative).exists()

    def is_dir(self, relative: str) -> bool:
        return self._path(relative).is_dir()

    def list_names(self, relative: str) -> tuple[str, ...]:
        path = self._path(relative)
        try:
            return tuple(sorted(entry.name for entry in os.scandir(path)))
        except OSError:
            return ()

    def write_text(self, relative: str, content: str) -> None:
        path = self._path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")

    def sha256(self, relative: str) -> str | None:
        digest = hashlib.sha256()
        try:
            with self._path(relative).open("rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
        except OSError:
            return None
        return digest.hexdigest()

    def size_bytes(self, relative: str) -> int:
        path = self._path(relative)
        if path.is_file():
            return path.stat().st_size
        total = 0
        for folder, _, names in os.walk(path):
            for name in names:
                try:
                    total += (Path(folder) / name).stat().st_size
                except OSError:
                    continue
        return total

    def files_under(self, relative: str) -> tuple[str, ...]:
        base = self._path(relative)
        if not base.is_dir():
            return ()
        found = (path.relative_to(self._root).as_posix() for path in base.rglob("*"))
        return tuple(sorted(item for item in found if (self._root / item).is_file()))

    def remove(self, relative: str) -> None:
        path = self._path(relative)
        if path.is_file():
            path.unlink()

    def scan(self, extra_exclusions: frozenset[str], collect_files: bool = False) -> ScanResult:
        count = 0
        nested: list[str] = []
        tests: dict[str, int] = {}
        entries: list[str] = []
        files: list[str] = []
        stack: list[tuple[str, str, bool]] = [(str(self._root), "", False)]
        while stack:
            folder, prefix, in_tests = stack.pop()
            try:
                iterator = os.scandir(folder)
            except OSError:
                continue
            with iterator:
                for entry in iterator:
                    name = entry.name
                    relative = f"{prefix}{name}"
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    if is_dir:
                        if is_excluded_dir(name, extra_exclusions) or relative in extra_exclusions:
                            continue
                        if name.startswith("."):
                            continue
                        stack.append(
                            (entry.path, f"{relative}/", in_tests or name in {"tests", "test"})
                        )
                        continue
                    if name == "CLAUDE.md" and prefix:
                        nested.append(relative)
                    if not is_source_file(name):
                        continue
                    count += 1
                    if collect_files:
                        files.append(relative)
                    kind = _test_kind(name, in_tests)
                    if kind is not None:
                        tests[kind] = tests.get(kind, 0) + 1
                    extension = _extension_key(name)
                    if extension is not None:
                        tests[extension] = tests.get(extension, 0) + 1
                    if name in {"main.go", "__main__.py"} and len(entries) < MAX_ENTRY_CANDIDATES:
                        entries.append(relative)
        return ScanResult(
            file_count=count,
            nested_claude_md=tuple(sorted(nested)),
            test_files=tests,
            entry_candidates=tuple(sorted(entries)),
            files=tuple(sorted(files)),
        )


class LocalHome:
    def __init__(self, home: Path) -> None:
        self._home = home

    def read_text(self, relative: str) -> str | None:
        try:
            return (self._home / relative).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def path(self, relative: str) -> Path:
        return self._home / relative

    def size_bytes(self, relative: str) -> int | None:
        target = self._home / relative
        try:
            return target.stat().st_size if target.is_file() else None
        except OSError:
            return None
