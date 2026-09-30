from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.adapters.system.process_tree import CREATE_BREAKAWAY_FROM_JOB
from cuanta.adapters.system.sandbox import MARKER, _alive, fingerprint
from cuanta.adapters.system.warm_sandbox import WarmSandbox, _json
from cuanta.domain.errors import EnvironmentFailure
from cuanta.domain.ledger import LedgerEvent
from cuanta.ports.sandbox import SandboxCopy

FINISHED = frozenset({"ready", "removed", "failed"})


class BackgroundCleanup:
    def __init__(
        self,
        copy: SandboxCopy,
        run_id: str,
        state: Path,
        parents: Sequence[Path] | None = None,
    ) -> None:
        self._path = state / "trials" / run_id / "cleanup.json"
        self._slot = copy.slot
        self._started = False
        if parents is None and copy.slot.parent.name == "cuanta-sandbox":
            parents = (copy.slot.parent.parent,)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(
                {
                    "status": "pending",
                    "origin": str(copy.origin),
                    "slot": str(copy.slot),
                    "parents": [str(path) for path in parents] if parents is not None else None,
                    "linked": list(copy.linked),
                    "fingerprint": copy.fingerprint,
                    "run_id": run_id,
                    "ledger": str(state / "ledger.db"),
                }
            ),
            encoding="utf-8",
        )

    def __call__(self) -> None:
        if self._started:
            return
        self._started = True
        flags = (
            subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | CREATE_BREAKAWAY_FROM_JOB
            if os.name == "nt"
            else 0
        )
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "cuanta.adapters.system.sandbox_cleanup", str(self._path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=flags,
                start_new_session=os.name != "nt",
            )
        except OSError as error:
            state = _json(self._path)
            self._path.write_text(
                json.dumps({**state, "status": "failed", "error": type(error).__name__}),
                encoding="utf-8",
            )
        else:
            process.poll()
            self._hand_over(process.pid)

    def _hand_over(self, pid: int) -> None:
        marker = self._slot / MARKER
        with suppress(OSError, ValueError):
            marker.write_text(json.dumps({**_json(marker), "pid": pid}), encoding="utf-8")


def finish_cleanup(path: Path) -> bool:
    state = _json(path)
    if state.get("status") in FINISHED:
        return state["status"] != "failed"
    path.write_text(
        json.dumps({**state, "status": "running", "pid": os.getpid()}), encoding="utf-8"
    )
    started = time.perf_counter()
    status, error = "failed", ""
    try:
        origin, slot = Path(str(state["origin"])), Path(str(state["slot"]))
        parents = state.get("parents")
        sandbox = WarmSandbox(
            parents=tuple(Path(str(parent)) for parent in parents)
            if isinstance(parents, list)
            else None
        )
        linked = state.get("linked")
        actual = fingerprint(
            origin, tuple(str(item) for item in linked) if isinstance(linked, list) else ()
        )
        expected = json.loads(json.dumps(actual))
        if expected != state.get("fingerprint"):
            success = sandbox._delete(slot, origin, strict=True)
        else:
            success = sandbox.release(origin, slot)
        status = ("ready" if slot.exists() else "removed") if success else "failed"
    except (OSError, ValueError, KeyError, EnvironmentFailure) as failure:
        error = type(failure).__name__
    seconds = time.perf_counter() - started
    ledger_path = Path(str(state.get("ledger", "")))
    try:
        if ledger_path.is_file():
            ledger = SqliteLedger(ledger_path)
            try:
                ledger.add_events(
                    [
                        LedgerEvent(
                            run_id=str(state["run_id"]),
                            source="cuanta",
                            kind="phase_timing",
                            ts=datetime.now(UTC).isoformat(),
                            duration_ms=round(seconds * 1000),
                            raw=json.dumps(
                                {"phase": "copy_removal", "duration_ms": seconds * 1000}
                            ),
                        )
                    ]
                )
            finally:
                ledger.close()
    except (OSError, sqlite3.Error) as failure:
        error = error or f"timing_{type(failure).__name__}"
    path.write_text(
        json.dumps(
            {
                **state,
                "status": status,
                "seconds": seconds,
                "error": error or None,
                "pid": os.getpid(),
            }
        ),
        encoding="utf-8",
    )
    return status != "failed"


def await_cleanup(project: Path, run_id: str, timeout: float = 60.0) -> bool:
    path = project / ".cuanta" / "trials" / run_id / "cleanup.json"
    deadline = time.monotonic() + timeout
    while path.exists():
        try:
            state = _json(path)
            status = state.get("status")
            worker = state.get("pid")
        except (OSError, ValueError):
            status, worker = None, None
        if status in FINISHED and (worker == os.getpid() or not _alive(worker)):
            return status != "failed"
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


if __name__ == "__main__":
    raise SystemExit(0 if finish_cleanup(Path(sys.argv[1])) else 1)
