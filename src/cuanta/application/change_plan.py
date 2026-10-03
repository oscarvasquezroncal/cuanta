from __future__ import annotations

import json
from collections.abc import Callable

from cuanta.domain.change_plan import ChangePlan, apply_overrides, compile_change_plan
from cuanta.domain.mandate import MandateRequest
from cuanta.ports.code_index import CodeIndex


class IndexChangePlan:
    def __init__(
        self, index: CodeIndex, now: Callable[[], str], excluded: frozenset[str] = frozenset()
    ) -> None:
        self._index = index
        self._now = now
        self._excluded = excluded

    def compile(
        self, request: MandateRequest, overrides: tuple[tuple[str, str], ...] = ()
    ) -> ChangePlan:
        try:
            commands: object = json.loads(self._index.meta().get("verify_commands", "[]"))
        except ValueError:
            commands = []
        verified = (
            tuple(item for item in commands if isinstance(item, str))
            if isinstance(commands, list)
            else ()
        )
        plan = compile_change_plan(
            request,
            self._index.files(),
            self._index.rows("symbols"),
            self._index.rows("edges"),
            self._index.rows("notes"),
            self._index.rows("rules"),
            self._index.rows("history"),
            self._index.rows("test_links"),
            verified,
            self._now(),
            self._excluded,
        )
        return apply_overrides(plan, overrides)
