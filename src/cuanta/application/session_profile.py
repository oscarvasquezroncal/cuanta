from __future__ import annotations

from collections.abc import Callable

from cuanta.domain.plugins import EMPTY_MCP, InstalledPlugin, lean_settings
from cuanta.domain.stable import content_name, stable_json
from cuanta.ports.workspace import Workspace

LEAN_DIR = ".cuanta/tmp"


class LeanProfile:
    def __init__(
        self, workspace: Workspace, plugins: Callable[[], tuple[InstalledPlugin, ...]]
    ) -> None:
        self._workspace = workspace
        self._plugins = plugins

    def _write(self, stem: str, text: str) -> str:
        relative = content_name(LEAN_DIR, stem, text)
        self._workspace.write_text(relative, text)
        return str(self._workspace.root / relative)

    def files(self) -> tuple[str, str]:
        mcp = self._write("lean-mcp", stable_json(EMPTY_MCP))
        settings = self._write("lean-settings", stable_json(lean_settings(self._plugins())))
        return mcp, settings
