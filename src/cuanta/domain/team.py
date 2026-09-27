from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from cuanta.domain.guarantees import Guarantee, cap_warning, engine_guarantees
from cuanta.domain.messages import Message, msg
from cuanta.domain.routing import Mix, Role, RoleRoute

MIX_TITLES: Mapping[Mix, str] = {
    Mix.CLAUDE_ONLY: "mix.claude_only",
    Mix.CLAUDE_PLANS: "mix.claude_plans",
    Mix.CODEX_PLANS: "mix.codex_plans",
}
BUILD_ROLES = frozenset({Role.SENIOR, Role.TESTER})


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
