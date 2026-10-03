from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from cuanta.domain.config import Config
from cuanta.domain.depth import DepthProfile
from cuanta.domain.messages import Message, counted, msg
from cuanta.domain.pricing import dollars

SECONDS_PER_MINUTE = 60.0


class LimitsMode(StrEnum):
    OFF = "off"
    DEPTH = "depth"


@dataclass(frozen=True, slots=True)
class RunLimits:
    budget_usd: float = 0.0
    max_turns: int = 0
    wall_min: float = 0.0

    @property
    def wall_s(self) -> float:
        return self.wall_min * SECONDS_PER_MINUTE

    @property
    def active(self) -> bool:
        return self.budget_usd > 0 or self.max_turns > 0 or self.wall_min > 0


NO_LIMITS = RunLimits()


@dataclass(frozen=True, slots=True)
class LimitRequest:
    mode: str = ""
    budget_usd: float | None = None
    max_turns: int | None = None
    wall_min: float | None = None


@dataclass(frozen=True, slots=True)
class LimitSettings:
    mode: LimitsMode = LimitsMode.OFF
    fixed: RunLimits = NO_LIMITS


def parse_limits_mode(text: str) -> LimitsMode:
    try:
        return LimitsMode(text)
    except ValueError:
        return LimitsMode.OFF


def limit_settings(config: Config) -> LimitSettings:
    return LimitSettings(
        parse_limits_mode(config.limits_mode),
        RunLimits(max(0.0, config.budget_usd), max(0, config.max_turns), max(0.0, config.wall_min)),
    )


def depth_limits(chosen: DepthProfile) -> RunLimits:
    return RunLimits(chosen.cost_cap_usd, chosen.max_turns)


def resolve_limits(
    request: LimitRequest, settings: LimitSettings, chosen: DepthProfile
) -> RunLimits:
    mode = parse_limits_mode(request.mode) if request.mode else settings.mode
    derived = depth_limits(chosen) if mode is LimitsMode.DEPTH else NO_LIMITS
    fixed = NO_LIMITS if request.mode == LimitsMode.OFF else settings.fixed
    budget = (
        request.budget_usd
        if request.budget_usd is not None
        else fixed.budget_usd or derived.budget_usd
    )
    turns = (
        request.max_turns if request.max_turns is not None else fixed.max_turns or derived.max_turns
    )
    wall = request.wall_min if request.wall_min is not None else fixed.wall_min or derived.wall_min
    return RunLimits(max(0.0, budget), max(0, turns), max(0.0, wall))


def _joined(parts: list[Message]) -> Message:
    if len(parts) == 1:
        return parts[0]
    return msg("limits.join", first=parts[0], rest=_joined(parts[1:]))


def limits_message(limits: RunLimits) -> Message:
    parts: list[Message] = []
    if limits.budget_usd > 0:
        parts.append(msg("limits.spend", cap=dollars(limits.budget_usd)))
    if limits.max_turns > 0:
        parts.append(counted("limits.turns", "turns", limits.max_turns))
    if limits.wall_min > 0:
        parts.append(msg("limits.wall", minutes=f"{limits.wall_min:g}"))
    if not parts:
        return msg("limits.none")
    return msg("limits.active", items=_joined(parts))


def limits_payload(limits: RunLimits) -> dict[str, float | int | None]:
    return {
        "budget_usd": limits.budget_usd or None,
        "max_turns": limits.max_turns or None,
        "wall_min": limits.wall_min or None,
    }
