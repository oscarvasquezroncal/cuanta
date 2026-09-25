from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from cuanta.domain.models import Access, Availability, ModelEntry
from cuanta.domain.pricing import PriceTable
from cuanta.ports.system import ProcessRunner

LIST_COMMAND = ("codex", "debug", "models")
LISTED = "list"
API_KEY_ENV = "OPENAI_API_KEY"
TIMEOUT_S = 60.0


def configured_models(path: Path) -> tuple[str, tuple[str, ...]]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return "", ()
    default = data.get("model")
    profiles = data.get("profiles")
    named: list[str] = []
    if isinstance(profiles, dict):
        for profile in profiles.values():
            if isinstance(profile, dict) and isinstance(profile.get("model"), str):
                named.append(profile["model"])
    return (default if isinstance(default, str) else ""), tuple(named)


def parse_models(text: str) -> tuple[dict[str, Any], ...]:
    try:
        data = json.loads(text)
    except ValueError:
        return ()
    items = data.get("models") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return ()
    return tuple(item for item in items if isinstance(item, dict) and item.get("slug"))


def efforts_of(item: Mapping[str, Any]) -> tuple[str, ...]:
    levels = item.get("supported_reasoning_levels") or []
    return tuple(str(level.get("effort")) for level in levels if isinstance(level, dict))


class CodexCatalog:
    def __init__(
        self, runner: ProcessRunner, home: Path, environ: Mapping[str, str], prices: PriceTable
    ) -> None:
        self._runner = runner
        self._config = home / ".codex" / "config.toml"
        self._environ = environ
        self._prices = prices

    @property
    def engine(self) -> str:
        return "codex"

    def available(self) -> bool:
        return self._runner.which(LIST_COMMAND[0]) is not None

    def _entry(
        self, slug: str, display: str, context: int, efforts: tuple[str, ...], default: str
    ) -> ModelEntry:
        price = self._prices.lookup(slug)
        return ModelEntry(
            engine=self.engine,
            id=slug,
            display=display,
            provider="openai",
            context=context,
            input_price=price.input if price else None,
            output_price=price.output if price else None,
            efforts=efforts,
            access=Access.API if self._environ.get(API_KEY_ENV) else Access.UNKNOWN,
            resolved=slug,
            default=slug == default,
        )

    def list(self) -> tuple[ModelEntry, ...]:
        default, profiles = configured_models(self._config)
        configured = {default, *profiles} - {""}
        completed = self._runner.run(list(LIST_COMMAND), timeout=TIMEOUT_S)
        entries: list[ModelEntry] = []
        for item in parse_models(completed.stdout if completed.ok else ""):
            slug = str(item["slug"])
            if item.get("visibility", LISTED) != LISTED and slug not in configured:
                continue
            context = item.get("context_window")
            entries.append(
                self._entry(
                    slug,
                    str(item.get("display_name") or slug),
                    context if isinstance(context, int) else 0,
                    efforts_of(item),
                    default,
                )
            )
        known = {entry.id for entry in entries}
        entries.extend(
            self._entry(name, name, 0, (), default) for name in sorted(configured - known)
        )
        return tuple(
            replace(entry, availability=Availability.CONFIGURED)
            if entry.id in configured
            else entry
            for entry in entries
        )
