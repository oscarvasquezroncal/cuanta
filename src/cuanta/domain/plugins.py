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
LSP_PLUGIN = "pyright-lsp@claude-plugins-official"
LSP_SERVER = "pyright-langserver"
LSP_LANGUAGES = frozenset({"python"})


def lsp_ready(language: str, server_found: bool, plugins: Sequence[InstalledPlugin]) -> bool:
    return (
        language in LSP_LANGUAGES
        and server_found
        and any(plugin.key == LSP_PLUGIN for plugin in plugins)
    )


def lean_settings(
    plugins: Sequence[InstalledPlugin],
    permissions_deny: tuple[str, ...] = (),
    owned_hooks: dict[str, object] | None = None,
    lsp: bool = False,
) -> dict[str, object]:
    keys = sorted({*(plugin.key for plugin in plugins), *BUILTIN_LEAN_OFF})
    disabled: Mapping[str, bool] = dict.fromkeys(keys, False)
    enabled = {key: lsp and key == LSP_PLUGIN for key in disabled}
    settings: dict[str, object] = {
        "disableAllHooks": owned_hooks is None,
        "enabledPlugins": enabled,
    }
    if permissions_deny:
        settings["permissions"] = {"deny": list(dict.fromkeys(permissions_deny))}
    if owned_hooks is not None:
        settings["hooks"] = owned_hooks
    return settings


EMPTY_MCP: dict[str, object] = {"mcpServers": {}}
