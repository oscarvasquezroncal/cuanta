from __future__ import annotations

from collections.abc import Mapping, Sequence

from cuanta.domain.engine import (
    BUDGET_LIMIT_SUBTYPE,
    CANCELLED_SUBTYPE,
    COST_UNKNOWN_SUBTYPE,
    GOVERNOR_STOP_SUBTYPE,
    TURN_LIMIT_SUBTYPE,
    WALL_LIMIT_SUBTYPE,
)
from cuanta.domain.governor_report import GovernorSummary
from cuanta.domain.ledger import Run
from cuanta.domain.messages import Message, counted, msg
from cuanta.domain.pricing import dollars

FINISHED_STATUSES = frozenset({"ok", "completed"})
PLAIN_REASONS = frozenset({"session_closed", "invalid_step_plan", "planning_changes"})
SECONDS_PER_MINUTE = 60.0
TEAM_ENDINGS = {"partial": "stop.team_partial", "failed": "stop.team_failed"}


def seconds_text(value: float) -> str:
    return f"{value:,.0f}"


def minutes_text(seconds: float) -> str:
    return f"{seconds / SECONDS_PER_MINUTE:g}"


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _cap(run: Run, payload: Mapping[str, object]) -> float:
    found = _number(payload.get("cap_usd"))
    if found is not None and found > 0:
        return found
    return run.cap_usd if run.cap_usd is not None and run.cap_usd > 0 else 0.0


def _turns(run: Run, payload: Mapping[str, object]) -> int:
    found = _number(payload.get("max_turns"))
    if found is not None and found > 0:
        return int(found)
    return run.max_turns if run.max_turns > 0 else run.turns


def _steps(payload: Mapping[str, object]) -> Sequence[Mapping[str, object]]:
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return ()
    return tuple(step for step in steps if isinstance(step, dict))


def _introduced(payload: Mapping[str, object]) -> int:
    checks = payload.get("checks")
    if not isinstance(checks, list) or not checks or not isinstance(checks[-1], dict):
        return 0
    introduced = checks[-1].get("introduced")
    return len(introduced) if isinstance(introduced, list) else 0


def _unfinished(payload: Mapping[str, object]) -> Message:
    steps = _steps(payload)
    position = next(
        (
            index
            for index, step in enumerate(steps, 1)
            if step.get("state") in {"stopped", "running", "repairing"}
        ),
        next(
            (index for index, step in enumerate(steps, 1) if step.get("state") != "green"),
            len(steps),
        ),
    )
    return msg("stop.unfinished", step=max(1, position), total=max(1, len(steps)))


def _spend_stop(key: str, fallback: str, cap: float) -> Message:
    return msg(key, cap=dollars(cap)) if cap > 0 else msg(fallback)


def _implementation_stop(run: Run, payload: Mapping[str, object], reason: str) -> Message:
    limit = _number(payload.get("time_limit_s")) or 0.0
    if reason == "repair_time_limit":
        return msg("stop.repair_time_limit", seconds=seconds_text(limit))
    if reason == "time_limit":
        return msg("stop.time_limit", seconds=seconds_text(limit))
    if reason == "wall_limit":
        return msg("stop.wall_limit", minutes=minutes_text(run.max_wall_s))
    if reason == "turn_limit":
        return counted("stop.turn_limit", "turns", _turns(run, payload))
    if reason == "cost_limit":
        return _spend_stop("stop.cost_limit", "engine.budget_stopped", _cap(run, payload))
    if reason == "cost_unknown":
        return _spend_stop("stop.cost_unknown", "engine.cost_unknown", _cap(run, payload))
    if reason == "repair_limit":
        rounds = _number(payload.get("repair_limit"))
        return counted("stop.repair_limit", "rounds", int(rounds) if rounds is not None else 0)
    if reason == "verification_failed":
        return counted("stop.verification_failed", "count", _introduced(payload))
    if reason == "unfinished":
        return _unfinished(payload)
    if reason in PLAIN_REASONS:
        return msg(f"stop.{reason}")
    if reason == "stopped":
        return msg("stop.user")
    if reason == "engine_exited":
        code = _number(payload.get("exit_code"))
        return msg("stop.engine_exited", code=int(code) if code is not None else "?")
    subtype = str(payload.get("engine_subtype") or run.end_reason or reason)
    return msg("stop.engine_failed", subtype=subtype)


def _finished(governor: GovernorSummary | None) -> Message:
    asked = [
        entry
        for entry in (governor.reactions if governor is not None else ())
        if entry.kind == "finish_now" and entry.sent and entry.spent_usd is not None
    ]
    if not asked:
        return msg("stop.finished")
    last = asked[-1]
    return msg(
        "stop.finished_on_request", spent=dollars(last.spent_usd), limit=dollars(last.limit_usd)
    )


def team_stop(completion: str, stopped: Message | None) -> Message | None:
    ending = TEAM_ENDINGS.get(completion)
    if ending is None:
        return None
    return stopped or msg(ending)


def stop_message(
    run: Run,
    implementation: Mapping[str, object] | None = None,
    governor: GovernorSummary | None = None,
) -> Message:
    payload = implementation or {}
    reason = str(payload.get("reason") or "")
    if run.status == "running":
        return msg("stop.running")
    if run.status == "interrupted":
        return msg("stop.interrupted")
    if reason:
        return _implementation_stop(run, payload, reason)
    end = run.end_reason
    if end == CANCELLED_SUBTYPE:
        return msg("stop.user")
    if end == WALL_LIMIT_SUBTYPE:
        return msg("stop.wall_limit", minutes=minutes_text(run.max_wall_s))
    if end == BUDGET_LIMIT_SUBTYPE:
        return _spend_stop("stop.cost_limit", "engine.budget_stopped", _cap(run, payload))
    if end == COST_UNKNOWN_SUBTYPE:
        return _spend_stop("stop.cost_unknown", "engine.cost_unknown", _cap(run, payload))
    if end == GOVERNOR_STOP_SUBTYPE:
        return _spend_stop("stop.governor", "engine.budget_stopped", _cap(run, payload))
    if end == TURN_LIMIT_SUBTYPE:
        return counted("stop.turn_limit", "turns", _turns(run, payload))
    if run.status in FINISHED_STATUSES:
        return _finished(governor)
    if end in {"", "success"}:
        return msg("stop.no_result") if not end else msg("stop.failed")
    return msg("stop.engine_failed", subtype=end)
