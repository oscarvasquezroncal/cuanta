from __future__ import annotations

import hashlib
import os
import stat
from fnmatch import fnmatchcase
from pathlib import Path

from cuanta.domain.code_index import IndexedFile, index_path
from cuanta.domain.detection import is_excluded_dir, is_source_file

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


def _relevant(path: Path) -> bool:
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
    def __init__(self, root: Path, exclusions: frozenset[str] = frozenset()) -> None:
        self._root = root.resolve()
        self._exclusions = frozenset(value.replace("\\", "/").lower() for value in exclusions)

    def _excluded(self, relative: str) -> bool:
        lowered = relative.lower()
        return any(
            is_excluded_dir(part, _EXCLUDED) or part in self._exclusions
            for part in Path(lowered).parts
        ) or any(fnmatchcase(lowered, pattern) for pattern in self._exclusions)

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

    def candidates(self) -> tuple[IndexedFile, ...]:
        result: list[IndexedFile] = []
        for folder, directories, files in os.walk(self._root, followlinks=False):
            base = Path(folder)
            kept: list[str] = []
            for name in sorted(directories):
                path = base / name
                relative = path.relative_to(self._root).as_posix()
                try:
                    if not self._excluded(relative) and not _linked(path):
                        kept.append(name)
                except OSError:
                    continue
            directories[:] = kept
            for name in sorted(files):
                path = base / name
                relative_path = path.relative_to(self._root)
                if not _relevant(relative_path):
                    continue
                normalized = relative_path.as_posix()
                try:
                    payload = self._bytes(normalized)
                except (OSError, ValueError):
                    continue
                if payload is None:
                    continue
                suffix = path.suffix.lower()
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
        if not _relevant(Path(normalized)):
            self._path(normalized)
            return None
        payload = self._bytes(normalized)
        return payload.decode("utf-8", errors="replace") if payload is not None else None
