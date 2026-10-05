from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path, PurePath, PurePosixPath
from uuid import uuid4

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
        self._hashes: dict[str, tuple[int, int, int, str]] = {}
        try:
            cached = json.loads(
                self._path(".cuanta/inventory-hashes.json").read_text(encoding="utf-8")
            )
            if isinstance(cached, dict):
                for name, entry in cached.items():
                    if (
                        isinstance(name, str)
                        and isinstance(entry, list)
                        and len(entry) == 4
                        and all(isinstance(value, int) for value in entry[:3])
                        and isinstance(entry[3], str)
                    ):
                        self._hashes[name] = (int(entry[0]), int(entry[1]), int(entry[2]), entry[3])
        except (OSError, ValueError):
            pass

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
        previous = dict(self._hashes)
        for normalized in self.paths() if paths is None else paths:
            try:
                info = self._path(normalized).stat()
                stamp = (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                cached = self._hashes.get(normalized)
                if cached is not None and cached[:3] == stamp:
                    digest, size = cached[3], info.st_size
                else:
                    payload = self._bytes(normalized)
                    if payload is None:
                        continue
                    after = self._path(normalized).stat()
                    digest, size = hashlib.sha256(payload).hexdigest(), len(payload)
                    if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) == stamp:
                        self._hashes[normalized] = (*stamp, digest)
            except (OSError, ValueError):
                continue
            suffix = PurePosixPath(normalized).suffix.lower()
            result.append(
                IndexedFile(
                    normalized,
                    digest,
                    _LANGUAGES.get(suffix, suffix.lstrip(".") or "text"),
                    size,
                )
            )
        self._hashes = {
            item.path: self._hashes[item.path] for item in result if item.path in self._hashes
        }
        if self._hashes != previous:
            self._save_hashes()
        return tuple(sorted(result, key=lambda item: item.path))

    def _save_hashes(self) -> None:
        temporary: Path | None = None
        try:
            destination = self._path(".cuanta/inventory-hashes.json")
            destination.parent.mkdir(exist_ok=True)
            temporary = self._path(f".cuanta/inventory-{uuid4().hex}.tmp")
            temporary.write_text(json.dumps(self._hashes, sort_keys=True), encoding="utf-8")
            os.replace(temporary, destination)
        except (OSError, ValueError):
            pass
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def read(self, path: str) -> str | None:
        normalized = index_path(path)
        if not _relevant(PurePosixPath(normalized)):
            self._path(normalized)
            return None
        payload = self._bytes(normalized)
        return payload.decode("utf-8", errors="replace") if payload is not None else None
