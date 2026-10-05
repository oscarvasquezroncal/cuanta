from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import stat
from collections.abc import Callable
from contextlib import suppress
from functools import partial
from pathlib import Path

from cuanta.adapters.system.git_files import GitView, load_git_view, walk_visible
from cuanta.adapters.system.platform import home_dir
from cuanta.domain.detection import (
    config_exclusions,
    excluded_by_config,
    is_excluded_dir,
    is_source_file,
)
from cuanta.domain.disk_usage import INDEX_DATABASE, SQLITE_HEADER_BYTES, DiskUsage, free_bytes
from cuanta.domain.index_rebuild import rebuild_leftover
from cuanta.ports.workspace import ScanResult

MAX_ENTRY_CANDIDATES = 12
EXECUTE_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
BINARY_FLAG = getattr(os, "O_BINARY", 0)
SNAPSHOT_EXCLUDED = frozenset(
    {".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache", "graphify-out"}
)
TEST_DIRS = frozenset({"tests", "test"})
ENTRY_NAMES = frozenset({"main.go", "__main__.py"})
RULEBOOK = "CLAUDE.md"
STATE_DIR = ".cuanta"


def _reclaimable(entry: os.DirEntry[str], size: int) -> int:
    if rebuild_leftover(INDEX_DATABASE, entry.name):
        return size
    if entry.name != INDEX_DATABASE:
        return 0
    try:
        with open(entry.path, "rb") as handle:
            header = handle.read(SQLITE_HEADER_BYTES)
    except OSError:
        return 0
    return free_bytes(header, size)


def _unlocked(path: Path, action: Callable[[], object]) -> None:
    try:
        action()
        return
    except PermissionError:
        if os.name != "nt":
            raise
        try:
            mode = os.stat(path).st_mode
        except OSError:
            mode = stat.S_IWRITE
        if mode & stat.S_IWRITE:
            raise
    os.chmod(path, mode | stat.S_IWRITE)
    try:
        action()
    except BaseException:
        with suppress(OSError):
            os.chmod(path, stat.S_IMODE(mode))
        raise


def _in_tests(relative: str) -> bool:
    return any(part in TEST_DIRS for part in relative.split("/")[:-1])


def _test_kind(name: str, relative: str) -> str | None:
    lowered = name.lower()
    if lowered.endswith("_test.go"):
        return "go"
    if lowered.endswith(".py") and (lowered.startswith("test_") or lowered.endswith("_test.py")):
        return "python"
    if any(marker in lowered for marker in (".test.", ".spec.")):
        return "js"
    if lowered.endswith(".rs") and _in_tests(relative):
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
    def __init__(self, root: Path, git_root: Path | None = None, home: Path | None = None) -> None:
        self._root = root
        self._git_root = git_root
        self._home = home
        self._hashes: dict[str, tuple[int, int, int, str]] = {}

    def _git_view(self) -> GitView:
        home = self._home if self._home is not None else home_dir()
        return load_git_view(self._git_root or self._root, home, os.environ)

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

    def redirected(self, relative: str) -> bool:
        path = self._path(relative)
        try:
            if path.is_symlink() or path.is_junction():
                return True
            return not path.resolve().is_relative_to(self._root.resolve())
        except (OSError, RuntimeError):
            return True

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

    def read_bytes(self, relative: str) -> bytes | None:
        try:
            return self._path(relative).read_bytes()
        except OSError:
            return None

    def write_bytes(self, relative: str, content: bytes, executable: bool | None = None) -> None:
        path = self._path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        staged = path.with_name(f".cuanta-{secrets.token_hex(4)}.tmp")
        handle = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | BINARY_FLAG, 0o666)
        try:
            with os.fdopen(handle, "wb") as writer:
                writer.write(content)
            if path.is_file():
                shutil.copymode(path, staged)
            if executable is not None:
                mode = os.stat(staged).st_mode
                os.chmod(staged, mode | EXECUTE_BITS if executable else mode & ~EXECUTE_BITS)
            _unlocked(path, partial(os.replace, staged, path))
        except BaseException:
            with suppress(OSError):
                os.chmod(staged, stat.S_IREAD | stat.S_IWRITE)
            with suppress(OSError):
                staged.unlink(missing_ok=True)
            raise

    def sha256(self, relative: str) -> str | None:
        digest = hashlib.sha256()
        try:
            path = self._path(relative)
            info = path.stat()
            stamp = (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            cached = self._hashes.get(relative)
            if cached is not None and cached[:3] == stamp:
                return cached[3]
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
            after = path.stat()
            if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) == stamp:
                self._hashes[relative] = (*stamp, digest.hexdigest())
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

    def disk_usage(self, relative: str) -> DiskUsage:
        total = files = largest_bytes = reclaimable = 0
        largest = largest_path = ""
        folders = [(self._path(relative), True)]
        while folders:
            folder, top = folders.pop()
            try:
                with os.scandir(folder) as listing:
                    entries = list(listing)
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False) and not entry.is_junction():
                        folders.append((Path(entry.path), False))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    size = entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
                total += size
                files += 1
                if top:
                    reclaimable += _reclaimable(entry, size)
                if size < largest_bytes:
                    continue
                name = Path(entry.path).relative_to(self._root).as_posix()
                if size > largest_bytes or not largest or name < largest:
                    largest, largest_bytes, largest_path = name, size, entry.path
        if largest_path:
            with suppress(OSError):
                exact = os.stat(largest_path).st_size
                total += exact - largest_bytes
                largest_bytes = exact
        return DiskUsage(total, files, largest, largest_bytes, reclaimable)

    def files_under(self, relative: str) -> tuple[str, ...]:
        base = self._path(relative)
        if not base.is_dir():
            return ()
        found = (path.relative_to(self._root).as_posix() for path in base.rglob("*"))
        return tuple(sorted(item for item in found if (self._root / item).is_file()))

    def remove(self, relative: str) -> None:
        path = self._path(relative)
        if path.is_file():
            _unlocked(path, path.unlink)

    def scan(
        self, extra_exclusions: frozenset[str], collect_files: bool = False, all_files: bool = False
    ) -> ScanResult:
        exclusions = config_exclusions(extra_exclusions)
        count = 0
        nested: list[str] = []
        tests: dict[str, int] = {}
        entries: list[str] = []
        files: list[str] = []
        modes: dict[str, int] = {}
        ignored: set[str] = set()

        def skipped(name: str, relative: str) -> bool:
            return (
                is_excluded_dir(name)
                or name == STATE_DIR
                or (name.startswith(".") and not all_files)
                or (all_files and name in SNAPSHOT_EXCLUDED)
                or excluded_by_config(relative, exclusions)
            )

        for item in walk_visible(self._root, self._git_view(), skipped):
            relative = item.path
            name = item.entry.name
            if excluded_by_config(relative, exclusions):
                continue
            if name == RULEBOOK and "/" in relative:
                nested.append(relative)
            if all_files and collect_files:
                try:
                    info = item.entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                files.append(relative)
                modes[relative] = info.st_mode & 0o777
                if item.ignored:
                    ignored.add(relative)
            if item.ignored or not is_source_file(name):
                continue
            count += 1
            if collect_files and not all_files:
                files.append(relative)
            kind = _test_kind(name, relative)
            if kind is not None:
                tests[kind] = tests.get(kind, 0) + 1
            extension = _extension_key(name)
            if extension is not None:
                tests[extension] = tests.get(extension, 0) + 1
            if name in ENTRY_NAMES and len(entries) < MAX_ENTRY_CANDIDATES:
                entries.append(relative)
        return ScanResult(
            file_count=count,
            nested_claude_md=tuple(sorted(nested)),
            test_files=tests,
            entry_candidates=tuple(sorted(entries)),
            files=tuple(sorted(files)),
            modes=modes,
            ignored=frozenset(ignored),
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
