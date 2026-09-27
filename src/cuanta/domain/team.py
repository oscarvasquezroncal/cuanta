from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from cuanta.domain.guarantees import Guarantee, cap_warning, engine_guarantees
from cuanta.domain.ledger import Run
from cuanta.domain.messages import Message, msg
from cuanta.domain.routing import Mix, Role, RoleRoute

MIX_TITLES: Mapping[Mix, str] = {
    Mix.CLAUDE_ONLY: "mix.claude_only",
    Mix.CLAUDE_PLANS: "mix.claude_plans",
    Mix.CODEX_PLANS: "mix.codex_plans",
}
BUILD_ROLES = frozenset({Role.SENIOR, Role.TESTER})
ACCEPTED = "accepted"


@dataclass(frozen=True, slots=True)
class MixAttempt:
    mix: Mix
    task_type: str
    cost_usd: float | None
    accepted: bool


@dataclass(frozen=True, slots=True)
class MixAdvice:
    mix: Mix
    attempts: int
    accepted: int
    per_accepted: float


@dataclass(frozen=True, slots=True)
class RoleCard:
    role: Role
    engine: str
    model: str
    share: float
    guarantees: tuple[Guarantee, ...]
    context: Message
    warnings: tuple[Message, ...]
    reason: Message

    @property
    def title(self) -> Message:
        if self.share > 0:
            return msg(
                "team.role",
                role=self.role.value,
                engine=self.engine,
                model=self.model,
                share=f"{self.share:.4f}",
            )
        return msg("team.role_unshared", role=self.role.value, engine=self.engine, model=self.model)


def mix_title(mix: Mix) -> Message:
    return msg(MIX_TITLES[mix])


def mix_of(engines: Mapping[str, str]) -> Mix | None:
    pair = (engines.get(Role.ANALYST.value, ""), engines.get(Role.SENIOR.value, ""))
    return {
        ("claude", "claude"): Mix.CLAUDE_ONLY,
        ("claude", "codex"): Mix.CLAUDE_PLANS,
        ("codex", "claude"): Mix.CODEX_PLANS,
    }.get(pair)


def mix_attempts(runs: Sequence[Run]) -> tuple[MixAttempt, ...]:
    roots = {run.id: run for run in runs if run.kind == "cross" and not run.parent_id}
    engines: dict[str, dict[str, str]] = {}
    costs: dict[str, list[float | None]] = {}
    for run in runs:
        if run.kind != "cross":
            continue
        root = run.parent_id or run.id
        if root not in roots:
            continue
        engines.setdefault(root, {}).setdefault(run.scope, run.engine)
        costs.setdefault(root, []).append(run.cost_usd)
    found: list[MixAttempt] = []
    for root, run in roots.items():
        mix = mix_of(engines.get(root, {}))
        if mix is None or not run.outcome:
            continue
        spent = costs.get(root, [])
        total = None if any(value is None for value in spent) else sum(v or 0.0 for v in spent)
        found.append(MixAttempt(mix, run.task_type, total, run.outcome == ACCEPTED))
    return tuple(found)


def recommend_mix(attempts: Sequence[MixAttempt], task_type: str) -> MixAdvice | None:
    best: MixAdvice | None = None
    for mix in Mix:
        group = [item for item in attempts if item.mix is mix and item.task_type == task_type]
        accepted = sum(item.accepted for item in group)
        if not accepted or any(item.cost_usd is None for item in group):
            continue
        per = sum(item.cost_usd or 0.0 for item in group) / accepted
        if best is None or per < best.per_accepted:
            best = MixAdvice(mix, len(group), accepted, per)
    return best


def advice_message(advice: MixAdvice, task_type: str) -> Message:
    return msg(
        "team.recommended",
        type=task_type,
        attempts=advice.attempts,
        mix=mix_title(advice.mix),
        cost=f"{advice.per_accepted:.4f}",
    )


def team_cards(
    routes: Sequence[RoleRoute],
    shares: Mapping[Role, float],
    budget_usd: float,
    index_tools: Callable[[str], bool],
    build_blocked: frozenset[str] = frozenset(),
) -> tuple[RoleCard, ...]:
    cards: list[RoleCard] = []
    for route in routes:
        if route.model is None or route.role is Role.ORCHESTRATOR:
            continue
        engine = route.engine
        warnings: list[Message] = []
        if engine in build_blocked and route.role in BUILD_ROLES:
            warnings.append(msg("guarantee.codex_builds"))
        warning = cap_warning(engine, budget_usd)
        if warning is not None:
            warnings.append(warning)
        cards.append(
            RoleCard(
                route.role,
                engine,
                route.model.resolved or route.model.id,
                shares.get(route.role, 0.0),
                engine_guarantees(engine),
                msg("team.context_index" if index_tools(engine) else "team.context_text"),
                tuple(warnings),
                route.reason,
            )
        )
    return tuple(cards)
