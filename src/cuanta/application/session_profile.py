from __future__ import annotations

from collections.abc import Callable, Mapping

from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.plugins import EMPTY_MCP, InstalledPlugin, lean_settings
from cuanta.domain.stable import content_name, stable_json
from cuanta.ports.ledger import EventQuery, Ledger
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

    def files(
        self,
        permissions_deny: tuple[str, ...] = (),
        owned_hooks: dict[str, object] | None = None,
        owned_mcp: Mapping[str, object] | None = None,
    ) -> tuple[str, str]:
        mcp = self._write(
            "lean-mcp", stable_json(owned_mcp if owned_mcp is not None else EMPTY_MCP)
        )
        settings = self._write(
            "lean-settings",
            stable_json(lean_settings(self._plugins(), permissions_deny, owned_hooks)),
        )
        return mcp, settings


def record_hook_event(ledger: Ledger, event: LedgerEvent) -> int:
    if not event.run_id or not event.session_id or not event.tool_use_id:
        return 0
    if ledger.get_run(event.run_id) is None:
        return 0
    existing = ledger.events(EventQuery(run_id=event.run_id, session_id=event.session_id))
    if any(
        item.source == event.source
        and item.kind == event.kind
        and item.tool_use_id == event.tool_use_id
        for item in existing
    ):
        return 0
    return ledger.add_events((event,))
