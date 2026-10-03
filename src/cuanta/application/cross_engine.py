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
from cuanta.application.routing import RoutePlan, pure_plan, without_role
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
from cuanta.application.timing import PhaseRecorder
from cuanta.domain.agents import AgentDefinition, role_of
from cuanta.domain.anchors import anchor_notes
from cuanta.domain.capsules import capsule_id
from cuanta.domain.change_plan import ChangePlan, guarded, plan_metrics, plan_notes
from cuanta.domain.claude_variants import resolve_variant
from cuanta.domain.costs import CostSource, sum_costs
from cuanta.domain.engine import (
    BUDGET_LIMIT_SUBTYPE,
    GOVERNOR_STOP_SUBTYPE,
    WALL_LIMIT_SUBTYPE,
    EngineOutcome,
)
from cuanta.domain.envelope import PIPELINE_SHAPE, SCOUT_SHAPE, is_fix
from cuanta.domain.errors import CuantaError, DomainFailure
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
from cuanta.domain.implementation import (
    REPAIR_FEEDBACK_BYTES,
    ImplementationReport,
    VerificationDelta,
    implementation_prompt,
    repair_feedback,
)
from cuanta.domain.ledger import Run
from cuanta.domain.mandate import (
    INLINE_EVIDENCE_LIMIT,
    INVESTIGATION,
    MandateRequest,
    clip_evidence,
    with_defaults,
)
from cuanta.domain.messages import Message, message_payload, msg
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
from cuanta.domain.stop_reason import minutes_text
from cuanta.domain.telemetry import unreadable_note
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
WALL_FLOOR_S = 1.0


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
    partial: bool = False


@dataclass(frozen=True, slots=True)
class VerificationRound:
    role: Role
    attempt: int
    results: tuple[VerifyResult, ...]
    outputs: tuple[str, ...] = ()
    delta: VerificationDelta | None = None

    @property
    def passed(self) -> bool:
        return (
            self.delta.passed
            if self.delta is not None
            else all(result.passed for result in self.results)
        )

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
    steered: bool = False
    partial: bool = False

    @property
    def verification_outputs(self) -> tuple[str, ...]:
        roles = {path for step in self.steps for path in step.changed_files}
        found = {path for item in self.verifications for path in item.outputs}
        return tuple(sorted(found - roles))


class _WallExpired(Exception):
    pass


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
    if report.stopped is not None:
        base["stopped"] = message_payload(report.stopped)
    governor = governor_metrics(
        report.governor,
        discipline_modes((step.role.value, step.read_discipline) for step in report.steps),
        recorded=report.steered,
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
    filled = with_defaults(request)
    lines = [
        f"TYPE: {filled.type}",
        f"WHAT: {filled.what}",
        f"WHY / EVIDENCE: {filled.why}",
        f"WHERE: {filled.where}",
        f"CONSTRAINTS: {filled.constraints}",
        f"TESTS: {filled.tests}",
        f"OUT OF SCOPE: {filled.out_of_scope}",
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
    failed = [result for result in results if not result.passed]
    share = REPAIR_FEEDBACK_BYTES // max(1, len(failed))
    failures = "\n".join(
        "\n".join(
            (
                f"- `{result.command}` exit {result.exit_code}",
                *(f"  {line}" for line in repair_feedback(result.errors, share).splitlines()),
            )
        )
        for result in failed
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
    steered: bool = False
    before: dict[str, str | None] = field(default_factory=dict)
    origin: Mapping[str, str] = field(default_factory=dict)
    anchors_noted: bool = False
    total: float | None = 0.0
    partial: bool = False
    walled: bool = False

    def report(
        self, state: CompletionState, stopped: Message | None = None, guard_role: str = ""
    ) -> CrossReport:
        return CrossReport(
            tuple(self.steps),
            state in {CompletionState.COMPLETE, CompletionState.COMPLETE_SKIPPED},
            self.total,
            stopped,
            state=state,
            skipped=tuple(self.skipped),
            verifications=tuple(self.rounds),
            handoffs=tuple(self.handoffs),
            guard_role=guard_role,
            governor=tuple(self.governed),
            scout=self.scout,
            docs=self.docs,
            steered=self.steered,
            partial=self.partial,
        )

    def charge(self, run: Run, cost: float | None) -> None:
        self.spent = sum_costs((self.spent, cost))
        self.total = sum_costs((self.total, run.cost_usd))
        self.partial = self.partial or run.partial

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
        docs_flag: bool = False,
        timing: PhaseRecorder | None = None,
        implementation_profile: str = "balanced",
        variant: str = "",
        pure: bool = False,
        model: str = "",
        wall_s: float = 0.0,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._wall_s = max(0.0, wall_s)
        self._monotonic = monotonic
        self._deadline: float | None = None
        self._implementation_profile = implementation_profile
        self._variant = variant
        self._pure = pure
        self._model = model
        self._timing = timing or PhaseRecorder()
        self._implementations: dict[str, ImplementationReport] = {}
        self._lost_telemetry = 0
        self._timing_run = ""
        self._timing_role = ""
        self._docs_mode = docs_mode
        self._docs_flag = docs_flag
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
        self._root = ""
        self._active: Engine | None = None
        self._halted = False
        self._launchers = launchers
        self._definitions = definitions
        self._capsules = capsules
        self._cwd = cwd
        self._budget = budget_usd
        self._max_turns = max(0, max_turns)

    def _wall_left(self) -> float | None:
        if self._deadline is None or self._monotonic is None:
            return None
        return max(0.0, self._deadline - self._monotonic())

    def _expired(self) -> bool:
        left = self._wall_left()
        return left is not None and left < WALL_FLOOR_S

    def _past_deadline(self, state: _Pass) -> bool:
        state.walled = self._expired()
        return state.walled

    def _wall_message(self) -> Message:
        return msg("stop.wall_limit", minutes=minutes_text(self._wall_s))

    def _wall_stop(self, state: _Pass, handoff: RoleHandoff | None = None) -> CrossReport:
        if handoff is not None:
            state.handoffs.append(handoff)
        final = CompletionState.PARTIAL if state.steps else CompletionState.FAILED
        return state.report(final, self._wall_message())

    def _walled(self, launch: Launch) -> bool:
        result = launch.outcome.result
        return (result is not None and result.subtype == WALL_LIMIT_SUBTYPE) or self._expired()

    def _unlaunched(self, state: _Pass, role: Role, created: tuple[str, ...]) -> CrossReport:
        self._release(created)
        state.progress.publish(finished(f"cross-{role.value}", Status.SKIP, self._wall_message()))
        return self._wall_stop(state)

    def _spend(self, run: Run) -> float | None:
        return None if run.partial and self._budget > 0 else run.cost_usd

    def _estimate(self, plan: RoutePlan, task_type: str) -> RunEstimate | None:
        if self._estimator is None:
            return None
        return self._estimator(plan, task_type, self._depth)

    def _started(self, run_id: str) -> None:
        self.current = run_id
        self._root = self._root or run_id

    def _save_interrupted(self) -> None:
        if not self._root or self._save_metrics is None:
            return
        state = CompletionState.PARTIAL if self.completed else CompletionState.FAILED
        stopped = message_payload(msg("stop.interrupted"))
        self._save_metrics(self._root, {"completion": state.value, "stopped": stopped})

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
    ) -> ContextPack | None:
        if self._context_pack is None:
            return None
        return self._context_pack(request, self._depth, role.value, protection)

    def _note_anchors(self, state: _Pass, pack: ContextPack) -> None:
        if state.anchors_noted:
            return
        state.anchors_noted = True
        for message in anchor_notes(pack.anchors, pack.budget):
            state.progress.publish(note(Status.WARN, message))

    def _snap(self) -> Mapping[str, str]:
        with self._timing.measure("snapshots_guards", self._timing_run, self._timing_role):
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
        with self._timing.measure("handoff", self._timing_run, self._timing_role):
            return chain if self._lines_of is None else refresh_chain(chain, self._lines_of)

    def _stamp(self, handoff: RoleHandoff) -> RoleHandoff:
        if self._lines_of is None:
            return handoff
        return replace(handoff, facts=refresh_facts(handoff.facts, self._lines_of))

    def _launch(self, turn: _Turn, spec: LaunchSpec, steering: Steering | None = None) -> Launch:
        left = self._wall_left()
        if left == 0.0:
            raise _WallExpired
        spec = replace(spec, role=turn.role.value)
        if left is not None:
            spec = replace(spec, max_wall_s=left, wall_limit_s=self._wall_s)
        engine = turn.launcher.engine
        self._active = engine
        if self._halted:
            engine.cancel()

        def started(run_id: str) -> None:
            self._started(run_id)
            self._timing_run = run_id
            self._timing.flush(run_id)
            if steering is not None:
                steering.started(run_id)

        try:
            if steering is None:
                launch = turn.launcher.launch(
                    self._isolated(spec, turn.engine), lambda _: None, started
                )
            else:
                launch = turn.launcher.launch(
                    replace(self._isolated(spec, turn.engine), steer=True), steering, started
                )
            self._timing_run = launch.run.id
            self._lost_telemetry += launch.unreadable
            if launch.implementation is not None:
                self._implementations[launch.run.id] = launch.implementation
            self._timing.flush(launch.run.id)
            return launch
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
        if self._implementation_profile == "fast":
            raise DomainFailure("fast implementation uses one native Claude launch")
        if self._pure:
            plan = pure_plan(plan, self._model)
        if self._variant and any(
            route.engine != "claude" for route in plan.routes if route.model is not None
        ):
            raise DomainFailure("Claude variants require every role to use Claude")
        self._timing.reset()
        self._timing_run = ""
        self._timing_role = ""
        self._root = ""
        self._lost_telemetry = 0
        self._deadline = (
            self._monotonic() + self._wall_s
            if self._wall_s > 0 and self._monotonic is not None
            else None
        )
        wall_start = self._timing.start()
        if self._refresh_index is not None:
            with self._timing.measure("index_refresh"):
                self._refresh_index()
        with self._timing.measure("forecast_plan"):
            protection = self._change_plan(request) if self._change_plan is not None else None
        before = self._snap()
        try:
            result = self._run(request, plan, progress, protection, before)
            self._timing_run = result.steps[0].run_id if result.steps else ""
            self._timing_role = ""
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
        except KeyboardInterrupt:
            self._save_interrupted()
            raise
        if result.steps and self._learn_run is not None:
            self._learn_run(result.steps[0].run_id)
        if result.steps:
            self._timing.flush(result.steps[0].run_id)
            self._timing.record("run_wall", wall_start, result.steps[0].run_id)
        if self._lost_telemetry > 0:
            progress.publish(note(Status.WARN, unreadable_note(self._lost_telemetry)))
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
            docs_choice(
                self._docs_mode, request, self._sandbox is not None, pinned, self._docs_flag
            )
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
        definitions = self._definitions()
        if self._preflight(request, plan, routed, verify, definitions):
            baseline = self._snap()
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
        for message in plan_notes(request, protection):
            progress.publish(note(Status.WARN, message))
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
        for role in order:
            halt = self._halt(state)
            if halt is not None:
                return halt
            self._timing_run = ""
            self._timing_role = role.value
            with self._timing.measure("forecast_plan", role=role.value):
                turn = self._admit(state, role, definitions)
            if isinstance(turn, CrossReport):
                return turn
            if turn is None:
                continue
            try:
                stop = self._perform(state, turn)
            except _WallExpired:
                return self._wall_stop(state)
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

    def _preflight(
        self,
        request: MandateRequest,
        plan: RoutePlan,
        routed: Sequence[Role],
        verify: tuple[str, ...],
        definitions: Sequence[AgentDefinition],
    ) -> bool:
        if request.type == INVESTIGATION:
            return False
        checked = False
        for role in routed:
            route = plan.route(role)
            if route is None or role in READ_ONLY_ROLES:
                continue
            launcher = self._launchers(route.engine)
            if launcher is None:
                continue
            checked = True
            definition = definition_for(role, definitions)
            body = definition.prompt if definition is not None else ""
            advice = guidance(role, False, verify, no_builds=route.engine in self._build_blocked)
            prompt = "\n\n".join(part for part in (body, request_block(request), advice) if part)
            spec = LaunchSpec(
                kind=CROSS_KIND,
                prompt=prompt,
                cwd=self._cwd,
                allowed_tools=(),
                scope=role.value,
                task_type=request.type,
                depth=self._depth,
                role=role.value,
                profile=self._implementation_profile,
                verify_commands=verify,
            )
            if launcher.preflight(spec):
                return True
        return checked

    def _admit(
        self, state: _Pass, role: Role, definitions: Sequence[AgentDefinition]
    ) -> _Turn | CrossReport | None:
        route = state.plan.route(role)
        if route is None or route.model is None:
            state.progress.publish(note(Status.SKIP, msg("cross.skipped", role=role.value)))
            return None
        if self._expired():
            return self._wall_stop(state)
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
        pack = self._context(state.request, role, effective) if scouted is None else None
        if pack is not None:
            self._note_anchors(state, pack)
        stable, volatile, packed = (
            (pack.stable_prefix, pack.excerpts, True) if pack is not None else ("", "", False)
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
            verify_commands=state.verify if not read_only else (),
            append_system_prompt=implementation_prompt(False, False) if not read_only else "",
            profile=self._implementation_profile,
            variant=self._variant,
            pure=self._pure,
            effort=resolve_variant(self._variant, model).effort if self._variant else "",
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
                implementation_profile=self._implementation_profile,
                variant=self._variant,
                request=state.request,
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
        state.steered = True
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
        prompt = codex_finish_prompt(_diff(state.baseline, self._snap()))
        if self._past_deadline(state):
            steering.settle(SALVAGED)
            return None
        state.progress.publish(note(Status.INFO, msg("governor.resuming", role=role)))
        cost = launch.run.cost_usd
        state.parent = state.parent or launch.run.id
        left = max(turn.role_cap - (cost or 0.0), 0.0)
        spec = replace(
            turn.spec,
            prompt=prompt,
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
            state.walled = state.walled or self._walled(resumed)
            state.charge(resumed.run, self._spend(resumed.run))
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
                    partial=resumed.run.partial,
                )
            )
            state.progress.publish(note(Status.WARN, msg("governor.resume_failed", role=role)))
            return None
        steering.settle(RESUMED)
        state.charge(launch.run, cost)
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
                partial=launch.run.partial,
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
            or self._past_deadline(state)
        ):
            state.progress.publish(
                note(Status.WARN, msg("governor.rotation_skipped", role=turn.role.value))
            )
            steering.settle(SKIPPED, ReactionKind.ROTATE)
            return None
        steering.settle(ROTATED, ReactionKind.ROTATE)
        text, _ = self._capsule(result.text)
        state.charge(launch.run, cost)
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
                partial=launch.run.partial,
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

    def _timed_forecast(self, state: _Pass, turn: _Turn) -> _Turn:
        with self._timing.measure("forecast_plan", role=turn.role.value):
            return self._root_forecast(state, turn)

    def _perform(self, state: _Pass, turn: _Turn) -> CrossReport | None:
        role = turn.role
        chain = self._refresh(merge_chain(state.handoffs))
        hidden = frozenset(self._hidden(turn))
        created = self._prepared(state, turn)
        turn = self._timed_forecast(state, turn)
        if self._expired():
            return self._unlaunched(state, role, created)
        try:
            turn, launch, earlier = self._steered(state, turn)
        except _WallExpired:
            return self._unlaunched(state, role, created)
        self._record_cap(launch.run.id, turn.spec.max_budget_usd, turn.role_cap)
        state.parent = state.parent or launch.run.id
        outcome = launch.outcome
        ok = outcome is not None and outcome.ok
        subtype = outcome.result.subtype if outcome is not None and outcome.result else ""
        text = outcome.result.text if outcome is not None and outcome.result else ""
        cost = launch.run.cost_usd
        state.charge(launch.run, self._spend(launch.run))
        budget_stop = not ok and ("budget" in subtype or subtype == GOVERNOR_STOP_SUBTYPE)
        carried = budget_stop and turn.engine != "claude" and subtype != GOVERNOR_STOP_SUBTYPE
        self._release(created)
        unreadable = self._unreadable(state, turn, hidden)
        overrun = self._overrun(state, turn, cost)
        current = self._snap()
        changed = _diff(state.baseline, current)
        state.baseline = current
        clipped, capsule = self._capsule(text)
        reads = tuple(item for run_id in (*earlier, launch.run.id) for item in self._reads(run_id))
        stopped = subtype if budget_stop and not carried else ""
        with self._timing.measure("handoff", launch.run.id, role.value):
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
            partial=launch.run.partial,
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
        with self._timing.measure("snapshots_guards", launch.run.id, role.value):
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

    def _prepared(self, state: _Pass, turn: _Turn) -> tuple[str, ...]:
        if self._new_files is None or turn.engine != "codex" or turn.read_only:
            return ()
        created = self._new_files.prepare(turn.spec.change_plan)
        if created:
            state.progress.publish(note(Status.INFO, msg("cross.prepared", count=len(created))))
        return created

    def _release(self, created: tuple[str, ...]) -> None:
        if created and self._new_files is not None:
            self._new_files.settle(created)

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
        if subtype == WALL_LIMIT_SUBTYPE or state.walled:
            return self._wall_stop(state, handoff)
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
        with self._timing.measure("verification", self.current, role.value):
            results = (
                self._verifier(state.verify, self._stopped) if self._verifier is not None else ()
            )
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
        implementation = self._implementations.pop(handoff.run_id, None)
        if implementation is not None:
            for attempt, check in enumerate(implementation.checks, 1):
                found = VerificationRound(turn.role, attempt, check.results, delta=check)
                state.rounds.append(found)
                state.last_round = found
            if implementation.checks:
                handoff = replace(handoff, verification=implementation.checks[-1].results)
            if not implementation.passed:
                state.handoffs.append(handoff)
                return handoff, state.report(
                    CompletionState.FAILED, msg("cross.verify_unresolved", role=turn.role.value)
                )
            return handoff, self._guarded(state, handoff) or self._halt(state, handoff)
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
        uncapped = self._budget <= 0
        if found.passed or not ok or (not uncapped and left_share <= REPAIR_MINIMUM_USD):
            return handoff, None
        hidden = frozenset(self._hidden(turn))
        if self._expired():
            return handoff, self._wall_stop(state, handoff)
        state.repair_usd = 0.0
        state.progress.publish(note(Status.WARN, msg("cross.repair", role=turn.role.value)))
        repair = replace(
            turn.spec,
            prompt=repair_prompt(turn.spec.prompt, found.results),
            max_budget_usd=(
                0.0
                if uncapped
                else native_cap(left_share, state.margins.usd(turn.model))
                if turn.engine == "claude"
                else left_share
            ),
            parent_id=state.parent,
            estimate=None,
            pipeline_budget_usd=0.0,
        )
        repair_start = self._timing.start()
        fixed = self._launch(turn, repair)
        self._timing.record("repair", repair_start, fixed.run.id, turn.role.value)
        self._record_cap(fixed.run.id, repair.max_budget_usd, left_share)
        state.charge(fixed.run, self._spend(fixed.run))
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
                0.0 if uncapped else left_share,
                repair.max_budget_usd,
                overrun,
                repair=True,
                changed_files=changed,
                unreadable_files=unreadable,
                index_tools=repair.index_tools is True,
                read_discipline=_discipline(turn),
                partial=fixed.run.partial,
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
        if self._walled(fixed):
            return handoff, self._wall_stop(state, handoff)
        found = self._verify(state, turn.role, 2)
        handoff = replace(handoff, verification=found.results)
        stop = self._guarded(state, handoff) or self._halt(state, handoff)
        if stop is not None:
            return handoff, stop
        if not found.passed:
            state.progress.publish(note(Status.WARN, msg("cross.verify_handed")))
        return handoff, None
