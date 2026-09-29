from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Protocol

from cuanta.application.engine_run import EngineLauncher, Launch, LaunchSpec
from cuanta.application.estimate import Estimator
from cuanta.application.forecast import (
    Forecaster,
    PlannedForecast,
    forecast_failure,
    publish_forecast,
)
from cuanta.application.routing import RoutePlan, without_role
from cuanta.application.scout import checker_prompt, read_budget, scout_prompt, senior_prompt
from cuanta.application.steering import (
    GovernorSetup,
    Steering,
    codex_finish_prompt,
    codex_steering,
    restart_left,
    resumable_thread,
    resume_prompt,
    role_forecast,
    role_steering,
)
from cuanta.domain.agents import AgentDefinition, role_of
from cuanta.domain.capsules import capsule_id
from cuanta.domain.change_plan import ChangePlan, guarded, plan_metrics
from cuanta.domain.costs import CostSource, sum_costs
from cuanta.domain.depth import DEFAULT_DEPTH, MAX_TURNS
from cuanta.domain.engine import BUDGET_LIMIT_SUBTYPE, GOVERNOR_STOP_SUBTYPE, EngineOutcome
from cuanta.domain.envelope import PIPELINE_SHAPE, SCOUT_SHAPE, is_fix
from cuanta.domain.errors import CuantaError
from cuanta.domain.estimates import RunEstimate
from cuanta.domain.evidence_pack import (
    PackCheck,
    SeniorScope,
    change_digest,
    check_pack,
    fallback_pack,
    leaked_reads,
    outside_edits,
    pack_handoff,
    parse_pack,
    render_pack,
    scope_plan,
)
from cuanta.domain.governor import (
    RESUMED,
    ROTATED,
    SALVAGED,
    SKIPPED,
    ReactionKind,
    ReactionTaken,
)
from cuanta.domain.governor_report import (
    BEST_EFFORT,
    HOOKS,
    discipline_modes,
    governor_metrics,
)
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
from cuanta.domain.read_discipline import READ_LINE_LIMIT, discipline_prompt
from cuanta.domain.role_budgets import (
    OPTIONAL_ROLES,
    OvershootMargins,
    RepairBudget,
    floor_fraction,
    floors_usd,
    native_cap,
    role_split,
)
from cuanta.domain.role_handoff import (
    Fact,
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
from cuanta.domain.routing import Provider, Role, parse_provider
from cuanta.domain.sandbox import SANDBOX_MODE, SandboxLaunch, docs_only
from cuanta.domain.scout import (
    SCOUT_TOOLS,
    DocsChoice,
    DocsMode,
    DocsReason,
    ScoutMode,
    docs_choice,
)
from cuanta.domain.scout_report import docs_payload, scout_payload
from cuanta.ports.capsules import CapsuleStore
from cuanta.ports.engine import Engine
from cuanta.ports.progress import ProgressSink

CROSS_ORDER = (Role.ANALYST, Role.SENIOR, Role.TESTER, Role.DOCS)
SCOUT_ORDER = (Role.SCOUT, Role.SENIOR, Role.TESTER, Role.DOCS)
TEAM_ROLES = frozenset({*CROSS_ORDER, *SCOUT_ORDER})
READ_ONLY_ROLES = frozenset({Role.ANALYST, Role.SCOUT})
CROSS_KIND = "cross"
DEFAULT_TOOLS = ("Read", "Grep", "Glob", "Edit", "Write")
WRITING_ROLES = frozenset({Role.SENIOR, Role.TESTER, Role.DOCS})
REPAIR_MINIMUM_USD = 0.01
BUDGET_EPSILON = 1e-9
SHOWN_PATHS = 5
NO_NODE_COMMANDS = "Do not run node, npm or npx commands (builds or tests)"

ReadRanges = tuple[tuple[str, int, int], ...]
LinesOf = Callable[[str], Sequence[str] | None]
VerifyCommands = Callable[[Sequence[str], Callable[[], bool]], tuple[VerifyResult, ...]]


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
    rotated: bool = False
    stopped: bool = False
    resumed: bool = False
    read_discipline: str = ""


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
class ScoutRecord:
    run_id: str
    capsule: str
    check: PackCheck
    senior: SeniorScope | None = None
    mode: ScoutMode = ScoutMode.LAUNCH


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
    governor: tuple[ReactionTaken, ...] = ()
    scout: ScoutRecord | None = None
    docs: DocsChoice | None = None

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
    governor = governor_metrics(
        report.governor,
        discipline_modes((step.role.value, step.read_discipline) for step in report.steps),
    )
    if governor:
        base["governor"] = governor
    if report.scout is not None:
        scout = report.scout
        base["scout"] = scout_payload(
            scout.mode.value, scout.run_id, scout.capsule, scout.check, scout.senior
        )
    if report.docs is not None and report.docs.reason is not DocsReason.FORCED_ON:
        base["docs"] = docs_payload(report.docs)
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


def guidance(
    role: Role,
    index_tools: bool,
    verify: Sequence[str],
    partial: bool = False,
    no_builds: bool = False,
    pack: bool = False,
) -> str:
    opener = " with Cuanta page (path and lines as start:end)" if index_tools else ""
    finder = " Use Cuanta find before Glob or Grep." if index_tools else ""
    source = "the evidence pack" if pack else "the chain"
    lines = [
        f"READ ONLY WHAT IS NEEDED: {source} lists anchored facts as path:start-end. "
        f"Open those ranges{opener} instead of whole files, and do not re-read files {source} "
        f"covers unless an anchor is marked stale.{finder}"
    ]
    if partial and pack:
        lines.append(
            "The scout's evidence pack is partial: it stopped early or returned no structured "
            "pack. That does not block you: treat the request, the pack's anchored facts and its "
            "edit set as the plan your instructions expect, fill any gap by opening only the "
            "cited ranges, and do your part."
        )
    elif partial:
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
            + (
                f"{NO_NODE_COMMANDS}; cuanta runs the checks between roles."
                if no_builds
                else "Do not run builds yourself."
            )
        )
    elif no_builds and role in WRITING_ROLES:
        lines.append(f"{NO_NODE_COMMANDS}: this engine cannot run them on this host.")
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


def _finished_turn(outcome: EngineOutcome) -> bool:
    answer = outcome.result
    if answer is None or not answer.text.strip():
        return False
    return outcome.ok or (outcome.exit_code == 0 and answer.subtype == BUDGET_LIMIT_SUBTYPE)


def _discipline(turn: _Turn) -> str:
    if turn.spec.read_discipline is not True:
        return ""
    return HOOKS if turn.engine == "claude" else BEST_EFFORT


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
    margins: OvershootMargins
    verify: tuple[str, ...]
    budget_tokens: int
    steps: list[CrossStep] = field(default_factory=list)
    handoffs: list[RoleHandoff] = field(default_factory=list)
    rounds: list[VerificationRound] = field(default_factory=list)
    skipped: list[SkippedRole] = field(default_factory=list)
    spent: float | None = 0.0
    parent: str = ""
    last_round: VerificationRound | None = None
    repair_usd: float = 0.0
    forecast: PlannedForecast | None = None
    governed: list[ReactionTaken] = field(default_factory=list)
    trailing: list[CrossStep] = field(default_factory=list)
    order: tuple[Role, ...] = CROSS_ORDER
    docs: DocsChoice | None = None
    scout: ScoutRecord | None = None
    before: dict[str, str | None] = field(default_factory=dict)
    origin: Mapping[str, str] = field(default_factory=dict)

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
            governor=tuple(self.governed),
            scout=self.scout,
            docs=self.docs,
        )

    @property
    def scouted(self) -> bool:
        return self.order == SCOUT_ORDER


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
        allocator: Callable[[RoutePlan, str, str, float, bool, bool], RepairBudget] | None = None,
        refresh_index: Callable[[], None] | None = None,
        change_plan: Callable[[MandateRequest], ChangePlan] | None = None,
        snapshot: Callable[[], Mapping[str, str]] | None = None,
        save_metrics: Callable[[str, Mapping[str, object]], None] | None = None,
        context_pack: Callable[[MandateRequest, str, str, ChangePlan | None], ContextPack]
        | None = None,
        learn_run: Callable[[str], None] | None = None,
        verifier: VerifyCommands | None = None,
        lines_of: LinesOf | None = None,
        reads_of: Callable[[str], ReadRanges] | None = None,
        margin: Callable[[], OvershootMargins] | None = None,
        index_tools: Callable[[str], bool] | None = None,
        new_files: NewFileGuard | None = None,
        build_blocked: frozenset[str] = frozenset(),
        forecaster: Forecaster | None = None,
        new_run_id: Callable[[], str] | None = None,
        governor: GovernorSetup | None = None,
        read_discipline: Callable[[str], bool] | None = None,
        read_max_lines: int = READ_LINE_LIMIT,
        docs_mode: DocsMode = DocsMode.ON,
    ) -> None:
        self._docs_mode = docs_mode
        self._read_discipline = read_discipline
        self._read_max_lines = read_max_lines
        self._governor = governor
        self._forecaster = forecaster
        self._new_run_id = new_run_id
        self._build_blocked = build_blocked
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
        self._active: Engine | None = None
        self._halted = False
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

    def _launch(self, turn: _Turn, spec: LaunchSpec, steering: Steering | None = None) -> Launch:
        engine = turn.launcher.engine
        self._active = engine
        if self._halted:
            engine.cancel()

        def started(run_id: str) -> None:
            self._started(run_id)
            if steering is not None:
                steering.started(run_id)

        try:
            if steering is None:
                return turn.launcher.launch(
                    self._isolated(spec, turn.engine), lambda _: None, self._started
                )
            return turn.launcher.launch(
                replace(self._isolated(spec, turn.engine), steer=True), steering, started
            )
        finally:
            self._active = None

    def _stopped(self) -> bool:
        return self._halted

    def stop(self) -> bool:
        self._halted = True
        engine = self._active
        if engine is not None:
            engine.cancel()
        return True

    def _halt(self, state: _Pass, handoff: RoleHandoff | None = None) -> CrossReport | None:
        if not self._halted:
            return None
        if handoff is not None:
            state.handoffs.append(handoff)
        final = CompletionState.PARTIAL if state.steps else CompletionState.FAILED
        return state.report(final, msg("cross.stopped"))

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
        order = SCOUT_ORDER if plan.route(Role.SCOUT) is not None else CROSS_ORDER
        pinned = plan.route(Role.DOCS) is not None and Role.DOCS in plan.policy.role_models
        docs = (
            docs_choice(self._docs_mode, request, self._sandbox is not None, pinned)
            if request.type != INVESTIGATION
            else None
        )
        docs_off = docs is not None and not docs.on
        if docs_off:
            plan = without_role(plan, Role.DOCS)
        routed = tuple(
            role
            for role in order
            if (route := plan.route(role)) is not None and route.model is not None
        )
        verify = protection.verify if protection is not None else ()
        repairable = bool(verify) and self._verifier is not None
        split = (
            self._allocator(plan, request.type, self._depth, self._budget, repairable, docs_off)
            if self._allocator is not None
            else role_split(
                dict.fromkeys(routed),
                self._budget,
                is_fix(request.type) and repairable,
                docs_off=docs_off and repairable,
            )
        )
        reserve = split.repair_usd if repairable else 0.0
        state = _Pass(
            request,
            plan,
            progress,
            protection,
            baseline,
            routed,
            split.shares,
            self._margin() if self._margin is not None else OvershootMargins(),
            verify,
            handoff_budget(self._depth),
            repair_usd=reserve,
            order=order,
            docs=docs,
        )
        if docs is not None and docs.reason is not DocsReason.FORCED_ON:
            progress.publish(note(Status.INFO, docs.message))
        if reserve > 0:
            key = (
                "cross.repair_reserve"
                if not split.from_docs
                else "cross.repair_reserve_docs"
                if is_fix(request.type)
                else "docs.funds_repair"
            )
            progress.publish(note(Status.INFO, msg(key, cap=f"{reserve:.4f}")))
        self.completed = state.steps
        definitions = self._definitions()
        for role in order:
            halt = self._halt(state)
            if halt is not None:
                return halt
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
        later_shares = sum(state.shares.get(later, 0.0) for later in future)
        reserved = later_shares + self._held(state)
        role_cap = max(0.0, remaining - reserved) if self._budget > 0 else 0.0
        optional = role in OPTIONAL_ROLES or (
            role is Role.TESTER and state.last_round is not None and state.last_round.passed
        )
        floor = self._budget * floor_fraction(role)
        wanted = max(state.shares.get(role, 0.0), floor)
        if self._budget > 0 and not optional and role_cap + BUDGET_EPSILON < floor:
            required = floors_usd(
                (later for later in future if later not in OPTIONAL_ROLES), self._budget
            )
            role_cap = max(role_cap, min(wanted, remaining - required), 0.0)
        if self._budget > 0 and optional and role_cap + BUDGET_EPSILON < floor:
            role_cap = max(role_cap, min(wanted, remaining - later_shares))
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
        read_only = role in READ_ONLY_ROLES or state.request.type == INVESTIGATION
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

    def _held(self, state: _Pass) -> float:
        repairable = bool(state.verify) and self._verifier is not None
        return state.repair_usd if repairable else 0.0

    def _repair_funds(self, state: _Pass, turn: _Turn, left_share: float) -> float:
        if state.repair_usd <= 0 or state.spent is None:
            return left_share
        future = sum(state.shares.get(later, 0.0) for later in turn.future)
        room = self._budget - state.spent - future
        return max(left_share, min(max(0.0, left_share) + state.repair_usd, room))

    def _spec(
        self,
        state: _Pass,
        role: Role,
        engine: str,
        model: str,
        definition: AgentDefinition | None,
        role_cap: float,
    ) -> LaunchSpec:
        read_only = role in READ_ONLY_ROLES or state.request.type == INVESTIGATION
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
                native = native_cap(role_cap, state.margins.usd(model))
                state.progress.publish(
                    note(
                        Status.INFO,
                        msg(
                            "cross.native_cap",
                            role=role.value,
                            cap=f"{native:.4f}",
                            margin=f"{role_cap - native:.4f}",
                            share=f"{role_cap:.4f}",
                        ),
                    )
                )
        effective = (
            replace(state.protection, read_only=True, edit=())
            if state.protection is not None and read_only
            else state.protection
        )
        scouted = state.scout if role is Role.SENIOR else None
        if scouted is not None:
            effective = scope_plan(state.protection, scouted.check.pack)
        indexed = self._index_tools(engine) if self._index_tools is not None else False
        disciplined = self._read_discipline(engine) if self._read_discipline is not None else False
        chain_text = render_chain(self._refresh(merge_chain(state.handoffs)), state.budget_tokens)
        stable, volatile, packed = (
            self._context(state.request, role, effective) if scouted is None else ("", "", False)
        )
        partial = any(item.status is not HandoffStatus.DONE for item in state.handoffs)
        advice = guidance(
            role,
            indexed,
            state.verify if not read_only else (),
            partial,
            engine in self._build_blocked,
            scouted is not None,
        )
        if disciplined and engine != "claude":
            advice = f"{advice}\n{discipline_prompt(self._read_max_lines)}"
        body = definition.prompt if definition is not None else ""
        prompt = (
            self._scout_prompt(state, role, engine, body, stable, volatile, advice)
            if state.scouted and role in {Role.SCOUT, Role.SENIOR, Role.TESTER}
            else role_prompt(body, role, state.request, chain_text, stable, volatile, advice)
        )
        tools = SCOUT_TOOLS if role is Role.SCOUT else tools_of(definition)
        return LaunchSpec(
            kind=CROSS_KIND,
            prompt=prompt,
            cwd=self._cwd,
            allowed_tools=tools if engine == "claude" else (),
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
            read_discipline=True if disciplined else None,
        )

    def _scout_prompt(
        self,
        state: _Pass,
        role: Role,
        engine: str,
        body: str,
        stable: str,
        volatile: str,
        advice: str,
    ) -> str:
        request = request_block(state.request)
        scouted = state.scout
        if role is Role.SCOUT or scouted is None:
            return scout_prompt(body, request, stable, volatile, advice)
        pack = scouted.check.pack
        if role is Role.SENIOR:
            planned = role_forecast(state.forecast, Role.SENIOR)
            reads = read_budget(pack, planned.stops.max_reads if planned is not None else 0)
            return senior_prompt(body, request, pack, reads, advice)
        later = [item for item in state.handoffs if item.role != Role.SCOUT.value]
        chain = render_chain(self._refresh(merge_chain(later)), state.budget_tokens)
        return checker_prompt(
            body,
            request,
            chain,
            self._digest(state),
            pack.tests,
            advice,
            engine in self._build_blocked,
        )

    def _text(self, path: str) -> str | None:
        lines = self._lines_of(path) if self._lines_of is not None else None
        return "\n".join(lines) if lines is not None else None

    def _digest(self, state: _Pass) -> str:
        outputs = {path for item in state.rounds for path in item.outputs}
        changed = tuple(path for path in _diff(state.origin, self._snap()) if path not in outputs)
        return change_digest(changed, state.before, self._text)

    def _scouted(
        self,
        state: _Pass,
        turn: _Turn,
        text: str,
        reads: ReadRanges,
        run_id: str,
        stopped: str,
    ) -> RoleHandoff:
        protection = state.protection
        planned = tuple(target.path for target in protection.edit) if protection else ()
        parsed = parse_pack(text)
        facts = tuple(Fact(path, start, end, "read by scout") for path, start, end in reads)
        pack = parsed if parsed is not None else fallback_pack(text, facts, ())
        if stopped:
            pack = replace(pack, status=HandoffStatus.PARTIAL)
        check = check_pack(pack, self._lines_of, planned)
        digest, _, _ = self._capsules.put(render_pack(check.pack))
        capsule = capsule_id(digest)
        state.scout = ScoutRecord(run_id, capsule, check)
        state.origin = dict(state.baseline)
        state.before = (
            {path: self._text(path) for path in check.pack.edit}
            if self._lines_of is not None
            else {}
        )
        self._pack_notes(state, turn.role, check, capsule, parsed is None)
        return pack_handoff(
            check,
            turn.role.value,
            engine=turn.engine,
            model=turn.model,
            run_id=run_id,
            capsule=capsule,
            verify=state.verify,
            reason=stopped,
        )

    def _pack_notes(
        self, state: _Pass, role: Role, check: PackCheck, capsule: str, fallback: bool
    ) -> None:
        pack = check.pack
        publish = state.progress.publish
        publish(
            note(
                Status.INFO,
                msg(
                    "scout.pack",
                    role=role.value,
                    tokens=f"{check.tokens:,}",
                    budget=f"{check.budget:,}",
                    facts=len(pack.facts),
                    snippets=len(pack.snippets),
                    edit=", ".join(pack.edit[:SHOWN_PATHS]) or "-",
                    capsule=capsule,
                ),
            )
        )
        if fallback:
            publish(note(Status.WARN, msg("scout.pack_fallback", role=role.value)))
        if check.edit_from_plan:
            paths = ", ".join(pack.edit[:SHOWN_PATHS])
            publish(note(Status.WARN, msg("scout.pack_plan_edit", role=role.value, paths=paths)))
        if check.invalid:
            publish(note(Status.WARN, msg("scout.pack_invalid", count=check.invalid)))
        if check.trimmed:
            publish(
                note(
                    Status.INFO,
                    msg(
                        "scout.pack_trimmed",
                        snippets=check.dropped_snippets,
                        facts=check.dropped_facts,
                    ),
                )
            )
        if check.over_budget:
            publish(
                note(
                    Status.WARN,
                    msg(
                        "scout.pack_over",
                        tokens=f"{check.tokens:,}",
                        budget=f"{check.budget:,}",
                    ),
                )
            )

    def _senior_scope(self, state: _Pass, handoff: RoleHandoff) -> None:
        scouted = state.scout
        if scouted is None:
            return
        pack = scouted.check.pack
        steps = [step for step in state.steps if step.role is Role.SENIOR]
        changed = tuple(dict.fromkeys(path for step in steps for path in step.changed_files))
        reads = tuple(dict.fromkeys(path for step in steps for path in step.read_files))
        earlier = scouted.senior
        scope = SeniorScope(
            pack.edit,
            pack.files,
            leaked_reads(reads, pack.edit, pack.files),
            outside_edits(changed, pack.edit, handoff.plan.edit),
        )
        state.scout = replace(scouted, senior=scope)
        known = (earlier.leaked or ()) if earlier is not None else ()
        named = earlier.outside.named if earlier is not None else ()
        unnamed = earlier.outside.unnamed if earlier is not None else ()
        role = Role.SENIOR.value
        leaked = tuple(path for path in scope.leaked or () if path not in known)
        if leaked:
            paths = ", ".join(leaked[:SHOWN_PATHS])
            text = msg("scout.leaks", role=role, count=len(leaked), paths=paths)
            state.progress.publish(note(Status.WARN, text))
        fresh = tuple(path for path in scope.outside.named if path not in named)
        if fresh:
            paths = ", ".join(fresh[:SHOWN_PATHS])
            state.progress.publish(note(Status.INFO, msg("scout.named", role=role, paths=paths)))
        stray = tuple(path for path in scope.outside.unnamed if path not in unnamed)
        if stray:
            paths = ", ".join(stray[:SHOWN_PATHS])
            state.progress.publish(note(Status.WARN, msg("scout.unnamed", role=role, paths=paths)))

    def _root_forecast(self, state: _Pass, turn: _Turn) -> _Turn:
        provider = parse_provider(turn.engine)
        if state.parent or provider is None or self._forecaster is None or self._new_run_id is None:
            return turn
        run_id = self._new_run_id()
        try:
            planned = self._forecaster.plan(
                state.request.type,
                state.plan,
                provider,
                self._depth,
                SCOUT_SHAPE if state.scouted else PIPELINE_SHAPE,
                self._budget,
                state.protection,
                max_turns=self._max_turns if provider is Provider.CLAUDE else 0,
            )
            stored = self._forecaster.record(run_id, planned, state.request)
        except (CuantaError, ValueError) as error:
            state.progress.publish(note(Status.WARN, forecast_failure(error)))
        else:
            publish_forecast(state.progress, stored)
            state.forecast = stored
        return replace(turn, spec=replace(turn.spec, run_id=run_id))

    def _steering(self, state: _Pass, turn: _Turn) -> Steering | None:
        if turn.engine not in {"claude", "codex"}:
            return None
        steer = role_steering if turn.engine == "claude" else codex_steering
        return steer(
            self._governor,
            turn.launcher,
            turn.spec,
            turn.role,
            turn.role_cap,
            state.forecast,
            state.progress,
        )

    def _steered(self, state: _Pass, turn: _Turn) -> tuple[_Turn, Launch, tuple[str, ...]]:
        steering = self._steering(state, turn)
        launch = self._launch(turn, turn.spec, steering)
        if steering is None:
            return turn, launch, ()
        follow = (
            self._rotate(state, turn, launch, steering)
            if turn.engine == "claude"
            else self._resume(state, turn, launch, steering)
        )
        state.governed.extend(steering.taken)
        return follow if follow is not None else (turn, launch, ())

    def _resume(
        self, state: _Pass, turn: _Turn, launch: Launch, steering: Steering
    ) -> tuple[_Turn, Launch, tuple[str, ...]] | None:
        if steering.stopped is None:
            return None
        role = turn.role.value
        result = launch.outcome.result
        thread = result.session_id if result is not None else ""
        if self._halted or not resumable_thread(thread) or not turn.launcher.resumable():
            steering.settle(SALVAGED)
            state.progress.publish(note(Status.WARN, msg("governor.resume_unavailable", role=role)))
            return None
        state.progress.publish(note(Status.INFO, msg("governor.resuming", role=role)))
        cost = launch.run.cost_usd
        state.parent = state.parent or launch.run.id
        left = max(turn.role_cap - (cost or 0.0), 0.0)
        spec = replace(
            turn.spec,
            prompt=codex_finish_prompt(_diff(state.baseline, self._snap())),
            resume_session=thread,
            run_id="",
            parent_id=state.parent,
            estimate=None,
            pipeline_budget_usd=0.0,
            max_budget_usd=left,
        )
        resumed = self._launch(turn, spec)
        if not _finished_turn(resumed.outcome):
            steering.settle(SALVAGED)
            state.spent = sum_costs((state.spent, resumed.run.cost_usd))
            state.trailing.append(
                CrossStep(
                    turn.role,
                    turn.engine,
                    turn.model,
                    resumed.run.id,
                    False,
                    resumed.run.cost_usd,
                    "",
                    resumed.run.cost_source,
                    left,
                    left,
                    index_tools=turn.spec.index_tools is True,
                    resumed=True,
                    read_discipline=_discipline(turn),
                )
            )
            state.progress.publish(note(Status.WARN, msg("governor.resume_failed", role=role)))
            return None
        steering.settle(RESUMED)
        state.spent = sum_costs((state.spent, cost))
        self._record_cap(launch.run.id, turn.spec.max_budget_usd, turn.role_cap)
        if self._save_metrics is not None and cost is not None:
            self._save_metrics(launch.run.id, {"governor_stop_estimate_usd": round(cost, 6)})
        clipped, _ = self._capsule(result.text if result is not None else "")
        state.steps.append(
            CrossStep(
                turn.role,
                turn.engine,
                turn.model,
                launch.run.id,
                True,
                cost,
                clipped,
                launch.run.cost_source,
                turn.role_cap,
                turn.spec.max_budget_usd,
                index_tools=turn.spec.index_tools is True,
                stopped=True,
                read_discipline=_discipline(turn),
            )
        )
        state.progress.publish(
            note(
                Status.INFO,
                msg("governor.resumed", role=role, cost=f"{cost or 0.0:.4f}", left=f"{left:.4f}"),
            )
        )
        return replace(turn, spec=spec, role_cap=left), resumed, (launch.run.id,)

    def _rotate(
        self, state: _Pass, turn: _Turn, launch: Launch, steering: Steering
    ) -> tuple[_Turn, Launch, tuple[str, ...]] | None:
        checkpoint = steering.checkpoint
        if checkpoint is None:
            return None
        cost = launch.run.cost_usd
        result = launch.outcome.result
        left = restart_left(turn.role_cap, cost) if cost is not None else 0.0
        if (
            self._halted
            or not launch.outcome.ok
            or launch.outcome.late_results > 0
            or result is None
            or not result.text.strip()
            or left <= REPAIR_MINIMUM_USD
        ):
            state.progress.publish(
                note(Status.WARN, msg("governor.rotation_skipped", role=turn.role.value))
            )
            steering.settle(SKIPPED, ReactionKind.ROTATE)
            return None
        steering.settle(ROTATED, ReactionKind.ROTATE)
        text, _ = self._capsule(result.text)
        state.spent = sum_costs((state.spent, cost))
        state.parent = state.parent or launch.run.id
        if self._save_metrics is not None:
            self._save_metrics(
                launch.run.id,
                {
                    "native_cap_usd": turn.spec.max_budget_usd,
                    "role_share_usd": turn.role_cap,
                    "rotation_saving_usd": round(checkpoint.saving_usd, 6),
                },
            )
        state.steps.append(
            CrossStep(
                turn.role,
                turn.engine,
                turn.model,
                launch.run.id,
                True,
                cost,
                text,
                launch.run.cost_source,
                turn.role_cap,
                turn.spec.max_budget_usd,
                index_tools=turn.spec.index_tools is True,
                rotated=True,
                read_discipline=_discipline(turn),
            )
        )
        native = native_cap(left, state.margins.usd(turn.model))
        state.progress.publish(
            note(
                Status.INFO,
                msg(
                    "governor.rotated",
                    role=turn.role.value,
                    left=f"{left:.4f}",
                    cap=f"{native:.4f}",
                    saving=f"{checkpoint.saving_usd:.4f}",
                ),
            )
        )
        spec = replace(
            turn.spec,
            prompt=resume_prompt(turn.spec.prompt, text),
            max_budget_usd=native,
            run_id="",
            parent_id=state.parent,
            estimate=None,
            pipeline_budget_usd=0.0,
        )
        steering.restart()
        fresh = self._launch(turn, spec, steering)
        return replace(turn, spec=spec, role_cap=left), fresh, (launch.run.id,)

    def _perform(self, state: _Pass, turn: _Turn) -> CrossReport | None:
        role = turn.role
        chain = self._refresh(merge_chain(state.handoffs))
        created: tuple[str, ...] = ()
        hidden = frozenset(self._hidden(turn))
        if self._new_files is not None and turn.engine == "codex" and not turn.read_only:
            created = self._new_files.prepare(turn.spec.change_plan)
            if created:
                state.progress.publish(note(Status.INFO, msg("cross.prepared", count=len(created))))
        turn = self._root_forecast(state, turn)
        turn, launch, earlier = self._steered(state, turn)
        self._record_cap(launch.run.id, turn.spec.max_budget_usd, turn.role_cap)
        state.parent = state.parent or launch.run.id
        outcome = launch.outcome
        ok = outcome is not None and outcome.ok
        subtype = outcome.result.subtype if outcome is not None and outcome.result else ""
        text = outcome.result.text if outcome is not None and outcome.result else ""
        cost = launch.run.cost_usd
        state.spent = sum_costs((state.spent, cost))
        budget_stop = not ok and ("budget" in subtype or subtype == GOVERNOR_STOP_SUBTYPE)
        carried = budget_stop and turn.engine != "claude" and subtype != GOVERNOR_STOP_SUBTYPE
        if created and self._new_files is not None:
            self._new_files.settle(created)
        unreadable = self._unreadable(state, turn, hidden)
        overrun = self._overrun(state, turn, cost)
        current = self._snap()
        changed = _diff(state.baseline, current)
        state.baseline = current
        clipped, capsule = self._capsule(text)
        reads = tuple(item for run_id in (*earlier, launch.run.id) for item in self._reads(run_id))
        stopped = subtype if budget_stop and not carried else ""
        handoff = (
            self._scouted(state, turn, text, reads, launch.run.id, stopped)
            if role is Role.SCOUT
            else self._stamp(
                build_handoff(
                    text,
                    role.value,
                    engine=turn.engine,
                    model=turn.model,
                    changed=(*changed, *unreadable),
                    reads=reads,
                    run_id=launch.run.id,
                    capsule=capsule,
                    stopped=stopped,
                )
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
            resumed=bool(turn.spec.resume_session),
            read_discipline=_discipline(turn),
        )
        state.steps.append(step)
        state.steps.extend(state.trailing)
        state.trailing.clear()
        state.progress.publish(
            finished(
                f"cross-{role.value}",
                Status.OK if step.ok else Status.WARN if step.salvaged else Status.FAIL,
                msg("cross.done", run=launch.run.id),
            )
        )
        if role is Role.SENIOR:
            self._senior_scope(state, handoff)
        stop = self._settle(state, turn, handoff, changed, ok, budget_stop, carried, subtype)
        if stop is not None:
            return stop
        if role in WRITING_ROLES and changed and not docs_only(changed):
            handoff, stop = self._check(state, turn, handoff, ok or carried, cost)
            if role is Role.SENIOR:
                self._senior_scope(state, handoff)
            if stop is not None:
                return replace(stop, scout=state.scout)
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
        capped = turn.role_cap > 0 or bool(turn.spec.resume_session)
        if turn.engine == "claude" or cost is None or not capped:
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
        halt = self._halt(state, handoff)
        if halt is not None:
            return halt
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
        results = self._verifier(state.verify, self._stopped) if self._verifier is not None else ()
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
        stop = self._guarded(state, handoff) or self._halt(state, handoff)
        if stop is not None:
            return handoff, stop
        if found.passed:
            state.repair_usd = 0.0
        left_share = self._repair_funds(state, turn, turn.role_cap - (cost or 0.0))
        if found.passed or not ok or left_share <= REPAIR_MINIMUM_USD:
            return handoff, None
        state.repair_usd = 0.0
        state.progress.publish(note(Status.WARN, msg("cross.repair", role=turn.role.value)))
        repair = replace(
            turn.spec,
            prompt=repair_prompt(turn.spec.prompt, found.results),
            max_budget_usd=(
                native_cap(left_share, state.margins.usd(turn.model))
                if turn.engine == "claude"
                else left_share
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
                read_discipline=_discipline(turn),
            )
        )
        handoff = replace(
            handoff,
            files_changed=tuple(dict.fromkeys((*handoff.files_changed, *changed, *unreadable))),
        )
        guard = self._guard(state, turn.role, handoff, changed, turn.spec.change_plan)
        if guard is not None:
            return handoff, guard
        stop = self._guarded(state, handoff) or self._halt(state, handoff)
        if stop is not None:
            return handoff, stop
        found = self._verify(state, turn.role, 2)
        handoff = replace(handoff, verification=found.results)
        stop = self._guarded(state, handoff) or self._halt(state, handoff)
        if stop is not None:
            return handoff, stop
        if not found.passed:
            state.progress.publish(note(Status.WARN, msg("cross.verify_handed")))
        return handoff, None
