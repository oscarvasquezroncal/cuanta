from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

FORGE_PLUGIN = "claude-agent-forge"
LEAN = "lean"
FULL = "full"
SESSIONS = (LEAN, FULL)


@dataclass(frozen=True, slots=True)
class InstalledPlugin:
    key: str
    version: str
    scope: str

    @property
    def name(self) -> str:
        return self.key.split("@", 1)[0]


def parse_installed(text: str) -> tuple[InstalledPlugin, ...]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ()
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        return ()
    found: list[InstalledPlugin] = []
    for key, entries in plugins.items():
        items = entries if isinstance(entries, list) else [entries]
        for entry in items:
            if isinstance(entry, dict):
                found.append(
                    InstalledPlugin(
                        str(key), str(entry.get("version", "")), str(entry.get("scope", ""))
                    )
                )
    return tuple(found)


def version_tuple(version: str) -> tuple[int, ...] | None:
    parts = re.findall(r"\d+", version)
    if not re.fullmatch(r"v?\d+(\.\d+)*([-+].*)?", version.strip()) or not parts:
        return None
    return tuple(int(part) for part in parts[:3])


def older_forge(plugins: Sequence[InstalledPlugin], vendored: str) -> tuple[InstalledPlugin, ...]:
    target = version_tuple(vendored)
    if target is None:
        return ()
    stale: list[InstalledPlugin] = []
    for plugin in plugins:
        current = version_tuple(plugin.version)
        if plugin.name == FORGE_PLUGIN and current is not None and current < target:
            stale.append(plugin)
    return tuple(stale)


BUILTIN_LEAN_OFF = ("agents-md@builtin",)


def lean_settings(plugins: Sequence[InstalledPlugin]) -> dict[str, object]:
    keys = sorted({*(plugin.key for plugin in plugins), *BUILTIN_LEAN_OFF})
    disabled: Mapping[str, bool] = dict.fromkeys(keys, False)
    return {"disableAllHooks": True, "enabledPlugins": dict(disabled)}


EMPTY_MCP: dict[str, object] = {"mcpServers": {}}
