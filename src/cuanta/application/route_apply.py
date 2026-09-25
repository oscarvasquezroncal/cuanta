from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from cuanta.application.routing import RouteAdvisor, RouteInputs, RoutePlan, with_overrides
from cuanta.domain.agents import AgentDefinition, AgentRoute, AgentsPlan, build_agents, parse_agent
from cuanta.domain.audit import MAIN_AGENT, AuditRow, audit
from cuanta.domain.depth import parse_depth, profile
from cuanta.domain.instinct import Choice
from cuanta.domain.ledger import LedgerEvent, RouteAudit
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.routing import (
    Role,
    RouteMode,
    RoutingPolicy,
    depth_capped,
    roles_that_run,
    single_model,
)
from cuanta.domain.spectrum import FALLBACK_KINDS, SUBAGENT_TOOLS, USAGE_KINDS, resolve_agents
from cuanta.domain.stable import content_name
from cuanta.domain.testing import GatewayStatus
from cuanta.ports.ledger import EventQuery, Ledger
from cuanta.ports.workspace import Workspace

AGENTS_DIR = ".claude/agents"
AGENTS_OUT = ".cuanta/tmp"
SUBAGENT_ENV = ("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "CLAUDE_CODE_SUBAGENT_MODEL")
CLAUDE = "claude"


@dataclass(frozen=True, slots=True)
class RouteOptions:
    mode: str = ""
    preset: str = ""
    role_models: tuple[tuple[str, str], ...] = ()
    keep_env_model: bool = True
    clarity: float | None = None
    depth: str = ""
    scope: Choice | None = None
    risk: float | None = None


@dataclass(frozen=True, slots=True)
class Applied:
    plan: RoutePlan
    engine: str
    agents: AgentsPlan | None = None
    agents_file: str = ""
    orchestrator: str = ""
    single: str = ""
    env_override: str = ""
    unset: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return self.plan.policy.mode is not RouteMode.OFF

    def planned(self) -> dict[str, tuple[str, str]]:
        found: dict[str, tuple[str, str]] = {}
        if self.agents is not None:
            for name, spec in self.agents.agents.items():
                role = self.agents.roles.get(name)
                model = spec.get("model")
                if role is not None and isinstance(model, str):
                    found[name] = (role.value, model)
        if self.orchestrator:
            found[MAIN_AGENT] = (Role.ORCHESTRATOR.value, self.orchestrator)
        if self.single:
            found = {MAIN_AGENT: ("single", self.single)}
        return found


def observed_models(events: list[LedgerEvent]) -> dict[str, tuple[str, ...]]:
    resolved = resolve_agents(events)
    usage = [event for event in resolved if event.kind in USAGE_KINDS and event.model]
    if not usage:
        usage = [event for event in resolved if event.kind in FALLBACK_KINDS and event.model]
    seen: dict[str, list[str]] = {}
    for event in usage:
        seen.setdefault(event.agent or MAIN_AGENT, []).append(event.model)
    return {agent: tuple(dict.fromkeys(models)) for agent, models in seen.items()}


def invocation_models(events: list[LedgerEvent]) -> dict[str, str]:
    found: dict[str, str] = {}
    for event in events:
        if event.tool_name not in SUBAGENT_TOOLS or not event.raw:
            continue
        try:
            data = json.loads(event.raw)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        params = data.get("parameters", data)
        if not isinstance(params, dict):
            continue
        agent, model = params.get("subagent_type"), params.get("model")
        if isinstance(agent, str) and isinstance(model, str) and model:
            found[agent] = model
    return found


class MandateRouting:
    def __init__(
        self,
        advisor: RouteAdvisor,
        policy: Callable[[], RoutingPolicy],
        workspace: Workspace,
        ledger: Ledger,
        environ: Mapping[str, str],
        settings_env: Callable[[], Mapping[str, str]],
        blast_radius: Callable[[str], int],
        clock_iso: Callable[[], str],
    ) -> None:
        self._advisor = advisor
        self._policy = policy
        self._workspace = workspace
        self._ledger = ledger
        self._environ = environ
        self._settings_env = settings_env
        self._blast_radius = blast_radius
        self._clock_iso = clock_iso

    def definitions(self) -> tuple[AgentDefinition, ...]:
        found: list[AgentDefinition] = []
        for path in sorted(self._workspace.files_under(AGENTS_DIR)):
            if not path.endswith(".md"):
                continue
            text = self._workspace.read_text(path)
            parsed = parse_agent(text, path) if text is not None else None
            if parsed is not None:
                found.append(parsed)
        return tuple(found)

    def env_override(self) -> str:
        settings = self._settings_env()
        return next(
            (name for name in SUBAGENT_ENV if self._environ.get(name) or settings.get(name)), ""
        )

    def inputs(
        self,
        request: MandateRequest,
        clarity: float | None = None,
        options: RouteOptions | None = None,
    ) -> RouteInputs:
        chosen = options or RouteOptions(clarity=clarity)
        latest = self._ledger.test_runs(limit=1)
        status = latest[0].status if latest else "unknown"
        return RouteInputs(
            task_type=request.type,
            what=request.what,
            where=request.where,
            tests=status,
            blast_radius=self._blast_radius(f"{request.what} {request.where}"),
            persistent_failure=status == GatewayStatus.PERSISTENT.value,
            clarity=clarity,
            roles=roles_that_run(request.type),
            scope=chosen.scope,
            risk=chosen.risk,
        )

    def _write(self, agents: AgentsPlan) -> str:
        text = agents.to_json()
        relative = content_name(AGENTS_OUT, "agents", text)
        self._workspace.write_text(relative, text)
        return str(self._workspace.root / relative)

    def apply(
        self,
        request: MandateRequest,
        options: RouteOptions,
        engine: str,
        graph_available: bool = True,
    ) -> Applied:
        policy = with_overrides(
            self._policy(), options.mode, options.preset, dict(options.role_models)
        )
        policy = replace(policy, engines=(engine,))
        if options.depth:
            policy = depth_capped(
                policy, profile(parse_depth(options.depth), request.type).tier_cap
            )
        plan = self._advisor.plan(policy, self.inputs(request, options.clarity, options))
        override = self.env_override()
        unset = (
            tuple(name for name in SUBAGENT_ENV if self._environ.get(name))
            if override and not options.keep_env_model
            else ()
        )
        if policy.mode is RouteMode.OFF:
            return Applied(plan, engine, env_override=override)
        if engine != CLAUDE:
            chosen = single_model(plan.routes)
            single = chosen.model.id if chosen is not None and chosen.model is not None else ""
            return Applied(plan, engine, single=single, env_override=override, unset=unset)
        effort = profile(parse_depth(options.depth), request.type).effort if options.depth else ""
        routes: dict[Role, AgentRoute] = {
            route.role: AgentRoute(route.model.resolved or route.model.id, effort)
            for route in plan.routes
            if route.model is not None and route.role is not Role.ORCHESTRATOR
        }
        agents = build_agents(self.definitions(), routes, graph_available)
        orchestrator = plan.route(Role.ORCHESTRATOR) or plan.route(Role.ANALYST)
        return Applied(
            plan,
            engine,
            agents=agents if agents.agents else None,
            agents_file=self._write(agents) if agents.agents else "",
            orchestrator=(
                orchestrator.model.resolved or orchestrator.model.id
                if orchestrator is not None and orchestrator.model is not None
                else ""
            ),
            env_override=override,
            unset=unset,
        )

    def record(self, run_id: str, task_type: str, applied: Applied) -> None:
        if applied.active:
            self._advisor.record(run_id, task_type, applied.plan)

    def close(self, run_id: str, tests: str, cost_usd: float | None) -> None:
        self._advisor.close(run_id, tests, cost_usd, 0)

    def audit(
        self, run_id: str, applied: Applied, delegated: tuple[str, ...] = ()
    ) -> tuple[AuditRow, ...]:
        planned = applied.planned()
        if not applied.active or not planned:
            return ()
        events = list(self._ledger.events(EventQuery(run_id=run_id)))
        override = applied.env_override if not applied.unset else ""
        rows = audit(
            planned,
            observed_models(events),
            override,
            invocation_models(events),
            frozenset(delegated),
        )
        now = self._clock_iso()
        self._ledger.add_route_audits(
            [
                RouteAudit(
                    run_id=run_id,
                    agent=row.agent,
                    role=row.role,
                    planned=row.planned,
                    actual=", ".join(row.actual),
                    status=row.status.value,
                    cause=row.cause_text,
                    created_at=now,
                )
                for row in rows
            ]
        )
        return rows
