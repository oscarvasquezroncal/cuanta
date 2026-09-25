from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from cuanta.application.engine_run import EngineLauncher, LaunchSpec
from cuanta.application.routing import RoutePlan
from cuanta.domain.agents import AgentDefinition, role_of
from cuanta.domain.capsules import capsule_id
from cuanta.domain.mandate import INLINE_EVIDENCE_LIMIT, MandateRequest, clip_evidence
from cuanta.domain.messages import Message, msg
from cuanta.domain.progress import Status, finished, note, started
from cuanta.domain.routing import Role
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


@dataclass(frozen=True, slots=True)
class CrossReport:
    steps: tuple[CrossStep, ...]
    ok: bool
    spent_usd: float
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
    ) -> None:
        self._launchers = launchers
        self._definitions = definitions
        self._capsules = capsules
        self._cwd = cwd
        self._budget = budget_usd

    def _handoff(self, text: str) -> str:
        if len(text) <= INLINE_EVIDENCE_LIMIT:
            return text
        digest, _, _ = self._capsules.put(text)
        return clip_evidence(text, capsule_id(digest))

    def run(self, request: MandateRequest, plan: RoutePlan, progress: ProgressSink) -> CrossReport:
        definitions = self._definitions()
        steps: list[CrossStep] = []
        spent = 0.0
        handoff = ""
        parent = ""
        for role in CROSS_ORDER:
            route = plan.route(role)
            if route is None or route.model is None:
                progress.publish(note(Status.SKIP, msg("cross.skipped", role=role.value)))
                continue
            remaining = self._budget - spent
            if self._budget > 0 and remaining <= 0:
                return CrossReport(tuple(steps), False, spent, msg("cross.budget"))
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
                started(key, msg("cross.step", role=role.value, engine=route.engine, model=model))
            )
            launch = launcher.launch(
                LaunchSpec(
                    kind=CROSS_KIND,
                    prompt=role_prompt(body, role, request, handoff),
                    cwd=self._cwd,
                    allowed_tools=tools_of(definition) if route.engine == "claude" else (),
                    model=model,
                    max_budget_usd=remaining if self._budget > 0 else 0.0,
                    scope=role.value,
                    parent_id=parent,
                ),
                lambda _: None,
            )
            parent = parent or launch.run.id
            outcome = launch.outcome
            ok = outcome is not None and outcome.ok
            text = outcome.result.text if outcome is not None and outcome.result else ""
            handoff = self._handoff(text)
            cost = launch.run.cost_usd
            spent += cost or 0.0
            steps.append(CrossStep(role, route.engine, model, launch.run.id, ok, cost, handoff))
            progress.publish(
                finished(
                    key, Status.OK if ok else Status.FAIL, msg("cross.done", run=launch.run.id)
                )
            )
            if not ok:
                return CrossReport(tuple(steps), False, spent, msg("cross.failed", role=role.value))
        return CrossReport(tuple(steps), bool(steps), spent)
