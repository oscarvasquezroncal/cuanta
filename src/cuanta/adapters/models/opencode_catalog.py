from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from cuanta.domain.models import Availability, ModelEntry
from cuanta.ports.system import ProcessRunner

LIST_COMMAND = ("opencode", "models", "--verbose")
ACTIVE = "active"
TIMEOUT_S = 90.0


def parse_verbose(text: str) -> tuple[tuple[str, dict[str, Any]], ...]:
    decoder = json.JSONDecoder()
    found: list[tuple[str, dict[str, Any]]] = []
    position = 0
    while position < len(text):
        newline = text.find("\n", position)
        if newline < 0:
            break
        header = text[position:newline].strip()
        position = newline + 1
        if not header or header.startswith(("{", "}", '"')) or "/" not in header:
            continue
        start = text.find("{", position)
        if start < 0:
            break
        try:
            data, end = decoder.raw_decode(text, start)
        except ValueError:
            continue
        position = end
        if isinstance(data, dict):
            found.append((header, data))
    return tuple(found)


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _table(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def configured_model(paths: tuple[Path, ...]) -> str:
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("model"), str):
            return str(data["model"])
    return ""


class OpenCodeCatalog:
    def __init__(self, runner: ProcessRunner, home: Path, project: Path) -> None:
        self._runner = runner
        self._configs = (
            project / "opencode.json",
            home / ".config" / "opencode" / "opencode.json",
        )

    @property
    def engine(self) -> str:
        return "opencode"

    def available(self) -> bool:
        return self._runner.which(LIST_COMMAND[0]) is not None

    def list(self) -> tuple[ModelEntry, ...]:
        completed = self._runner.run(list(LIST_COMMAND), timeout=TIMEOUT_S)
        default = configured_model(self._configs)
        entries: list[ModelEntry] = []
        for reference, data in parse_verbose(completed.stdout if completed.ok else ""):
            if data.get("status", ACTIVE) != ACTIVE:
                continue
            cost = _table(data, "cost")
            context = _table(data, "limit").get("context")
            entry = ModelEntry(
                engine=self.engine,
                id=reference,
                display=str(data.get("name") or reference),
                provider=str(data.get("providerID") or reference.split("/", 1)[0]),
                context=context if isinstance(context, int) else 0,
                input_price=_number(cost.get("input")),
                output_price=_number(cost.get("output")),
                efforts=tuple(str(name) for name in _table(data, "variants")),
                resolved=reference,
                default=reference == default,
            )
            if entry.default:
                entry = replace(entry, availability=Availability.CONFIGURED)
            entries.append(entry)
        return tuple(entries)
