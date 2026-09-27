from __future__ import annotations

import json
import math
import os
import secrets
import stat
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from hashlib import sha256
from pathlib import Path

from cuanta.adapters.graph.file_graph import (
    GRAPH_FILE,
    graph_document,
    graph_fresh,
    graph_object,
    graph_path,
    load_index_graph,
)
from cuanta.adapters.graph.graphify import BINARY, GraphifyTool
from cuanta.adapters.system.index_inventory import LocalIndexInventory
from cuanta.adapters.system.process_runner import SubprocessRunner
from cuanta.domain.code_index import IndexedFile, IndexStructure
from cuanta.domain.detection import SizeTier, is_source_file, size_tier
from cuanta.ports.system import ProcessRunner

LEASE_SECONDS = 1200.0
LOCK_FILE = "graph-refresh.lock"
STATUS_FILE = "graph-refresh.log"


def _state_dir(root: Path) -> Path:
    path = root.resolve() / ".cuanta"
    path.mkdir(exist_ok=True)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ValueError("Graph refresh state must stay inside the project")
    return path


def _state_file(state: Path, name: str) -> Path:
    path = state / name
    for candidate in (state, path):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("Graph refresh state paths must not traverse links")
    return path


@contextmanager
def _claim(state: Path) -> Iterator[bool]:
    claim = _state_file(state, "graph-refresh.claim")
    try:
        descriptor = os.open(claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        yield False
        return
    try:
        os.close(descriptor)
        yield True
    finally:
        claim.unlink(missing_ok=True)


def _read_lease(state: Path) -> dict[str, object]:
    try:
        return graph_object(json.loads(_state_file(state, LOCK_FILE).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def _leased(lease: dict[str, object]) -> bool:
    expiry = lease.get("expires")
    return (
        isinstance(expiry, (int, float))
        and not isinstance(expiry, bool)
        and math.isfinite(expiry)
        and expiry > time.time()
    )


def _write_lease(state: Path, lease: dict[str, object]) -> None:
    _atomic_state(state, LOCK_FILE, json.dumps(lease))


def _atomic_state(state: Path, name: str, data: str) -> None:
    target = _state_file(state, name)
    temporary = _state_file(state, f"graph-state.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as writer:
            writer.write(data)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _record(state: Path, token: str, status: str, detail: str = "") -> None:
    payload = {
        "token": token,
        "pid": os.getpid(),
        "time": time.time(),
        "status": status,
        "detail": detail[:240],
    }
    with suppress(OSError, ValueError):
        _atomic_state(state, STATUS_FILE, json.dumps(payload))


def _release(state: Path, token: str) -> None:
    with _claim(state) as claimed:
        if claimed and _read_lease(state).get("token") == token:
            _state_file(state, LOCK_FILE).unlink(missing_ok=True)


def _renew(state: Path, token: str) -> bool:
    with _claim(state) as claimed:
        lease = _read_lease(state)
        if not claimed or lease.get("token") != token or not _leased(lease):
            return False
        lease["pid"] = os.getpid()
        lease["expires"] = time.time() + LEASE_SECONDS
        _write_lease(state, lease)
        return True


def _eligible(root: Path, files: tuple[IndexedFile, ...]) -> bool:
    sources = tuple(file for file in files if is_source_file(Path(file.path).name))
    if size_tier(len(sources)) is SizeTier.SMALL:
        return False
    graph = graph_path(root, GRAPH_FILE)
    if graph is None:
        return False
    manifest = graph_document(root, "graphify-out/manifest.json")
    graph_time = graph.stat().st_mtime_ns
    for file in sources:
        path = graph_path(root, file.path)
        if path is not None and (
            path.stat().st_mtime_ns > graph_time
            or (file.path in manifest and not graph_fresh(root, file, {}, manifest))
        ):
            return True
    return False


def _detached_flags() -> int:
    if os.name != "nt":
        return 0
    return (
        getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    )


class LocalIndexGraph:
    def __init__(self, root: Path, runner: ProcessRunner) -> None:
        self._root = root.resolve()
        self._runner = runner

    def records(self, files: tuple[IndexedFile, ...]) -> IndexStructure:
        return load_index_graph(self._root, files)

    def fingerprint(self) -> str:
        digest = sha256()
        for relative in (GRAPH_FILE, "graphify-out/manifest.json"):
            digest.update(relative.encode())
            path = graph_path(self._root, relative)
            if path is None:
                digest.update(b"\0missing\0")
                continue
            try:
                digest.update(path.read_bytes())
            except OSError:
                digest.update(b"\0unreadable\0")
        manifest = graph_document(self._root, "graphify-out/manifest.json")
        for relative in sorted(manifest):
            digest.update(relative.encode())
            path = graph_path(self._root, relative)
            try:
                value = str(path.stat().st_mtime_ns) if path is not None else "missing"
            except OSError:
                value = "unreadable"
            digest.update(value.encode())
        return digest.hexdigest()

    def request_refresh(self, files: tuple[IndexedFile, ...]) -> None:
        try:
            if self._runner.which(BINARY) is None or not _eligible(self._root, files):
                return
            state = _state_dir(self._root)
            with _claim(state) as claimed:
                if not claimed:
                    return
                self._start(state)
        except (OSError, ValueError) as error:
            with suppress(OSError, ValueError):
                _record(_state_dir(self._root), "", "unavailable", str(error))

    def _start(self, state: Path) -> None:
        existing = _read_lease(state)
        if _leased(existing):
            return
        lock = _state_file(state, LOCK_FILE)
        if not existing and lock.exists() and lock.stat().st_mtime + LEASE_SECONDS > time.time():
            return
        token = secrets.token_hex(16)
        lease: dict[str, object] = {
            "token": token,
            "pid": 0,
            "expires": time.time() + LEASE_SECONDS,
        }
        _write_lease(state, lease)
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "cuanta.adapters.graph.index_graph", str(self._root), token],
                cwd=self._root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                creationflags=_detached_flags(),
            )
        except OSError:
            if _read_lease(state).get("token") == token:
                _state_file(state, LOCK_FILE).unlink(missing_ok=True)
            raise
        lease["pid"] = process.pid
        _write_lease(state, lease)
        _record(state, token, "scheduled")


def run_worker(root: Path, token: str, runner: ProcessRunner) -> None:
    try:
        state = _state_dir(root)
        lease = _read_lease(state)
        if lease.get("token") != token:
            return
        if not _leased(lease):
            _record(state, token, "expired", "worker lease has expired")
            return
        files = LocalIndexInventory(root).candidates()
        if not _eligible(root, files):
            _record(state, token, "skipped", "tree is small or graph is no longer older")
            return
        if not _renew(state, token):
            _record(state, token, "expired", "worker no longer owns a live lease")
            return
        graph = GraphifyTool(runner)
        if not graph.available():
            _record(state, token, "broken", "graphify executable health failed")
            return
        _record(state, token, "running")
        result = graph.update(root)
        _record(state, token, "updated" if result.ok else "failed", result.detail)
    except (OSError, ValueError) as error:
        with suppress(OSError, ValueError):
            _record(_state_dir(root), token, "failed", str(error))
    finally:
        with suppress(OSError, ValueError):
            _release(_state_dir(root), token)


def main() -> int:
    if len(sys.argv) != 3 or not revalid_token(sys.argv[2]):
        return 2
    run_worker(Path(sys.argv[1]).resolve(), sys.argv[2], SubprocessRunner())
    return 0


def revalid_token(token: str) -> bool:
    return len(token) == 32 and all(character in "0123456789abcdef" for character in token)


if __name__ == "__main__":
    raise SystemExit(main())
