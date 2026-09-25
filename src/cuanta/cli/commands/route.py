from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.application.routing import RoutePlan
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Block, Document
    from cuanta.domain.routing import CostRange

ROUTE_MODES = ("auto", "fixed", "off")
PRESETS = ("save", "balanced", "best")


def parse_role_models(values: list[str]) -> dict[str, str]:
    from cuanta.domain.errors import DomainFailure
    from cuanta.domain.routing import ROLES

    names = {role.value for role in ROLES}
    pinned: dict[str, str] = {}
    for value in values:
        role, _, model = value.partition("=")
        role = role.strip().lower()
        if role not in names or not model.strip():
            raise DomainFailure(
                f"bad --role-model {value}", f"use role=model with a role in {', '.join(names)}"
            )
        pinned[role] = model.strip()
    return pinned


def check_choice(value: str, allowed: tuple[str, ...], flag: str) -> None:
    from cuanta.domain.errors import DomainFailure

    if value and value not in allowed:
        raise DomainFailure(f"unknown {flag} {value}", f"use one of {', '.join(allowed)}")


def route_command(
    ctx: typer.Context,
    task_type: Annotated[
        str, typer.Option("--type", help="feature, bug, refactor, investigation.")
    ] = "bug",
    what: Annotated[str, typer.Option("--what", help="The change, concretely.")] = "",
    where: Annotated[str, typer.Option("--where", help="File, module or area.")] = "",
    route: Annotated[str, typer.Option("--route", help="auto, fixed or off.")] = "",
    preset: Annotated[str, typer.Option("--preset", help="save, balanced or best.")] = "",
    role_model: Annotated[
        list[str] | None, typer.Option("--role-model", help="Pin a role: senior=opus.")
    ] = None,
    persistent: Annotated[
        bool, typer.Option("--persistent-failure", help="Plan as if the last fix failed twice.")
    ] = False,
    _dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Only print the plan (the default).")
    ] = True,
) -> None:
    execute(
        ctx,
        lambda session: _route(
            session, task_type, what, where, route, preset, role_model or [], persistent
        ),
    )


def plan_for(
    container: "Container",
    task_type: str,
    what: str,
    where: str,
    route: str = "",
    preset: str = "",
    role_models: dict[str, str] | None = None,
    persistent: bool = False,
) -> "tuple[RoutePlan, CostRange]":
    check_choice(route, ROUTE_MODES, "--route")
    check_choice(preset, PRESETS, "--preset")
    return container.plan_route(task_type, what, where, route, preset, role_models, persistent)


def plan_blocks(plan: "RoutePlan", estimate: "CostRange", budget: float) -> "list[Block]":
    from cuanta.cli.document import Column, Line, Table
    from cuanta.cli.fmt import usd
    from cuanta.domain.messages import english

    policy = plan.policy
    header = (
        f"routing {policy.mode.value} · preset {policy.preset.value} · "
        f"engines {', '.join(policy.engines)} · instinct {plan.backend}"
    )
    blocks: list[Block] = [Line(header)]
    if plan.scope is not None:
        risk = f" · risk {plan.risk:.1f}/2" if plan.risk is not None else ""
        blocks.append(Line(f"scope {plan.scope.option} ({plan.scope.probability:.0%}){risk}"))
    rows = tuple(
        (
            route.role.value,
            route.requested.value,
            route.tier.value if route.tier else "-",
            route.engine or "-",
            (route.model.id if route.model else "engine default"),
            english(route.reason),
            f"{route.confidence:.0%}" if route.confidence is not None else "-",
        )
        for route in plan.routes
    )
    blocks.append(
        Table(
            "team",
            (
                Column("role"),
                Column("asked"),
                Column("tier"),
                Column("engine"),
                Column("model"),
                Column("why"),
                Column("confidence", numeric=True),
            ),
            rows,
        )
    )
    if estimate.samples:
        text = (
            f"estimate {usd(estimate.p50)}–{usd(estimate.p90)} "
            f"(p50–p90 of {estimate.samples} similar mandates)"
        )
        over = budget > 0 and estimate.p90 is not None and estimate.p90 > budget
        blocks.append(Line(text + (f" · above the ${budget:.2f} budget" if over else "")))
    else:
        blocks.append(Line("estimate: no finished mandates to compare with yet"))
    return blocks


def plan_payload(plan: "RoutePlan", estimate: "CostRange") -> dict[str, object]:
    from cuanta.domain.messages import english

    return {
        "mode": plan.policy.mode.value,
        "preset": plan.policy.preset.value,
        "engines": list(plan.policy.engines),
        "backend": plan.backend,
        "scope": plan.scope.option if plan.scope else None,
        "scope_confidence": plan.scope.probability if plan.scope else None,
        "risk": plan.risk,
        "routes": [
            {
                "role": route.role.value,
                "requested": route.requested.value,
                "tier": route.tier.value if route.tier else None,
                "engine": route.engine or None,
                "model": route.model.id if route.model else None,
                "resolved": route.model.resolved if route.model else None,
                "reason": english(route.reason),
                "confidence": route.confidence,
            }
            for route in plan.routes
        ],
        "estimate": {"p50": estimate.p50, "p90": estimate.p90, "samples": estimate.samples},
    }


def _route(
    session: Session,
    task_type: str,
    what: str,
    where: str,
    route: str,
    preset: str,
    role_models: list[str],
    persistent: bool,
) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document

    container = Container.for_project(session.project)
    try:
        plan, estimate = plan_for(
            container,
            task_type,
            what,
            where,
            route,
            preset,
            parse_role_models(role_models),
            persistent,
        )
    finally:
        container.close()
    blocks = plan_blocks(plan, estimate, container.config.budget_usd)
    return Document(blocks=tuple(blocks), payload=plan_payload(plan, estimate))
