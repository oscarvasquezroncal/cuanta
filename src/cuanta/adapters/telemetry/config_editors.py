from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit.items import Table

from cuanta.domain.messages import Message, msg
from cuanta.domain.telemetry import (
    WiringPlan,
    WiringReport,
    WiringState,
    claude_env,
    codex_otel_table,
    endpoint,
)

MANIFEST = "manifest.json"


class Backups:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def _manifest(self) -> dict[str, Any]:
        try:
            data = json.loads((self._directory / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, manifest: dict[str, Any]) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        (self._directory / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def remember(self, target: Path, name: str) -> None:
        manifest = self._manifest()
        key = str(target)
        if key in manifest:
            return
        entry: dict[str, Any] = {"existed": target.exists(), "backup": ""}
        if target.exists():
            self._directory.mkdir(parents=True, exist_ok=True)
            backup = self._directory / name
            shutil.copy2(target, backup)
            entry["backup"] = str(backup)
        manifest[key] = entry
        self._save(manifest)

    def plan(self, target: Path, name: str) -> WiringPlan:
        exists = target.exists()
        recorded = self._manifest().get(str(target))
        if isinstance(recorded, dict) and recorded.get("backup"):
            backup = str(recorded["backup"])
        else:
            backup = str(self._directory / name) if exists else ""
        return WiringPlan("", str(target), backup, exists)

    def restore(self, target: Path) -> Message:
        manifest = self._manifest()
        entry = manifest.pop(str(target), None)
        if not isinstance(entry, dict):
            return msg("wiring.no_backup")
        backup = Path(str(entry.get("backup") or ""))
        if entry.get("existed") and backup.is_file():
            shutil.copy2(backup, target)
            outcome = msg("wiring.restored", backup=backup.name)
        else:
            target.unlink(missing_ok=True)
            outcome = msg("wiring.removed")
        self._save(manifest)
        return outcome


class ClaudeSettingsWiring:
    engine = "claude"

    def __init__(self, project: Path, backups: Backups) -> None:
        self._path = project / ".claude" / "settings.local.json"
        self._backups = backups

    def plan(self) -> WiringPlan:
        return replace(
            self._backups.plan(self._path, "claude-settings.local.json.bak"), engine=self.engine
        )

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def enable(self, port: int, project: str) -> WiringReport:
        self._backups.remember(self._path, "claude-settings.local.json.bak")
        document = self._read()
        env = document.get("env")
        env = dict(env) if isinstance(env, dict) else {}
        env.update(claude_env(port, project))
        document["env"] = env
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        return WiringReport(
            self.engine,
            WiringState.ON,
            msg("wiring.env_block", endpoint=endpoint(port)),
            str(self._path),
        )

    def disable(self) -> WiringReport:
        outcome = self._backups.restore(self._path)
        return WiringReport(self.engine, WiringState.OFF, outcome, str(self._path))

    def status(self, port: int) -> WiringReport:
        env = self._read().get("env")
        if not isinstance(env, dict) or env.get("CLAUDE_CODE_ENABLE_TELEMETRY") != "1":
            return WiringReport(
                self.engine, WiringState.OFF, msg("wiring.interactive_off"), str(self._path)
            )
        target = str(env.get("OTEL_EXPORTER_OTLP_ENDPOINT", ""))
        if target == endpoint(port):
            return WiringReport(
                self.engine, WiringState.ON, msg("wiring.target", target=target), str(self._path)
            )
        return WiringReport(
            self.engine,
            WiringState.OTHER,
            msg("wiring.exports", target=target or "default"),
            str(self._path),
        )


class CodexConfigWiring:
    engine = "codex"

    def __init__(self, home: Path, backups: Backups, installed: bool) -> None:
        self._path = home / ".codex" / "config.toml"
        self._backups = backups
        self._installed = installed

    def plan(self) -> WiringPlan:
        return replace(self._backups.plan(self._path, "codex-config.toml.bak"), engine=self.engine)

    def _document(self) -> tomlkit.TOMLDocument:
        if self._path.is_file():
            return tomlkit.parse(self._path.read_text(encoding="utf-8"))
        return tomlkit.document()

    def enable(self, port: int, project: str) -> WiringReport:
        if not self._installed and not self._path.parent.is_dir():
            return WiringReport(
                self.engine, WiringState.UNAVAILABLE, msg("wiring.codex_missing"), str(self._path)
            )
        self._backups.remember(self._path, "codex-config.toml.bak")
        document = self._document()
        table = tomlkit.table()
        for key, value in codex_otel_table(port).items():
            table[key] = _inline(value)
        document["otel"] = table
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(tomlkit.dumps(document), encoding="utf-8")
        return WiringReport(
            self.engine,
            WiringState.ON,
            msg("wiring.otel", endpoint=endpoint(port)),
            str(self._path),
        )

    def disable(self) -> WiringReport:
        outcome = self._backups.restore(self._path)
        return WiringReport(self.engine, WiringState.OFF, outcome, str(self._path))

    def status(self, port: int) -> WiringReport:
        if not self._path.is_file():
            state = WiringState.OFF if self._installed else WiringState.UNAVAILABLE
            return WiringReport(self.engine, state, msg("wiring.no_codex_config"), str(self._path))
        try:
            document = self._document()
        except Exception as error:
            return WiringReport(
                self.engine,
                WiringState.OTHER,
                msg("wiring.unreadable", error=error),
                str(self._path),
            )
        otel = document.get("otel")
        if not isinstance(otel, Table | dict):
            return WiringReport(
                self.engine, WiringState.OFF, msg("wiring.no_otel"), str(self._path)
            )
        exporter = otel.get("exporter")
        target = ""
        if isinstance(exporter, dict):
            http = exporter.get("otlp-http")
            if isinstance(http, dict):
                target = str(http.get("endpoint", ""))
        if target.startswith(endpoint(port)):
            return WiringReport(
                self.engine, WiringState.ON, msg("wiring.target", target=target), str(self._path)
            )
        return WiringReport(
            self.engine,
            WiringState.OTHER,
            msg("wiring.exporter", target=target or exporter),
            str(self._path),
        )


def _inline(value: object) -> object:
    if isinstance(value, dict):
        table = tomlkit.inline_table()
        for key, inner in value.items():
            table[key] = _inline(inner)
        return table
    return value
