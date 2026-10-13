from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from cuanta.domain.advisor import family_of
from cuanta.domain.claude_variants import resolve_variant
from cuanta.domain.mandate import MandateType
from cuanta.domain.messages import Message, msg
from cuanta.domain.pricing import PriceTable
from cuanta.domain.routing import Role

COMPLEX = "complex"
SINGLE_MODEL = "sonnet"
ADVISOR_MODEL = "opus"
SINGLE_EFFORTS: Mapping[str, str] = {
    MandateType.FEATURE.value: "medium",
    MandateType.BUG.value: "high",
}


@dataclass(frozen=True, slots=True)
class RoleDefault:
    model: str
    effort: str
    advisor: str = ""


TEAM_DEFAULTS: Mapping[Role, RoleDefault] = {
    Role.ANALYST: RoleDefault("haiku", "medium"),
    Role.SENIOR: RoleDefault("sonnet", "high", ADVISOR_MODEL),
    Role.TESTER: RoleDefault("haiku", "medium"),
    Role.DOCS: RoleDefault("haiku", "low"),
}


@dataclass(frozen=True, slots=True)
class LaunchScope:
    option: str
    probability: float
    backend: str
    fallback: str = ""
    plan_files: int = 0
    anchors: int = 0
    history: int = 0
    configured: str = ""

    @property
    def team(self) -> bool:
        return self.option == COMPLEX


def scope_context(
    task_type: str, what: str, where: str, plan_files: int, anchors: int, history: int
) -> dict[str, object]:
    return {
        "kind": "scope",
        "type": task_type,
        "what": what,
        "where": where,
        "signatures": 0,
        "plan_files": plan_files,
        "anchors": anchors,
        "history": history,
    }


def scope_message(scope: LaunchScope) -> Message:
    shape = msg("instinct.launch_team" if scope.team else "instinct.launch_single")
    values = {
        "scope": msg(f"scope.{scope.option}"),
        "probability": f"{scope.probability:.2f}",
        "shape": shape,
        "files": scope.plan_files,
        "anchors": scope.anchors,
        "history": scope.history,
    }
    if scope.fallback:
        return msg(
            "instinct.launch_fallback",
            backend=scope.backend,
            configured=scope.configured,
            **values,
        )
    return msg("instinct.launch", backend=scope.backend, **values)


def scope_payload(scope: LaunchScope) -> dict[str, object]:
    return {
        "scope": scope.option,
        "probability": round(scope.probability, 3),
        "backend": scope.backend,
        "fallback": scope.fallback or None,
        "plan_files": scope.plan_files,
        "anchors": scope.anchors,
        "history": scope.history,
        "shape": "team" if scope.team else "single",
    }


def single_effort(task_type: str) -> str:
    return SINGLE_EFFORTS.get(task_type, "medium")


DEFAULT_PRICED = "claude-sonnet-5-5"
EXPENSIVE_EFFORTS = frozenset({"xhigh", "max"})


def _factor(numerator: float | None, denominator: float | None) -> str:
    if not numerator or not denominator:
        return "?"
    return f"{numerator / denominator:.1f}"


def opt_ins(
    model: str, pure: bool, variant: str, team: bool, prices: PriceTable
) -> tuple[Message, ...]:
    found: list[Message] = []
    base = prices.lookup(DEFAULT_PRICED)
    main = prices.lookup(model) if model else None
    ratio = _factor(main.output if main else None, base.output if base else None)
    if family_of(model) in {"opus", "fable"}:
        found.append(msg("optin.model", model=model, factor=ratio))
    if pure:
        found.append(msg("optin.pure", model=model or "-", factor=ratio))
    choice = resolve_variant(variant, model) if variant else None
    if choice is not None and choice.fast:
        fast = main.fast if main is not None else None
        found.append(
            msg(
                "optin.fast",
                model=model,
                factor=_factor(fast.output if fast else None, main.output if main else None),
            )
        )
    if choice is not None and choice.ultracode and team:
        found.append(msg("optin.ultracode_team"))
    elif choice is not None and choice.effort in EXPENSIVE_EFFORTS:
        found.append(msg("optin.effort", effort=choice.effort))
    return tuple(found)
