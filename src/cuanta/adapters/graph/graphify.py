from __future__ import annotations

from pathlib import Path

from cuanta.ports.graph import GraphResult
from cuanta.ports.system import ProcessRunner

BINARY = "graphify"
PACKAGE = "graphifyy"
INSTALL_TIMEOUT_S = 600.0
UPDATE_TIMEOUT_S = 900.0
HEALTH_TIMEOUT_S = 5.0


def _tail(text: str) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1][:200] if lines else ""


class GraphifyTool:
    def __init__(self, runner: ProcessRunner) -> None:
        self._runner = runner

    def available(self) -> bool:
        return self._health().ok

    def _health(self) -> GraphResult:
        if self._runner.which(BINARY) is None:
            return GraphResult(False, "graphify not on PATH")
        completed = self._runner.run([BINARY, "--help"], timeout=HEALTH_TIMEOUT_S)
        output = f"{completed.stderr}\n{completed.stdout}"
        if not completed.ok or "uv trampoline failed" in output.lower():
            lines = [line.strip() for line in output.splitlines() if line.strip()]
            reason = next(
                (line for line in lines if "uv trampoline failed" in line.lower()),
                lines[-1] if lines else "",
            )
            failure = reason[:200] or f"graphify --help exited {completed.returncode}"
            return GraphResult(False, failure)
        return GraphResult(True, "graphify --help succeeded")

    def install(self) -> GraphResult:
        attempts = (["uv", "tool", "install", PACKAGE], ["pip", "install", PACKAGE])
        failures: list[str] = []
        for command in attempts:
            if self._runner.which(command[0]) is None:
                failures.append(f"{command[0]} not found")
                continue
            completed = self._runner.run(command, timeout=INSTALL_TIMEOUT_S)
            if completed.ok:
                return GraphResult(True, " ".join(command))
            failures.append(f"{' '.join(command)}: {_tail(completed.stderr or completed.stdout)}")
        return GraphResult(False, "; ".join(failures))

    def update(self, root: Path) -> GraphResult:
        health = self._health()
        if not health.ok:
            return health
        completed = self._runner.run([BINARY, "update", "."], cwd=root, timeout=UPDATE_TIMEOUT_S)
        if completed.ok:
            return GraphResult(True, "graphify update .")
        return GraphResult(False, _tail(completed.stderr or completed.stdout) or "graphify failed")
