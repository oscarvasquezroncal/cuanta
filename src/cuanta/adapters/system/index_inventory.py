from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path, PurePath, PurePosixPath

from cuanta.adapters.system.git_files import GitView, load_git_view, walk_visible
from cuanta.adapters.system.platform import home_dir
from cuanta.domain.code_index import IndexedFile, index_path
from cuanta.domain.detection import (
    KNOWLEDGE_FILES,
    config_exclusions,
    excluded_by_config,
    is_excluded_dir,
    is_source_file,
)

_EXCLUDED = frozenset(
    {
        ".cuanta",
        ".claude",
        "graphify-out",
        ".cache",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".turbo",
        "coverage",
        ".coverage",
        "htmlcov",
        ".tox",
        ".nox",
        "secrets",
        "credentials",
        ".ssh",
        ".aws",
        ".azure",
        ".gcloud",
    }
)
_SUFFIXES = frozenset(
    {
        ".css",
        ".scss",
        ".sass",
        ".md",
        ".mdx",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
        ".cmd",
        ".bat",
        ".bash",
        ".zsh",
        ".html",
        ".htm",
    }
)
_ROOT_DOCS = frozenset({"readme", "license", "licence", "copying", "notice", "makefile"})
_SECRET_NAMES = frozenset(
    {"credentials", "secrets", "credential", "secret", "id_rsa", "id_ed25519"}
)
_GENERATED = (".min.js", ".min.css", ".bundle.js", ".min.mjs", ".lock")
_LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".md": "markdown",
    ".mdx": "markdown",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".ps1": "powershell",
    ".cmd": "batch",
    ".bat": "batch",
    ".yml": "yaml",
}


def _linked(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _relevant(path: PurePath) -> bool:
    name = path.name.lower()
    if name.startswith(".env") or path.stem.lower() in _SECRET_NAMES:
        return False
    if name.endswith(_GENERATED) or name in {"package-lock.json", "npm-shrinkwrap.json"}:
        return False
    return (
        is_source_file(name)
        or path.suffix.lower() in _SUFFIXES
        or (len(path.parts) == 1 and name in _ROOT_DOCS)
    )


class LocalIndexInventory:
    def __init__(
        self,
        root: Path,
        exclusions: frozenset[str] = frozenset(),
        view: Callable[[], GitView] | None = None,
    ) -> None:
        self._root = root.resolve()
        self._exclusions = config_exclusions(exclusions)
        self._view = view or partial(load_git_view, self._root, home_dir(), os.environ)

    def _excluded(self, relative: str) -> bool:
        return any(
            is_excluded_dir(part, _EXCLUDED) for part in relative.lower().split("/")
        ) or excluded_by_config(relative, self._exclusions)

    def _skipped(self, name: str, relative: str) -> bool:
        return is_excluded_dir(name.lower(), _EXCLUDED) or excluded_by_config(
            relative, self._exclusions
        )

    def _path(self, relative: str) -> Path:
        normalized = index_path(relative)
        candidate = self._root / normalized
        current = self._root
        for part in Path(normalized).parts:
            current /= part
            try:
                if _linked(current):
                    raise ValueError("Index paths cannot traverse links or junctions")
            except FileNotFoundError:
                break
        if not candidate.resolve().is_relative_to(self._root):
            raise ValueError("Index paths must stay inside the project")
        return candidate

    def _bytes(self, relative: str) -> bytes | None:
        if self._excluded(relative):
            return None
        try:
            path = self._path(relative)
            payload = path.read_bytes()
            self._path(relative)
        except OSError:
            return None
        return None if b"\x00" in payload else payload

    def paths(self) -> tuple[str, ...]:
        found: list[str] = []
        for item in walk_visible(self._root, self._view(), self._skipped):
            if item.ignored and item.entry.name not in KNOWLEDGE_FILES:
                continue
            if _relevant(PurePosixPath(item.path)) and not self._excluded(item.path):
                found.append(item.path)
        return tuple(sorted(found))

    def tracked_folders(self, folders: Sequence[str]) -> frozenset[str]:
        tracked = self._view().tracked
        return frozenset(name for name in folders if tracked.holds(name) or tracked.has_under(name))

    def candidates(self, paths: Sequence[str] | None = None) -> tuple[IndexedFile, ...]:
        result: list[IndexedFile] = []
        for normalized in self.paths() if paths is None else paths:
            try:
                payload = self._bytes(normalized)
            except (OSError, ValueError):
                continue
            if payload is None:
                continue
            suffix = PurePosixPath(normalized).suffix.lower()
            result.append(
                IndexedFile(
                    normalized,
                    hashlib.sha256(payload).hexdigest(),
                    _LANGUAGES.get(suffix, suffix.lstrip(".") or "text"),
                    len(payload),
                )
            )
        return tuple(sorted(result, key=lambda item: item.path))

    def read(self, path: str) -> str | None:
        normalized = index_path(path)
        if not _relevant(PurePosixPath(normalized)):
            self._path(normalized)
            return None
        payload = self._bytes(normalized)
        return payload.decode("utf-8", errors="replace") if payload is not None else None
