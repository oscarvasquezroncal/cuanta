from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx

from cuanta.adapters.telemetry.otlp_receiver import build_listener, next_free_port
from cuanta.domain.errors import EnvironmentFailure
from cuanta.domain.telemetry import endpoint
from cuanta.ports.ledger import Ledger
from cuanta.ports.listener import ListenerStatus

PIDFILE = "listener.json"
HEALTH_TIMEOUT_S = 1.0
START_TIMEOUT_S = 15.0


class LocalListenerControl:
    def __init__(
        self,
        cuanta_dir: Path,
        project: Path,
        ledger_factory: Callable[[], Ledger],
        keep_prompts: bool = False,
        linger_s: float = 1.5,
    ) -> None:
        self._linger = linger_s
        self._dir = cuanta_dir
        self._project = project
        self._ledger_factory = ledger_factory
        self._keep_prompts = keep_prompts

    @property
    def pidfile(self) -> Path:
        return self._dir / PIDFILE

    def _record(self) -> dict[str, object]:
        try:
            data = json.loads(self.pidfile.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_record(self, port: int, pid: int, token: str) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self.pidfile.write_text(
            json.dumps({"port": port, "pid": pid, "token": token}), encoding="utf-8"
        )

    def _probe(self, port: int) -> ListenerStatus:
        try:
            response = httpx.get(f"{endpoint(port)}/health", timeout=HEALTH_TIMEOUT_S)
            data = response.json()
        except (httpx.HTTPError, ValueError):
            return ListenerStatus(False, port)
        if not isinstance(data, dict) or not data.get("ok"):
            return ListenerStatus(False, port)
        return ListenerStatus(
            True,
            port,
            int(data.get("pid") or 0),
            int(data.get("received") or 0),
            int(data.get("written") or 0),
            float(data.get("uptime_s") or 0.0),
        )

    def status(self) -> ListenerStatus:
        record = self._record()
        port = record.get("port")
        if not isinstance(port, int):
            return ListenerStatus(False)
        return self._probe(port)

    def free_port(self, preferred: int) -> int:
        return next_free_port(preferred)

    def start_background(self, port: int) -> ListenerStatus:
        current = self.status()
        if current.running:
            return current
        chosen = self.free_port(port)
        command = [
            sys.executable,
            "-m",
            "cuanta",
            "--project",
            str(self._project),
            "--plain",
            "listen",
            "--port",
            str(chosen),
        ]
        self._dir.mkdir(parents=True, exist_ok=True)
        with (self._dir / "listener.log").open("ab") as log:
            if os.name == "nt":
                flags = (
                    getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
                subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    cwd=self._project,
                    close_fds=True,
                    creationflags=flags,
                )
            else:
                subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    cwd=self._project,
                    close_fds=True,
                    start_new_session=True,
                )
        deadline = time.monotonic() + START_TIMEOUT_S
        while time.monotonic() < deadline:
            probe = self._probe(chosen)
            if probe.running:
                return probe
            time.sleep(0.2)
        raise EnvironmentFailure(
            f"listener did not start on port {chosen}", f"see {self._dir / 'listener.log'}"
        )

    def stop(self) -> bool:
        record = self._record()
        port, token = record.get("port"), record.get("token")
        if not isinstance(port, int) or not isinstance(token, str):
            return False
        try:
            httpx.post(
                f"{endpoint(port)}/shutdown",
                headers={"X-Cuanta-Token": token},
                timeout=HEALTH_TIMEOUT_S * 3,
            )
        except httpx.HTTPError:
            return False
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self._probe(port).running:
            time.sleep(0.1)
        stopped = not self._probe(port).running
        if stopped:
            self.pidfile.unlink(missing_ok=True)
        return stopped

    def serve(self, port: int, on_ready: Callable[[ListenerStatus], None]) -> None:
        token = secrets.token_hex(16)
        listener = build_listener(port, self._ledger_factory, self._keep_prompts, token)
        self._write_record(listener.port, os.getpid(), token)
        on_ready(ListenerStatus(True, listener.port, os.getpid(), owned=True))
        try:
            listener.serve_forever()
        finally:
            record = self._record()
            if record.get("pid") == os.getpid():
                self.pidfile.unlink(missing_ok=True)

    @contextmanager
    def scoped(self, port: int) -> Iterator[ListenerStatus]:
        current = self.status()
        if current.running:
            yield current
            return
        token = secrets.token_hex(16)
        listener = build_listener(
            self.free_port(port), self._ledger_factory, self._keep_prompts, token
        )
        listener.start()
        self._write_record(listener.port, os.getpid(), token)
        try:
            yield ListenerStatus(True, listener.port, os.getpid(), owned=True)
        finally:
            time.sleep(self._linger)
            listener.stop()
            record = self._record()
            if record.get("token") == token:
                self.pidfile.unlink(missing_ok=True)
