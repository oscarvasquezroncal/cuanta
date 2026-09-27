from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from cuanta.application.engine_run import EngineLauncher, LaunchSpec
from cuanta.application.estimate import Estimator
from cuanta.application.routing import RoutePlan
from cuanta.domain.agents import AgentDefinition, role_of
from cuanta.domain.capsules import capsule_id
from cuanta.domain.costs import CostSource, sum_costs
from cuanta.domain.depth import DEFAULT_DEPTH, MAX_TURNS
from cuanta.domain.estimates import RunEstimate
from cuanta.domain.guarantees import readonly_unavailable
from cuanta.domain.mandate import (
    INLINE_EVIDENCE_LIMIT,
    INVESTIGATION,
    MandateRequest,
    clip_evidence,
)
from cuanta.domain.messages import Message, msg
from cuanta.domain.progress import Status, finished, note, started
from cuanta.domain.role_budgets import allocate_budget
from cuanta.domain.routing import Role
from cuanta.domain.sandbox import SANDBOX_MODE, SandboxLaunch
from cuanta.ports.capsules import CapsuleStore
from cuanta.ports.progress import ProgressSink

CROSS_ORDER = (Role.ANALYST, Role.SENIOR, Role.TESTER, Role.DOCS)
CROSS_KIND = "cross"
DEFAULT_TOOLS = ("Read", "Grep", "Glob", "Edit", "Write")


@dataclass(frozen=True, slots=True)
class CrossStep:
    role: Role
    engine: str
    model: str
    run_id: str
    ok: bool
    cost_usd: float | None
    handoff: str
    cost_source: CostSource = "unknown"
    budget_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class CrossReport:
    steps: tuple[CrossStep, ...]
    ok: bool
    spent_usd: float | None
    stopped: Message | None = None


def request_block(request: MandateRequest) -> str:
    lines = [
        f"TYPE: {request.type}",
        f"WHAT: {request.what}",
        f"WHY / EVIDENCE: {request.why}",
        f"WHERE: {request.where or '-'}",
        f"CONSTRAINTS: {request.constraints or '-'}",
        f"TESTS: {request.tests or '-'}",
        f"OUT OF SCOPE: {request.out_of_scope}",
    ]
    return "\n".join(lines)


def role_prompt(body: str, role: Role, request: MandateRequest, handoff: str) -> str:
    previous = handoff or "none: you are the first role."
    return (
        f"{body.strip()}\n\n"
        f"You are the {role.value} of a pipeline where every role runs separately. "
        "Do only your role's part.\n\n"
        f"=== REQUEST ===\n{request_block(request)}\n\n"
        f"=== HANDOFF FROM THE PREVIOUS ROLE ===\n{previous}\n\n"
        "End your answer with one JSON object: your handoff for the next role."
    )


def definition_for(role: Role, definitions: Sequence[AgentDefinition]) -> AgentDefinition | None:
    return next((item for item in definitions if role_of(item.name) is role), None)


def tools_of(definition: AgentDefinition | None) -> tuple[str, ...]:
    if definition is None:
        return DEFAULT_TOOLS
    tools = definition.fields.get("tools")
    return tuple(str(tool) for tool in tools) if isinstance(tools, list) else DEFAULT_TOOLS


class CrossEnginePipeline:
    def __init__(
        self,
        launchers: Callable[[str], EngineLauncher | None],
        definitions: Callable[[], tuple[AgentDefinition, ...]],
        capsules: CapsuleStore,
        cwd: str,
        budget_usd: float,
        max_turns: int = 0,
        sandbox: SandboxLaunch | None = None,
        checkpoint: Callable[[], Message | None] | None = None,
        estimator: Estimator | None = None,
        depth: str = "",
        allocator: Callable[[RoutePlan, str, str, float], Mapping[Role, float]] | None = None,
        refresh_index: Callable[[], None] | None = None,
    ) -> None:
        self._refresh_index = refresh_index
        self._estimator = estimator
        self._allocator = allocator
        self._depth = depth
        self._sandbox = sandbox
        self._checkpoint = checkpoint
        self.completed: list[CrossStep] = []
        self.current = ""
        self._launchers = launchers
        self._definitions = definitions
        self._capsules = capsules
        self._cwd = cwd
        self._budget = budget_usd
        self._max_turns = max_turns if max_turns > 0 else MAX_TURNS[DEFAULT_DEPTH]

    def _estimate(self, plan: RoutePlan, task_type: str) -> RunEstimate | None:
        if self._estimator is None:
            return None
        return self._estimator(plan, task_type, self._depth)

    def _started(self, run_id: str) -> None:
        self.current = run_id

    def _isolated(self, spec: LaunchSpec, engine: str) -> LaunchSpec:
        sandbox = self._sandbox
        if sandbox is None:
            return spec
        denied = sandbox.denied if engine == "claude" else ()
        return replace(
            spec,
            prompt=f"{spec.prompt}\n\n{sandbox.note}" if sandbox.note else spec.prompt,
            temporary_copy=True,
            mode=SANDBOX_MODE,
            env=sandbox.env,
            disallowed_tools=(*spec.disallowed_tools, *denied),
        )

    def _handoff(self, text: str) -> str:
        if len(text) <= INLINE_EVIDENCE_LIMIT:
            return text
        digest, _, _ = self._capsules.put(text)
        return clip_evidence(text, capsule_id(digest))

    def run(self, request: MandateRequest, plan: RoutePlan, progress: ProgressSink) -> CrossReport:
        if self._refresh_index is not None:
            self._refresh_index()
        definitions = self._definitions()
        steps: list[CrossStep] = []
        self.completed = steps
        spent: float | None = 0.0
        handoff = ""
        parent = ""
        active = {
            role: None
            for role in CROSS_ORDER
            if (route := plan.route(role)) is not None and route.model is not None
        }
        shares = (
            self._allocator(plan, request.type, self._depth, self._budget)
            if self._allocator is not None
            else allocate_budget(active, self._budget)
        )
        for role in CROSS_ORDER:
            route = plan.route(role)
            if route is None or route.model is None:
                progress.publish(note(Status.SKIP, msg("cross.skipped", role=role.value)))
                continue
            if self._budget > 0 and spent is None:
                return CrossReport(tuple(steps), False, None, msg("cross.cost_unknown"))
            remaining = self._budget - spent if spent is not None else 0.0
            if self._budget > 0 and remaining <= 0:
                return CrossReport(tuple(steps), False, spent, msg("cross.budget"))
            future = CROSS_ORDER[CROSS_ORDER.index(role) + 1 :]
            reserved = sum(shares.get(later, 0.0) for later in future)
            role_cap = max(0.0, remaining - reserved) if self._budget > 0 else 0.0
            if self._budget > 0 and role_cap <= 1e-9:
                return CrossReport(
                    tuple(steps), False, spent, msg("cross.role_budget", role=role.value)
                )
            read_only = role is Role.ANALYST or request.type == INVESTIGATION
            refusal = readonly_unavailable(route.engine) if read_only else None
            if refusal is not None:
                return CrossReport(tuple(steps), False, spent, refusal)
            launcher = self._launchers(route.engine)
            if launcher is None:
                return CrossReport(
                    tuple(steps), False, spent, msg("cross.no_engine", engine=route.engine)
                )
            definition = definition_for(role, definitions)
            body = definition.prompt if definition is not None else ""
            model = route.model.resolved or route.model.id
            key = f"cross-{role.value}"
            progress.publish(
                started(
                    key,
                    msg("cross.step", role=role.value, engine=route.engine, model=model),
                )
            )
            if role_cap > 0:
                progress.publish(
                    note(Status.INFO, msg("cross.share", role=role.value, cap=f"{role_cap:.4f}"))
                )
            spec = LaunchSpec(
                kind=CROSS_KIND,
                prompt=role_prompt(body, role, request, handoff),
                cwd=self._cwd,
                allowed_tools=tools_of(definition) if route.engine == "claude" else (),
                model=model,
                max_budget_usd=role_cap,
                max_turns=self._max_turns if route.engine == "claude" else 0,
                scope=role.value,
                parent_id=parent,
                read_only=read_only,
                task_type=request.type,
                depth=self._depth,
                estimate=None if parent else self._estimate(plan, request.type),
                pipeline_budget_usd=self._budget if not parent else 0.0,
            )
            launch = launcher.launch(
                self._isolated(spec, route.engine), lambda _: None, self._started
            )
            parent = parent or launch.run.id
            outcome = launch.outcome
            ok = outcome is not None and outcome.ok
            text = outcome.result.text if outcome is not None and outcome.result else ""
            handoff = self._handoff(text)
            cost = launch.run.cost_usd
            spent = sum_costs((spent, cost))
            steps.append(
                CrossStep(
                    role,
                    route.engine,
                    model,
                    launch.run.id,
                    ok,
                    cost,
                    handoff,
                    launch.run.cost_source,
                    role_cap,
                )
            )
            progress.publish(
                finished(
                    key, Status.OK if ok else Status.FAIL, msg("cross.done", run=launch.run.id)
                )
            )
            stop = self._checkpoint() if self._checkpoint is not None else None
            if stop is not None:
                return CrossReport(tuple(steps), False, spent, stop)
            if not ok:
                if outcome.result is not None and outcome.result.subtype == "error_cost_unknown":
                    return CrossReport(tuple(steps), False, spent, msg("engine.cost_unknown"))
                if (
                    outcome is not None
                    and outcome.result is not None
                    and "budget" in outcome.result.subtype
                ):
                    return CrossReport(tuple(steps), False, spent, msg("cross.budget"))
                return CrossReport(tuple(steps), False, spent, msg("cross.failed", role=role.value))
        return CrossReport(tuple(steps), bool(steps), spent)
