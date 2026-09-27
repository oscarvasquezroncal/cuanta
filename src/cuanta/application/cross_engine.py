from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Protocol

from cuanta.application.engine_run import EngineLauncher, Launch, LaunchSpec
from cuanta.application.estimate import Estimator
from cuanta.application.routing import RoutePlan
from cuanta.domain.agents import AgentDefinition, role_of
from cuanta.domain.capsules import capsule_id
from cuanta.domain.change_plan import ChangePlan, guarded, plan_metrics
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
from cuanta.domain.pack import ContextPack
from cuanta.domain.progress import Status, finished, note, started
from cuanta.domain.role_budgets import (
    DEFAULT_MARGIN,
    OPTIONAL_ROLES,
    allocate_budget,
    floor_fraction,
    floors_usd,
    soft_cap,
)
from cuanta.domain.role_handoff import (
    HandoffChain,
    HandoffStatus,
    RoleHandoff,
    VerifyResult,
    build_handoff,
    estimate_tokens,
    handoff_budget,
    merge_chain,
    refresh_chain,
    refresh_facts,
    render_chain,
    schema_instruction,
)
from cuanta.domain.routing import Role
from cuanta.domain.sandbox import SANDBOX_MODE, SandboxLaunch, docs_only
from cuanta.ports.capsules import CapsuleStore
from cuanta.ports.progress import ProgressSink

CROSS_ORDER = (Role.ANALYST, Role.SENIOR, Role.TESTER, Role.DOCS)
CROSS_KIND = "cross"
DEFAULT_TOOLS = ("Read", "Grep", "Glob", "Edit", "Write")
WRITING_ROLES = frozenset({Role.SENIOR, Role.TESTER, Role.DOCS})
REPAIR_MINIMUM_USD = 0.01
BUDGET_EPSILON = 1e-9
SHOWN_PATHS = 5

ReadRanges = tuple[tuple[str, int, int], ...]
LinesOf = Callable[[str], Sequence[str] | None]


class CompletionState(StrEnum):
    COMPLETE = "complete"
    COMPLETE_SKIPPED = "complete_skipped"
    PARTIAL = "partial"
    FAILED = "failed"


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
    native_cap_usd: float = 0.0
    overrun_usd: float = 0.0
    salvaged: bool = False
    repair: bool = False
    handoff_tokens: int = 0
    covered_files: tuple[str, ...] = ()
    read_files: tuple[str, ...] = ()
    reread_files: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    unreadable_files: tuple[str, ...] = ()
    index_tools: bool = False


@dataclass(frozen=True, slots=True)
class VerificationRound:
    role: Role
    attempt: int
    results: tuple[VerifyResult, ...]
    outputs: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)

    @property
    def seconds(self) -> float:
        return round(sum(result.seconds for result in self.results), 2)


@dataclass(frozen=True, slots=True)
class SkippedRole:
    role: Role
    reason: Message


@dataclass(frozen=True, slots=True)
class CrossReport:
    steps: tuple[CrossStep, ...]
    ok: bool
    spent_usd: float | None
    stopped: Message | None = None
    change_plan: ChangePlan | None = None
    changed_files: tuple[str, ...] = ()
    state: CompletionState = CompletionState.FAILED
    skipped: tuple[SkippedRole, ...] = ()
    verifications: tuple[VerificationRound, ...] = ()
    handoffs: tuple[RoleHandoff, ...] = ()
    guard_role: str = ""

    @property
    def verification_outputs(self) -> tuple[str, ...]:
        roles = {path for step in self.steps for path in step.changed_files}
        found = {path for item in self.verifications for path in item.outputs}
        return tuple(sorted(found - roles))


class NewFileGuard(Protocol):
    def prepare(self, plan: ChangePlan | None) -> tuple[str, ...]: ...

    def settle(self, created: Sequence[str]) -> tuple[str, ...]: ...

    def unreadable(self) -> tuple[str, ...]: ...


def cross_metrics(report: CrossReport) -> dict[str, object]:
    base: dict[str, object] = {
        "completion": report.state.value,
        "skipped_roles": [item.role.value for item in report.skipped],
        "verification_rounds": [
            {
                "role": item.role.value,
                "attempt": item.attempt,
                "passed": item.passed,
                "seconds": item.seconds,
                "commands": [
                    {
                        "command": result.command,
                        "exit_code": result.exit_code,
                        "seconds": result.seconds,
                        "timed_out": result.timed_out,
                        "errors": list(result.errors),
                    }
                    for result in item.results
                ],
            }
            for item in report.verifications
        ],
        "handoff_tokens": {step.role.value: step.handoff_tokens for step in report.steps},
        "rereads": {
            step.role.value: {
                "covered": len(step.covered_files),
                "read": len(step.read_files),
                "reread": len(step.reread_files),
            }
            for step in report.steps
        },
    }
    if report.guard_role:
        base["guard_role"] = report.guard_role
    if report.change_plan is None:
        return base
    engine = "claude" if all(step.engine == "claude" for step in report.steps) else "cross"
    return {**base, **plan_metrics(report.change_plan, report.changed_files, engine)}


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


def guidance(role: Role, index_tools: bool, verify: Sequence[str], partial: bool = False) -> str:
    opener = " with Cuanta page (path and lines as start:end)" if index_tools else ""
    finder = " Use Cuanta find before Glob or Grep." if index_tools else ""
    lines = [
        "READ ONLY WHAT IS NEEDED: the chain lists anchored facts as path:start-end. "
        f"Open those ranges{opener} instead of whole files, and do not re-read files the chain "
        f"covers unless an anchor is marked stale.{finder}"
    ]
    if partial:
        lines.append(
            "An earlier role's handoff is partial: it stopped early or returned no structured "
            "handoff. That does not block you: treat the request, the anchored facts and the plan "
            "sets in the chain as the plan your instructions expect, fill any gap by opening only "
            "the cited ranges, and do your part."
        )
    if verify and role in WRITING_ROLES:
        commands = ", ".join(f"`{command}`" for command in verify)
        lines.append(
            f"After you finish, cuanta itself runs {commands} and adds the results to the chain. "
            "Do not run builds yourself."
        )
    if role is Role.TESTER:
        lines.append(
            "Write or adjust tests and review the change; the verification results in the chain "
            "tell you whether the build passes."
        )
    return "\n".join(lines)


def role_prompt(
    body: str,
    role: Role,
    request: MandateRequest,
    handoff: str,
    context: str = "",
    volatile: str = "",
    advice: str = "",
) -> str:
    parts = [body.strip()] if body.strip() else []
    parts.append(
        f"You are the {role.value} of a pipeline where every role runs separately. "
        "Do only your role's part."
    )
    if context:
        parts.append(context)
    if volatile:
        parts.append(volatile)
    parts.append(f"=== REQUEST ===\n{request_block(request)}")
    parts.append(
        "=== HANDOFF CHAIN FROM EARLIER ROLES ===\n" + (handoff or "none: you are the first role.")
    )
    if advice:
        parts.append(advice)
    parts.append(schema_instruction())
    return "\n\n".join(parts)


def repair_prompt(prompt: str, results: Sequence[VerifyResult]) -> str:
    failures = "\n".join(
        "\n".join(
            (
                f"- `{result.command}` exit {result.exit_code}",
                *(f"  {line}" for line in result.errors),
            )
        )
        for result in results
        if not result.passed
    )
    return (
        f"{prompt}\n\n=== REPAIR TURN ===\n"
        "cuanta ran the verification after your change and these checks failed:\n"
        f"{failures}\n"
        "Fix only what these errors point to, open only the cited lines, then end with the JSON "
        "handoff."
    )


def definition_for(role: Role, definitions: Sequence[AgentDefinition]) -> AgentDefinition | None:
    return next((item for item in definitions if role_of(item.name) is role), None)


def tools_of(definition: AgentDefinition | None) -> tuple[str, ...]:
    if definition is None:
        return DEFAULT_TOOLS
    tools = definition.fields.get("tools")
    return tuple(str(tool) for tool in tools) if isinstance(tools, list) else DEFAULT_TOOLS


def _diff(before: Mapping[str, str], after: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    )


def _folded(paths: Sequence[str]) -> dict[str, str]:
    return {path.casefold(): path for path in paths}


def final_state(
    skipped: bool, verification: VerificationRound | None, salvaged: bool = False
) -> CompletionState:
    if salvaged or (verification is not None and not verification.passed):
        return CompletionState.PARTIAL
    return CompletionState.COMPLETE_SKIPPED if skipped else CompletionState.COMPLETE


@dataclass(slots=True)
class _Pass:
    request: MandateRequest
    plan: RoutePlan
    progress: ProgressSink
    protection: ChangePlan | None
    baseline: Mapping[str, str]
    routed: tuple[Role, ...]
    shares: Mapping[Role, float]
    margin: float
    verify: tuple[str, ...]
    budget_tokens: int
    steps: list[CrossStep] = field(default_factory=list)
    handoffs: list[RoleHandoff] = field(default_factory=list)
    rounds: list[VerificationRound] = field(default_factory=list)
    skipped: list[SkippedRole] = field(default_factory=list)
    spent: float | None = 0.0
    parent: str = ""
    last_round: VerificationRound | None = None

    def report(
        self, state: CompletionState, stopped: Message | None = None, guard_role: str = ""
    ) -> CrossReport:
        return CrossReport(
            tuple(self.steps),
            state in {CompletionState.COMPLETE, CompletionState.COMPLETE_SKIPPED},
            self.spent,
            stopped,
            state=state,
            skipped=tuple(self.skipped),
            verifications=tuple(self.rounds),
            handoffs=tuple(self.handoffs),
            guard_role=guard_role,
        )


@dataclass(frozen=True, slots=True)
class _Turn:
    role: Role
    engine: str
    model: str
    launcher: EngineLauncher
    spec: LaunchSpec
    role_cap: float
    read_only: bool
    future: tuple[Role, ...]


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
        change_plan: Callable[[MandateRequest], ChangePlan] | None = None,
        snapshot: Callable[[], Mapping[str, str]] | None = None,
        save_metrics: Callable[[str, Mapping[str, object]], None] | None = None,
        context_pack: Callable[[MandateRequest, str, str, ChangePlan | None], ContextPack]
        | None = None,
        learn_run: Callable[[str], None] | None = None,
        verifier: Callable[[Sequence[str]], tuple[VerifyResult, ...]] | None = None,
        lines_of: LinesOf | None = None,
        reads_of: Callable[[str], ReadRanges] | None = None,
        margin: Callable[[], float] | None = None,
        index_tools: Callable[[str], bool] | None = None,
        new_files: NewFileGuard | None = None,
    ) -> None:
        self._learn_run = learn_run
        self._refresh_index = refresh_index
        self._change_plan = change_plan
        self._context_pack = context_pack
        self._snapshot = snapshot
        self._save_metrics = save_metrics
        self._estimator = estimator
        self._allocator = allocator
        self._depth = depth
        self._sandbox = sandbox
        self._checkpoint = checkpoint
        self._verifier = verifier
        self._lines_of = lines_of
        self._reads_of = reads_of
        self._margin = margin
        self._index_tools = index_tools
        self._new_files = new_files
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

    def _capsule(self, text: str) -> tuple[str, str]:
        if len(text) <= INLINE_EVIDENCE_LIMIT:
            return text, ""
        digest, _, _ = self._capsules.put(text)
        reference = capsule_id(digest)
        return clip_evidence(text, reference), reference

    def _context(
        self, request: MandateRequest, role: Role, protection: ChangePlan | None
    ) -> tuple[str, str, bool]:
        if self._context_pack is None:
            return "", "", False
        pack = self._context_pack(request, self._depth, role.value, protection)
        return pack.stable_prefix, pack.excerpts, True

    def _snap(self) -> Mapping[str, str]:
        return self._snapshot() if self._snapshot is not None else {}

    def _reads(self, run_id: str) -> ReadRanges:
        if self._reads_of is None:
            return ()
        resolved: list[tuple[str, int, int]] = []
        for path, start, end in self._reads_of(run_id):
            if end >= start:
                resolved.append((path, start, end))
                continue
            lines = self._lines_of(path) if self._lines_of is not None else None
            if lines:
                resolved.append((path, start, max(start, len(lines))))
        return tuple(resolved)

    def _refresh(self, chain: HandoffChain) -> HandoffChain:
        return chain if self._lines_of is None else refresh_chain(chain, self._lines_of)

    def _stamp(self, handoff: RoleHandoff) -> RoleHandoff:
        if self._lines_of is None:
            return handoff
        return replace(handoff, facts=refresh_facts(handoff.facts, self._lines_of))

    def _launch(self, turn: _Turn, spec: LaunchSpec) -> Launch:
        return turn.launcher.launch(
            self._isolated(spec, turn.engine), lambda _: None, self._started
        )

    def run(self, request: MandateRequest, plan: RoutePlan, progress: ProgressSink) -> CrossReport:
        if self._refresh_index is not None:
            self._refresh_index()
        protection = self._change_plan(request) if self._change_plan is not None else None
        before = self._snap()
        result = self._run(request, plan, progress, protection, before)
        after = self._snap()
        outputs = set(result.verification_outputs)
        changed = tuple(path for path in _diff(before, after) if path not in outputs)
        violated = protection is not None and any(guarded(path, protection) for path in changed)
        state = CompletionState.FAILED if violated else result.state
        result = replace(
            result,
            ok=state in {CompletionState.COMPLETE, CompletionState.COMPLETE_SKIPPED},
            state=state,
            change_plan=protection,
            changed_files=changed,
        )
        if result.steps and self._save_metrics is not None:
            self._save_metrics(result.steps[0].run_id, cross_metrics(result))
        if result.steps and self._learn_run is not None:
            self._learn_run(result.steps[0].run_id)
        return result

    def _run(
        self,
        request: MandateRequest,
        plan: RoutePlan,
        progress: ProgressSink,
        protection: ChangePlan | None,
        baseline: Mapping[str, str],
    ) -> CrossReport:
        routed = tuple(
            role
            for role in CROSS_ORDER
            if (route := plan.route(role)) is not None and route.model is not None
        )
        shares = (
            self._allocator(plan, request.type, self._depth, self._budget)
            if self._allocator is not None
            else allocate_budget(dict.fromkeys(routed), self._budget)
        )
        state = _Pass(
            request,
            plan,
            progress,
            protection,
            baseline,
            routed,
            shares,
            self._margin() if self._margin is not None else DEFAULT_MARGIN,
            protection.verify if protection is not None else (),
            handoff_budget(self._depth),
        )
        self.completed = state.steps
        definitions = self._definitions()
        for role in CROSS_ORDER:
            turn = self._admit(state, role, definitions)
            if isinstance(turn, CrossReport):
                return turn
            if turn is None:
                continue
            stop = self._perform(state, turn)
            if stop is not None:
                return stop
        if not state.steps:
            return state.report(CompletionState.FAILED)
        salvaged = any(step.salvaged and step.role is Role.SENIOR for step in state.steps)
        final = final_state(bool(state.skipped), state.last_round, salvaged)
        cause = None
        if final is CompletionState.PARTIAL and salvaged:
            cause = msg("cross.writer_salvaged", role=Role.SENIOR.value)
        elif final is CompletionState.PARTIAL and state.last_round is not None:
            cause = msg("cross.verify_unresolved", role=state.last_round.role.value)
        return state.report(final, cause)

    def _admit(
        self, state: _Pass, role: Role, definitions: Sequence[AgentDefinition]
    ) -> _Turn | CrossReport | None:
        route = state.plan.route(role)
        if route is None or route.model is None:
            state.progress.publish(note(Status.SKIP, msg("cross.skipped", role=role.value)))
            return None
        if self._budget > 0 and state.spent is None:
            return state.report(CompletionState.FAILED, msg("cross.cost_unknown"))
        remaining = self._budget - state.spent if state.spent is not None else 0.0
        future = state.routed[state.routed.index(role) + 1 :]
        reserved = sum(state.shares.get(later, 0.0) for later in future)
        role_cap = max(0.0, remaining - reserved) if self._budget > 0 else 0.0
        optional = role in OPTIONAL_ROLES or (
            role is Role.TESTER and state.last_round is not None and state.last_round.passed
        )
        floor = self._budget * floor_fraction(role)
        if self._budget > 0 and not optional and role_cap + BUDGET_EPSILON < floor:
            required = floors_usd(
                (later for later in future if later not in OPTIONAL_ROLES), self._budget
            )
            wanted = max(state.shares.get(role, 0.0), floor)
            role_cap = max(role_cap, min(wanted, remaining - required), 0.0)
        if self._budget > 0 and optional and role_cap + BUDGET_EPSILON < floor:
            key = "cross.tester_skipped" if role is Role.TESTER else "cross.optional_skipped"
            reason = msg(key, role=role.value, cap=f"{role_cap:.4f}", floor=f"{floor:.4f}")
            state.skipped.append(SkippedRole(role, reason))
            state.progress.publish(note(Status.SKIP, reason))
            return None
        if self._budget > 0 and role_cap <= BUDGET_EPSILON:
            over = next((step for step in reversed(state.steps) if step.overrun_usd > 0), None)
            reason = (
                msg(
                    "cross.role_budget_overrun",
                    role=role.value,
                    other=over.role.value,
                    engine=over.engine,
                    over=f"{over.overrun_usd:.4f}",
                )
                if over is not None
                else msg("cross.role_budget", role=role.value)
            )
            return state.report(CompletionState.PARTIAL, reason)
        read_only = role is Role.ANALYST or state.request.type == INVESTIGATION
        refusal = readonly_unavailable(route.engine) if read_only else None
        if refusal is not None:
            return state.report(CompletionState.FAILED, refusal)
        launcher = self._launchers(route.engine)
        if launcher is None:
            return state.report(CompletionState.FAILED, msg("cross.no_engine", engine=route.engine))
        model = route.model.resolved or route.model.id
        spec = self._spec(
            state, role, route.engine, model, definition_for(role, definitions), role_cap
        )
        return _Turn(role, route.engine, model, launcher, spec, role_cap, read_only, future)

    def _spec(
        self,
        state: _Pass,
        role: Role,
        engine: str,
        model: str,
        definition: AgentDefinition | None,
        role_cap: float,
    ) -> LaunchSpec:
        read_only = role is Role.ANALYST or state.request.type == INVESTIGATION
        native = role_cap
        state.progress.publish(
            started(
                f"cross-{role.value}",
                msg("cross.step", role=role.value, engine=engine, model=model),
            )
        )
        if role_cap > 0:
            state.progress.publish(
                note(Status.INFO, msg("cross.share", role=role.value, cap=f"{role_cap:.4f}"))
            )
            if engine == "claude":
                native = soft_cap(role_cap, state.margin)
                state.progress.publish(
                    note(
                        Status.INFO,
                        msg(
                            "cross.native_cap",
                            role=role.value,
                            cap=f"{native:.4f}",
                            margin=f"{state.margin * 100:.0f}",
                            share=f"{role_cap:.4f}",
                        ),
                    )
                )
        effective = (
            replace(state.protection, read_only=True, edit=())
            if state.protection is not None and read_only
            else state.protection
        )
        indexed = self._index_tools(engine) if self._index_tools is not None else False
        chain_text = render_chain(self._refresh(merge_chain(state.handoffs)), state.budget_tokens)
        stable, volatile, packed = self._context(state.request, role, effective)
        partial = any(item.status is not HandoffStatus.DONE for item in state.handoffs)
        advice = guidance(role, indexed, state.verify if not read_only else (), partial)
        body = definition.prompt if definition is not None else ""
        return LaunchSpec(
            kind=CROSS_KIND,
            prompt=role_prompt(body, role, state.request, chain_text, stable, volatile, advice),
            cwd=self._cwd,
            allowed_tools=tools_of(definition) if engine == "claude" else (),
            model=model,
            max_budget_usd=native,
            max_turns=self._max_turns if engine == "claude" else 0,
            scope=role.value,
            parent_id=state.parent,
            read_only=read_only,
            task_type=state.request.type,
            depth=self._depth,
            estimate=None if state.parent else self._estimate(state.plan, state.request.type),
            pipeline_budget_usd=self._budget if not state.parent else 0.0,
            change_plan=effective,
            stable_prefix=packed,
            index_tools=indexed,
        )

    def _perform(self, state: _Pass, turn: _Turn) -> CrossReport | None:
        role = turn.role
        chain = self._refresh(merge_chain(state.handoffs))
        created: tuple[str, ...] = ()
        hidden = frozenset(self._hidden(turn))
        if self._new_files is not None and turn.engine == "codex" and not turn.read_only:
            created = self._new_files.prepare(turn.spec.change_plan)
            if created:
                state.progress.publish(note(Status.INFO, msg("cross.prepared", count=len(created))))
        launch = self._launch(turn, turn.spec)
        self._record_cap(launch.run.id, turn.spec.max_budget_usd, turn.role_cap)
        state.parent = state.parent or launch.run.id
        outcome = launch.outcome
        ok = outcome is not None and outcome.ok
        subtype = outcome.result.subtype if outcome is not None and outcome.result else ""
        text = outcome.result.text if outcome is not None and outcome.result else ""
        cost = launch.run.cost_usd
        state.spent = sum_costs((state.spent, cost))
        budget_stop = not ok and "budget" in subtype
        carried = budget_stop and turn.engine != "claude"
        if created and self._new_files is not None:
            self._new_files.settle(created)
        unreadable = self._unreadable(state, turn, hidden)
        overrun = self._overrun(state, turn, cost)
        current = self._snap()
        changed = _diff(state.baseline, current)
        state.baseline = current
        clipped, capsule = self._capsule(text)
        reads = self._reads(launch.run.id)
        handoff = self._stamp(
            build_handoff(
                text,
                role.value,
                engine=turn.engine,
                model=turn.model,
                changed=(*changed, *unreadable),
                reads=reads,
                run_id=launch.run.id,
                capsule=capsule,
                stopped=subtype if budget_stop and not carried else "",
            )
        )
        covered = _folded(chain.covered)
        read_files = tuple(dict.fromkeys(path for path, _, _ in reads))
        step = CrossStep(
            role,
            turn.engine,
            turn.model,
            launch.run.id,
            ok or carried,
            cost,
            clipped,
            launch.run.cost_source,
            turn.role_cap,
            turn.spec.max_budget_usd,
            overrun,
            budget_stop and not carried,
            False,
            estimate_tokens(render_chain(chain, state.budget_tokens)),
            chain.covered,
            read_files,
            tuple(path for path in read_files if path.casefold() in covered),
            changed,
            unreadable,
            turn.spec.index_tools is True,
        )
        state.steps.append(step)
        state.progress.publish(
            finished(
                f"cross-{role.value}",
                Status.OK if step.ok else Status.WARN if step.salvaged else Status.FAIL,
                msg("cross.done", run=launch.run.id),
            )
        )
        stop = self._settle(state, turn, handoff, changed, ok, budget_stop, carried, subtype)
        if stop is not None:
            return stop
        if role in WRITING_ROLES and changed and not docs_only(changed):
            handoff, stop = self._check(state, turn, handoff, ok, cost)
            if stop is not None:
                return stop
        state.handoffs.append(handoff)
        return None

    def _record_cap(self, run_id: str, native: float, share: float) -> None:
        if self._save_metrics is not None and native > 0:
            self._save_metrics(run_id, {"native_cap_usd": native, "role_share_usd": share})

    def _hidden(self, turn: _Turn) -> tuple[str, ...]:
        if self._new_files is None or turn.engine != "codex" or turn.read_only:
            return ()
        return self._new_files.unreadable()

    def _unreadable(
        self, state: _Pass, turn: _Turn, hidden: frozenset[str] = frozenset()
    ) -> tuple[str, ...]:
        unreadable = tuple(path for path in self._hidden(turn) if path not in hidden)
        if unreadable:
            state.progress.publish(
                note(
                    Status.WARN,
                    msg("cross.unreadable", paths=", ".join(unreadable[:SHOWN_PATHS])),
                )
            )
        return unreadable

    def _overrun(self, state: _Pass, turn: _Turn, cost: float | None) -> float:
        if turn.engine == "claude" or cost is None or turn.role_cap <= 0:
            return 0.0
        overrun = max(0.0, cost - turn.role_cap)
        if overrun > 0:
            state.progress.publish(
                note(
                    Status.WARN,
                    msg(
                        "cross.overrun",
                        role=turn.role.value,
                        engine=turn.engine,
                        cost=f"{cost:.4f}",
                        cap=f"{turn.role_cap:.4f}",
                        over=f"{overrun:.4f}",
                    ),
                )
            )
        return overrun

    def _guard(
        self,
        state: _Pass,
        role: Role,
        handoff: RoleHandoff,
        changed: Sequence[str],
        plan: ChangePlan | None,
    ) -> CrossReport | None:
        protection = plan
        violated = (
            tuple(path for path in changed if guarded(path, protection))
            if protection is not None
            else ()
        )
        if not violated:
            return None
        state.handoffs.append(handoff)
        return state.report(
            CompletionState.FAILED,
            msg("cross.guard_role", role=role.value, paths=", ".join(violated[:SHOWN_PATHS])),
            role.value,
        )

    def _settle(
        self,
        state: _Pass,
        turn: _Turn,
        handoff: RoleHandoff,
        changed: Sequence[str],
        ok: bool,
        budget_stop: bool,
        carried: bool,
        subtype: str,
    ) -> CrossReport | None:
        role = turn.role
        guard = self._guard(state, role, handoff, changed, turn.spec.change_plan)
        if guard is not None:
            return guard
        stop = self._guarded(state, handoff)
        if stop is not None:
            return stop
        if not ok and not budget_stop:
            state.handoffs.append(handoff)
            if subtype == "error_cost_unknown":
                return state.report(CompletionState.FAILED, msg("engine.cost_unknown"))
            return state.report(CompletionState.FAILED, msg("cross.failed", role=role.value))
        if handoff.status is HandoffStatus.BLOCKED and role not in OPTIONAL_ROLES:
            state.handoffs.append(handoff)
            reason = handoff.reason or handoff.summary or "no reason given"
            return state.report(
                CompletionState.PARTIAL, msg("cross.blocked", role=role.value, reason=reason)
            )
        if not budget_stop or carried:
            return None
        left = self._budget - state.spent if state.spent is not None else 0.0
        needed = floors_usd(
            (later for later in turn.future if later not in OPTIONAL_ROLES), self._budget
        )
        if left + BUDGET_EPSILON < needed:
            state.handoffs.append(handoff)
            return state.report(
                CompletionState.PARTIAL,
                msg(
                    "cross.partial_budget",
                    role=role.value,
                    left=f"{max(0.0, left):.4f}",
                    need=f"{needed:.4f}",
                ),
            )
        state.progress.publish(note(Status.WARN, msg("cross.salvaged", role=role.value)))
        return None

    def _guarded(self, state: _Pass, handoff: RoleHandoff) -> CrossReport | None:
        stop = self._checkpoint() if self._checkpoint is not None else None
        if stop is None:
            return None
        state.handoffs.append(handoff)
        return state.report(CompletionState.FAILED, stop)

    def _verify(self, state: _Pass, role: Role, attempt: int) -> VerificationRound:
        key = f"cross-verify-{role.value}-{attempt}"
        state.progress.publish(
            started(key, msg("cross.verify_commands", commands=", ".join(state.verify)))
        )
        before = self._snap()
        results = self._verifier(state.verify) if self._verifier is not None else ()
        after = self._snap()
        state.baseline = after
        found = VerificationRound(role, attempt, results, _diff(before, after))
        state.rounds.append(found)
        state.last_round = found
        state.progress.publish(
            finished(
                key,
                Status.OK if found.passed else Status.FAIL,
                msg(
                    "cross.verify",
                    role=role.value,
                    passed=sum(result.passed for result in results),
                    total=len(results),
                    seconds=f"{found.seconds:.1f}",
                ),
            )
        )
        return found

    def _check(
        self,
        state: _Pass,
        turn: _Turn,
        handoff: RoleHandoff,
        ok: bool,
        cost: float | None,
    ) -> tuple[RoleHandoff, CrossReport | None]:
        if not state.verify or self._verifier is None:
            return handoff, None
        found = self._verify(state, turn.role, 1)
        handoff = replace(handoff, verification=found.results)
        stop = self._guarded(state, handoff)
        if stop is not None:
            return handoff, stop
        left_share = turn.role_cap - (cost or 0.0)
        if found.passed or not ok or left_share <= REPAIR_MINIMUM_USD:
            return handoff, None
        state.progress.publish(note(Status.WARN, msg("cross.repair", role=turn.role.value)))
        repair = replace(
            turn.spec,
            prompt=repair_prompt(turn.spec.prompt, found.results),
            max_budget_usd=(
                soft_cap(left_share, state.margin) if turn.engine == "claude" else left_share
            ),
            parent_id=state.parent,
            estimate=None,
            pipeline_budget_usd=0.0,
        )
        hidden = frozenset(self._hidden(turn))
        fixed = self._launch(turn, repair)
        self._record_cap(fixed.run.id, repair.max_budget_usd, left_share)
        state.spent = sum_costs((state.spent, fixed.run.cost_usd))
        unreadable = self._unreadable(state, turn, hidden)
        overrun = self._overrun(state, replace(turn, role_cap=left_share), fixed.run.cost_usd)
        current = self._snap()
        changed = _diff(state.baseline, current)
        state.baseline = current
        text = (
            fixed.outcome.result.text if fixed.outcome is not None and fixed.outcome.result else ""
        )
        clipped, _ = self._capsule(text)
        state.steps.append(
            CrossStep(
                turn.role,
                turn.engine,
                turn.model,
                fixed.run.id,
                fixed.outcome is not None and fixed.outcome.ok,
                fixed.run.cost_usd,
                clipped,
                fixed.run.cost_source,
                left_share,
                repair.max_budget_usd,
                overrun,
                repair=True,
                changed_files=changed,
                unreadable_files=unreadable,
                index_tools=repair.index_tools is True,
            )
        )
        handoff = replace(
            handoff,
            files_changed=tuple(dict.fromkeys((*handoff.files_changed, *changed, *unreadable))),
        )
        guard = self._guard(state, turn.role, handoff, changed, turn.spec.change_plan)
        if guard is not None:
            return handoff, guard
        stop = self._guarded(state, handoff)
        if stop is not None:
            return handoff, stop
        found = self._verify(state, turn.role, 2)
        handoff = replace(handoff, verification=found.results)
        stop = self._guarded(state, handoff)
        if stop is not None:
            return handoff, stop
        if not found.passed:
            state.progress.publish(note(Status.WARN, msg("cross.verify_handed")))
        return handoff, None
