from __future__ import annotations

import json

from cuanta.ports.workspace import Workspace

RUNS_DIR = ".cuanta/runs"
BLOBS_DIR = ".cuanta/blobs"
REPORT_NAME = "report.md"
META_NAME = "run.json"
BLOB_LIMIT_BYTES = 256_000
BINARY_MARKERS = ("\x00", "�")


class RunReports:
    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    def folder(self, run_id: str) -> str:
        return f"{RUNS_DIR}/{run_id}"

    def report_path(self, run_id: str) -> str:
        return f"{self.folder(run_id)}/{REPORT_NAME}"

    def meta_path(self, run_id: str) -> str:
        return f"{self.folder(run_id)}/{META_NAME}"

    def save_report(self, run_id: str, text: str) -> str:
        path = self.report_path(run_id)
        self._workspace.write_text(path, text if text.endswith("\n") else text + "\n")
        return path

    def report(self, run_id: str) -> str | None:
        return self._workspace.read_text(self.report_path(run_id))

    def save_meta(self, run_id: str, payload: dict[str, object]) -> str:
        path = self.meta_path(run_id)
        self._workspace.write_text(path, json.dumps(payload, indent=2) + "\n")
        return path

    def meta(self, run_id: str) -> dict[str, object] | None:
        text = self._workspace.read_text(self.meta_path(run_id))
        if text is None:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def keep_blob(self, path: str, digest: str) -> None:
        target = f"{BLOBS_DIR}/{digest}"
        if self._workspace.exists(target):
            return
        if self._workspace.size_bytes(path) > BLOB_LIMIT_BYTES:
            return
        text = self._workspace.read_text(path)
        if text is None or any(marker in text for marker in BINARY_MARKERS):
            return
        self._workspace.write_text(target, text)

    def blob(self, digest: str) -> str | None:
        return self._workspace.read_text(f"{BLOBS_DIR}/{digest}")
