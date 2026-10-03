from __future__ import annotations

from collections.abc import Mapping

from textual.content import Content

from cuanta.domain.implementation import settled_state
from cuanta.tui.i18n import Catalog

STATES = frozenset(
    {"green", "red", "pending", "running", "failed", "blocked", "repairing", "stopped"}
)


def implementation_content(
    t: Catalog, value: Mapping[str, object], finished: bool = True
) -> Content:
    state = t("result.implementation_green" if value.get("passed") else "result.implementation_red")
    rows = [
        t(
            "result.implementation",
            state=state,
            repairs=value.get("repairs", 0),
            limit=value.get("repair_limit", 3),
            remaining=value.get("repair_rounds_remaining", "n/a"),
        )
    ]
    steps = value.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            state_key = str(step.get("state", "pending"))
            if finished:
                state_key = settled_state(state_key)
            translated = (
                t(f"result.implementation_{state_key}") if state_key in STATES else state_key
            )
            rows.append(
                t("result.implementation_step", title=step.get("title", ""), state=translated)
            )
    checks = value.get("checks")
    if isinstance(checks, list) and checks and isinstance(checks[-1], dict):
        for field in ("introduced", "preexisting"):
            errors = checks[-1].get(field)
            if isinstance(errors, list):
                rows.extend(t(f"result.implementation_{field}", error=error) for error in errors)
    return Content("\n".join(rows))
