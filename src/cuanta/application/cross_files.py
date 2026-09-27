from __future__ import annotations

import re
from collections.abc import Sequence

from cuanta.domain.change_plan import ChangePlan
from cuanta.ports.workspace import Workspace

MARKERS = frozenset("*?[")
MEANT_EMPTY = frozenset({"__init__.py", ".gitkeep", ".keep", "py.typed"})
ROOT_FILE = re.compile(r"[\w-]+(?:\.[\w-]+)*\.[A-Za-z0-9]{1,5}")


class CodexFileGuard:
    def __init__(self, workspace: Workspace, exclusions: frozenset[str]) -> None:
        self._workspace = workspace
        self._exclusions = exclusions

    def prepare(self, plan: ChangePlan | None) -> tuple[str, ...]:
        created: list[str] = []
        for target in plan.edit if plan is not None else ():
            path = target.path
            if (
                MARKERS & set(path)
                or ("/" not in path.strip("/") and ROOT_FILE.fullmatch(path) is None)
                or path.endswith("/")
                or self._workspace.exists(path)
            ):
                continue
            try:
                self._workspace.write_text(path, "")
            except OSError:
                continue
            created.append(path)
        return tuple(created)

    def settle(self, created: Sequence[str]) -> tuple[str, ...]:
        removed: list[str] = []
        for path in created:
            if path.rsplit("/", 1)[-1] in MEANT_EMPTY:
                continue
            content = self._workspace.read_bytes(path)
            if content is not None and not content:
                try:
                    self._workspace.remove(path)
                except OSError:
                    continue
                removed.append(path)
        return tuple(removed)

    def unreadable(self) -> tuple[str, ...]:
        scan = self._workspace.scan(self._exclusions, collect_files=True, all_files=True)
        return tuple(path for path in scan.files if self._workspace.sha256(path) is None)
