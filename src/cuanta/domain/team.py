from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from cuanta.domain.guarantees import Guarantee, cap_warning, engine_guarantees
from cuanta.domain.ledger import Run
from cuanta.domain.messages import Message, msg
from cuanta.domain.routing import Provider, Role, RoleRoute, parse_provider

PROVIDER_TITLES: Mapping[Provider, str] = {
    Provider.CLAUDE: "provider.claude",
    Provider.CODEX: "provider.codex",
}
BUILD_ROLES = frozenset({Role.SENIOR, Role.TESTER})
ACCEPTED = "accepted"
PIPELINE = "pipeline"
CROSS = "cross"
MANDATE = "mandate"


@dataclass(frozen=True, slots=True)
class ProviderAttempt:
    provider: Provider
    task_type: str
    cost_usd: float | None
    accepted: bool


@dataclass(frozen=True, slots=True)
class ProviderAdvice:
    provider: Provider
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


def provider_title(provider: Provider) -> Message:
    return msg(PROVIDER_TITLES[provider])


def runs_per_role(engine: str, pipeline: bool) -> bool:
    return pipeline and parse_provider(engine) is Provider.CODEX


def _team_provider(engines: set[str]) -> Provider | None:
    return parse_provider(next(iter(engines))) if len(engines) == 1 else None


def provider_attempts(
    runs: Sequence[Run], shapes: Mapping[str, str]
) -> tuple[ProviderAttempt, ...]:
    roots = {run.id: run for run in runs if run.kind == CROSS and not run.parent_id}
    engines: dict[str, set[str]] = {}
    costs: dict[str, list[float | None]] = {}
    for run in runs:
        if run.kind != CROSS:
            continue
        root = run.parent_id or run.id
        if root not in roots:
            continue
        engines.setdefault(root, set()).add(run.engine)
        costs.setdefault(root, []).append(run.cost_usd)
    found: list[ProviderAttempt] = []
    for root, run in roots.items():
        provider = _team_provider(engines.get(root, set()))
        if provider is None or not run.outcome:
            continue
        spent = costs.get(root, [])
        total = None if any(value is None for value in spent) else sum(v or 0.0 for v in spent)
        found.append(ProviderAttempt(provider, run.task_type, total, run.outcome == ACCEPTED))
    for run in runs:
        provider = parse_provider(run.engine)
        if run.kind != MANDATE or provider is None or not run.outcome:
            continue
        if shapes.get(run.id) != PIPELINE or runs_per_role(run.engine, True):
            continue
        found.append(
            ProviderAttempt(provider, run.task_type, run.cost_usd, run.outcome == ACCEPTED)
        )
    return tuple(found)


def recommend_provider(
    attempts: Sequence[ProviderAttempt], task_type: str
) -> ProviderAdvice | None:
    best: ProviderAdvice | None = None
    for provider in Provider:
        group = [
            item for item in attempts if item.provider is provider and item.task_type == task_type
        ]
        accepted = sum(item.accepted for item in group)
        if not accepted or any(item.cost_usd is None for item in group):
            continue
        per = sum(item.cost_usd or 0.0 for item in group) / accepted
        if best is None or per < best.per_accepted:
            best = ProviderAdvice(provider, len(group), accepted, per)
    return best


def advice_message(advice: ProviderAdvice, task_type: str) -> Message:
    return msg(
        "team.recommended",
        type=task_type,
        attempts=advice.attempts,
        provider=provider_title(advice.provider),
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
