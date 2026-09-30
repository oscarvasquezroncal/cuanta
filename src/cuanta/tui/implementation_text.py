from __future__ import annotations

from collections.abc import Mapping

from textual.content import Content

from cuanta.tui.i18n import Catalog


def implementation_content(t: Catalog, value: Mapping[str, object]) -> Content:
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
            translated = (
                t(f"result.implementation_{state_key}")
                if state_key
                in {"green", "red", "pending", "running", "failed", "blocked", "repairing"}
                else state_key
            )
            rows.append(
                t("result.implementation_step", title=step.get("title", ""), state=translated)
            )
    if reason := value.get("reason"):
        reason_key = str(reason)
        known = {
            "time_limit",
            "turn_limit",
            "cost_unknown",
            "cost_limit",
            "session_closed",
            "engine_failed",
            "invalid_step_plan",
            "planning_changes",
            "repair_limit",
            "verification_failed",
        }
        translated_reason = (
            t(f"result.implementation_reason_{reason_key}") if reason_key in known else reason
        )
        rows.append(t("result.implementation_stop", reason=translated_reason))
    checks = value.get("checks")
    if isinstance(checks, list) and checks and isinstance(checks[-1], dict):
        for field in ("introduced", "preexisting"):
            errors = checks[-1].get(field)
            if isinstance(errors, list):
                rows.extend(t(f"result.implementation_{field}", error=error) for error in errors)
    return Content("\n".join(rows))
