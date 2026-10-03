from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from cuanta.domain.limits import RunLimits
from cuanta.domain.messages import Message, msg


class GuaranteeStatus(StrEnum):
    ENFORCED = "enforced"
    CHECKED = "checked"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class Guarantee:
    name: str
    status: GuaranteeStatus
    detail: Message

    @property
    def message(self) -> Message:
        return msg(
            "guarantee.row",
            name=msg(f"guarantee.{self.name}"),
            status=msg(f"guarantee.{self.status.value}"),
            detail=self.detail,
        )


def engine_guarantees(
    engine: str, *, file_checks: bool = True, limits: RunLimits | None = None
) -> tuple[Guarantee, ...]:
    enforced = GuaranteeStatus.ENFORCED
    checked = GuaranteeStatus.CHECKED
    unavailable = GuaranteeStatus.UNAVAILABLE
    statuses = {
        "claude": (enforced, enforced, checked if file_checks else unavailable, checked),
        "codex": (checked, unavailable, enforced, checked),
        "opencode": (enforced, unavailable, unavailable, checked),
    }.get(engine, (unavailable,) * 4)
    names = ("spend", "turns", "readonly", "telemetry")
    known = engine if engine in ("claude", "codex", "opencode") else "unknown"
    hidden = (
        set()
        if limits is None
        else {
            *(("spend",) if limits.budget_usd <= 0 else ()),
            *(("turns",) if limits.max_turns <= 0 else ()),
        }
    )
    return tuple(
        Guarantee(
            name,
            status,
            msg(
                "guarantee.claude_readonly_unchecked"
                if engine == "claude" and name == "readonly" and not file_checks
                else f"guarantee.{known}_{name}"
            ),
        )
        for name, status in zip(names, statuses, strict=True)
        if name not in hidden
    )


def cap_warning(engine: str, budget_usd: float) -> Message | None:
    if budget_usd <= 0 or engine == "claude":
        return None
    if engine == "opencode":
        return msg("guarantee.step_cap_warning")
    return msg("guarantee.cap_warning", engine=engine)


def readonly_unavailable(engine: str) -> Message | None:
    return msg("guarantee.opencode_refused") if engine == "opencode" else None
