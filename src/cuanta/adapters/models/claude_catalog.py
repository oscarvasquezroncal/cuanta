from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from cuanta.adapters.models.tiers import ClaudeFacts, claude_facts
from cuanta.domain.models import Access, Availability, ModelEntry
from cuanta.domain.pricing import PriceTable

ALIAS_ENV = "ANTHROPIC_DEFAULT_{alias}_MODEL"
MODEL_ENV = "ANTHROPIC_MODEL"
API_KEY_ENV = "ANTHROPIC_API_KEY"
DEFAULT_ALIAS = "opus"
MANAGED_SETTINGS = (
    Path("C:/Program Files/ClaudeCode/managed-settings.json"),
    Path("/Library/Application Support/ClaudeCode/managed-settings.json"),
    Path("/etc/claude-code/managed-settings.json"),
)


def read_settings(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: data[key] for key in ("model", "availableModels") if key in data}


def read_env_block(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    block = data.get("env") if isinstance(data, dict) else None
    if not isinstance(block, dict):
        return {}
    return {str(key): str(value) for key, value in block.items() if isinstance(value, str | int)}


def settings_env(home: Path, project: Path) -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in (
        home / ".claude" / "settings.json",
        project / ".claude" / "settings.json",
        project / ".claude" / "settings.local.json",
    ):
        merged.update(read_env_block(path))
    return merged


def allowed(reference: str, resolved: str, allowlist: tuple[str, ...]) -> bool:
    if not allowlist:
        return True
    for item in (entry.lower() for entry in allowlist):
        if item in {reference.lower(), resolved.lower()} or resolved.lower().startswith(item):
            return True
    return False


class ClaudeCatalog:
    def __init__(
        self,
        home: Path,
        project: Path,
        environ: Mapping[str, str],
        prices: PriceTable,
        installed: bool,
        managed: tuple[Path, ...] = MANAGED_SETTINGS,
    ) -> None:
        self._layers = (
            *managed,
            project / ".claude" / "settings.local.json",
            project / ".claude" / "settings.json",
            home / ".claude" / "settings.json",
        )
        self._environ = environ
        self._prices = prices
        self._installed = installed

    @property
    def engine(self) -> str:
        return "claude"

    def available(self) -> bool:
        return self._installed

    def _setting(self, key: str) -> object:
        for path in self._layers:
            value = read_settings(path).get(key)
            if value is not None:
                return value
        return None

    def _resolved(self, fact: ClaudeFacts) -> str:
        override = self._environ.get(ALIAS_ENV.format(alias=fact.alias.upper()), "").strip()
        return override or fact.resolves

    def list(self) -> tuple[ModelEntry, ...]:
        raw = self._setting("availableModels")
        allowlist = tuple(str(item) for item in raw) if isinstance(raw, list) else ()
        configured = self._environ.get(MODEL_ENV, "").strip() or str(self._setting("model") or "")
        default = (configured or DEFAULT_ALIAS).lower()
        access = Access.API if self._environ.get(API_KEY_ENV) else Access.UNKNOWN
        entries: list[ModelEntry] = []
        for fact in claude_facts():
            resolved = self._resolved(fact)
            if not allowed(fact.alias, resolved, allowlist):
                continue
            price = self._prices.lookup(resolved)
            chosen = default in {fact.alias, resolved.lower()}
            listed = bool(allowlist) or (chosen and bool(configured))
            entries.append(
                ModelEntry(
                    engine=self.engine,
                    id=fact.alias,
                    display=fact.display,
                    provider="anthropic",
                    context=fact.context,
                    input_price=price.input if price else None,
                    output_price=price.output if price else None,
                    efforts=fact.efforts,
                    availability=Availability.CONFIGURED if listed else Availability.DETECTED,
                    access=access,
                    resolved=resolved,
                    default=chosen,
                )
            )
        aliased = {entry.resolved.lower() for entry in entries}
        for name in self._prices.names():
            if not name.startswith("claude-") or name in aliased:
                continue
            if not allowed(name, name, allowlist):
                continue
            price = self._prices.lookup(name)
            entries.append(
                ModelEntry(
                    engine=self.engine,
                    id=name,
                    display=name,
                    provider="anthropic",
                    input_price=price.input if price else None,
                    output_price=price.output if price else None,
                    access=access,
                    resolved=name,
                    default=default == name,
                )
            )
        return tuple(entries)
