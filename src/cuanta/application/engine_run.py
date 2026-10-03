from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from types import TracebackType

from cuanta.application.implementer import ImplementationSession
from cuanta.application.run_reports import RunReports
from cuanta.application.timing import PhaseRecorder
from cuanta.domain.change_plan import EXECUTION, ChangePlan, deny_rules, strict_tools
from cuanta.domain.claude_variants import pure_environment
from cuanta.domain.engine import (
    CANCELLED_SUBTYPE,
    GOVERNOR_STOP_SUBTYPE,
    RAISED_SUBTYPE,
    TURN_LIMIT_SUBTYPE,
    WALL_LIMIT_SUBTYPE,
    EngineEvent,
    EngineOutcome,
    EngineRequest,
    ModelUsage,
    RunResult,
    cut_by_turns,
)
from cuanta.domain.errors import DomainFailure
from cuanta.domain.estimates import RunEstimate
from cuanta.domain.ids import trace_id_of, traceparent
from cuanta.domain.implementation import ImplementationProfile, ImplementationReport
from cuanta.domain.ledger import LedgerEvent, Run
from cuanta.domain.messages import msg
from cuanta.domain.overhead import spawn_event
from cuanta.domain.plugins import FULL, LEAN
from cuanta.domain.pricing import CostEstimate, PriceTable, estimate, estimate_cost
from cuanta.domain.received import Received, ended_early, received_cost
from cuanta.domain.telemetry import UNREADABLE_KIND, claude_env, run_env
from cuanta.ports.engine import Engine, Resumable, RunStop, TurnInput
from cuanta.ports.ledger import EventQuery, Ledger
from cuanta.ports.listener import ListenerControl, ListenerStatus
from cuanta.ports.system import Clock

DEFAULT_DENIED = ("Bash(git *)",)


@dataclass(frozen=True, slots=True)
class LaunchSpec:
    kind: str
    prompt: str
    cwd: str
    allowed_tools: tuple[str, ...]
    disallowed_tools: tuple[str, ...] = DEFAULT_DENIED
    model: str = ""
    max_budget_usd: float = 0.0
    hu_ref: str = ""
    scope: str = ""
    parent_id: str = ""
    agents_file: str = ""
    effort: str = ""
    append_system_prompt: str = ""
    unset_env: tuple[str, ...] = ()
    run_id: str = ""
    session: str = ""
    task_type: str = ""
    depth: str = ""
    tools: tuple[str, ...] | None = None
    max_turns: int = 0
    stable_prefix: bool = False
    persist_session: bool = True
    read_only: bool = False
    temporary_copy: bool = False
    mode: str = ""
    index_tools: bool | None = None
    env: tuple[tuple[str, str], ...] = ()
    estimate: RunEstimate | None = None
    shape: str = ""
    pipeline_budget_usd: float = 0.0
    change_plan: ChangePlan | None = None
    steer: bool = False
    resume_session: str = ""
    read_discipline: bool | None = None
    role: str = ""
    profile: str = "balanced"
    variant: str = ""
    pure: bool = False
    verify_commands: tuple[str, ...] = ()
    implementation_steps: bool = False
    max_wall_s: float = 0.0
    wall_limit_s: float = 0.0


@dataclass(frozen=True, slots=True)
class Launch:
    run: Run
    outcome: EngineOutcome
    implementation: ImplementationReport | None = None
    unreadable: int = 0


class WallTimer:
    def __init__(self, seconds: float, expire: Callable[[], None]) -> None:
        self._timer: threading.Timer | None = None
        if seconds > 0:
            self._timer = threading.Timer(min(seconds, threading.TIMEOUT_MAX), expire)
            self._timer.daemon = True

    def __enter__(self) -> WallTimer:
        if self._timer is not None:
            self._timer.start()
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.disarm()

    def disarm(self) -> None:
        if self._timer is not None:
            self._timer.cancel()


class EngineLauncher:
    def __init__(
        self,
        engine: Engine,
        ledger: Ledger,
        clock: Clock,
        new_run_id: Callable[[], str],
        entropy: Callable[[int], bytes],
        project_name: str,
        port: int,
        listener: ListenerControl | None,
        prices: PriceTable | None = None,
        reports: RunReports | None = None,
        lean_files: Callable[[], tuple[str, str]] | None = None,
        default_session: str = FULL,
        guard_files: Callable[[LaunchSpec], tuple[str, str]] | None = None,
        index_tools: bool = False,
        index_server: tuple[str, ...] = (),
        implementer: Callable[[LaunchSpec], ImplementationSession | None] | None = None,
    ) -> None:
        self._index_server = index_server
        self._implementer = implementer
        self._lean_files = lean_files
        self._guard_files = guard_files
        self._index_tools = index_tools
        self._default_session = default_session
        self._reports = reports
        self._engine = engine
        self._ledger = ledger
        self._clock = clock
        self._timing = PhaseRecorder(clock, ledger)
        self._new_run_id = new_run_id
        self._entropy = entropy
        self._project = project_name
        self._port = port
        self._listener = listener
        self._prices = prices
        self._halt_estimate: Callable[[], float | None] | None = None
        self._sent = 0

    def _cost(self, outcome: EngineOutcome | None, usage: list[LedgerEvent]) -> CostEstimate:
        reported = _reported(outcome)
        if reported is not None:
            return estimate(reported, msg("cost.engine"), kind="reported")
        if not usage:
            return estimate(None, msg("cost.missing_usage"))
        return estimate_cost(usage, self._prices)

    @property
    def engine(self) -> Engine:
        return self._engine

    @property
    def timing(self) -> PhaseRecorder:
        return self._timing

    def steerable(self) -> bool:
        engine = self._engine
        return isinstance(engine, TurnInput) and engine.accepts_turns()

    def _implementation(self, spec: LaunchSpec) -> ImplementationSession | None:
        if self._implementer is None or spec.kind not in {"mandate", "cross"} or spec.read_only:
            return None
        if self.steerable():
            return self._implementer(spec)
        if spec.implementation_steps or (
            spec.kind == "mandate" and spec.profile == ImplementationProfile.FAST
        ):
            name = self._engine.name
            raise DomainFailure(
                f"{name} cannot take follow-up turns in one session, which fast and ordered "
                "implementation need",
                f"upgrade {name} so its help lists --input-format, or use the balanced profile",
            )
        return None

    def preflight(self, spec: LaunchSpec) -> bool:
        return self._implementation(spec) is not None

    def send_turn(self, text: str) -> bool:
        engine = self._engine
        sent = isinstance(engine, TurnInput) and engine.send_turn(text)
        if sent:
            self._sent += 1
        return sent

    def resumable(self) -> bool:
        engine = self._engine
        return isinstance(engine, Resumable) and engine.resumable()

    def halt(self, estimate: Callable[[], float | None]) -> bool:
        engine = self._engine
        if not isinstance(engine, RunStop):
            return False
        self._halt_estimate = estimate
        engine.halt(GOVERNOR_STOP_SUBTYPE)
        return True

    def _received(
        self, run_id: str, outcome: EngineOutcome | None, rows: list[LedgerEvent]
    ) -> CostEstimate:
        recorded = self._ledger.events(EventQuery(run_id=run_id))
        return received_cost(_reported(outcome), recorded, rows, self._prices)

    def _halted_cost(self, outcome: EngineOutcome | None, cost: CostEstimate) -> CostEstimate:
        guess = self._halt_estimate
        self._halt_estimate = None
        result = outcome.result if outcome is not None else None
        if guess is None or result is None or result.subtype != GOVERNOR_STOP_SUBTYPE:
            return cost
        value = guess()
        if value is None:
            return cost
        return estimate(max(value, cost.value or 0.0), msg("cost.governor_stop"), kind="estimated")

    def _steered(self, request: EngineRequest, spec: LaunchSpec) -> EngineRequest:
        return replace(request, stream_input=True) if spec.steer and self.steerable() else request

    def request(
        self, spec: LaunchSpec, run_id: str, parent: str, port: int | None
    ) -> EngineRequest:
        env = run_env(run_id, parent)
        if port is not None and self._engine.name == "claude":
            env.update(claude_env(port, self._project, run_id, parent))
        env.update(dict(spec.env))
        if spec.pure:
            env.update(pure_environment(spec.model))
        mcp_config, settings_file = "", ""
        session = spec.session or self._default_session
        plan = spec.change_plan or (ChangePlan(read_only=True) if spec.read_only else None)
        strict = plan is not None and (plan.read_only or bool(plan.guard))
        indexed = self._index_tools if spec.index_tools is None else spec.index_tools
        own_profile = self.owns_profile(spec)
        if own_profile and self._guard_files is not None:
            mcp_config, settings_file = self._guard_files(spec)
        elif session == LEAN and self._engine.name == "claude" and self._lean_files is not None:
            mcp_config, settings_file = self._lean_files()
        denied = spec.disallowed_tools
        tools = spec.tools
        allowed = spec.allowed_tools
        if strict and plan is not None and self._engine.name == "claude":
            delegation = bool(spec.agents_file) or bool({"Agent", "Task"} & set(allowed))
            tools = tuple(
                tool for tool in strict_tools(plan) if delegation or tool not in {"Agent", "Task"}
            )
            if spec.tools is not None:
                tools = tuple(tool for tool in tools if tool in spec.tools)
            allowed = tools
            denied = tuple(dict.fromkeys((*denied, *deny_rules(plan), *EXECUTION)))
        system = spec.append_system_prompt
        if own_profile and indexed:
            from cuanta.domain.index_tools import INDEX_CONTRACT, INDEX_TOOLS

            allowed = tuple(dict.fromkeys((*allowed, *INDEX_TOOLS)))
            system = "\n\n".join(part for part in (system, INDEX_CONTRACT) if part)
        return EngineRequest(
            prompt=spec.prompt,
            cwd=spec.cwd,
            env=env,
            allowed_tools=allowed,
            disallowed_tools=denied,
            model=spec.model,
            max_budget_usd=spec.max_budget_usd,
            agents_file=spec.agents_file,
            effort=spec.effort,
            append_system_prompt=system,
            unset_env=spec.unset_env,
            mcp_config=mcp_config,
            settings_file=settings_file,
            tools=tools,
            max_turns=spec.max_turns,
            stable_prefix=spec.stable_prefix,
            persist_session=spec.persist_session,
            read_only=spec.read_only or (plan is not None and plan.read_only),
            temporary_copy=spec.temporary_copy,
            setting_sources=() if own_profile else None,
            strict_guard=strict and self._engine.name == "claude",
            index_server=(
                (*self._index_server, "--run-id", run_id)
                if indexed and self._index_server and self._engine.name == "codex"
                else ()
            ),
            resume_session=spec.resume_session,
            pure=spec.pure,
            variant=spec.variant,
        )

    def owns_profile(self, spec: LaunchSpec) -> bool:
        session = spec.session or self._default_session
        plan = spec.change_plan or (ChangePlan(read_only=True) if spec.read_only else None)
        strict = plan is not None and (plan.read_only or bool(plan.guard))
        return (
            self._engine.name == "claude"
            and self._guard_files is not None
            and (
                strict
                or spec.pure
                or bool(spec.variant)
                or (session == LEAN and spec.kind in {"mandate", "cross"})
                or (spec.index_tools is True and spec.kind == "cross")
                or (spec.read_discipline is True and spec.kind == "cross")
            )
        )

    @contextmanager
    def _telemetry(self) -> Iterator[ListenerStatus | None]:
        if self._listener is None:
            with nullcontext(None) as nothing:
                yield nothing
            return
        with self._listener.scoped(self._port) as status:
            yield status

    def _save_metadata(self, run_id: str, spec: LaunchSpec) -> None:
        if spec.role:
            self._ledger.add_events(
                [
                    LedgerEvent(
                        run_id=run_id,
                        source="cuanta",
                        agent=spec.role,
                        kind="run_role",
                        ts=self._clock.now_iso(),
                    )
                ]
            )
        if self._reports is None:
            return
        meta = self._reports.meta(run_id) or {}
        meta["variant"] = spec.variant or spec.effort or None
        meta["implementation_profile"] = spec.profile
        meta["pure"] = spec.pure
        if spec.shape or spec.kind == "cross":
            meta["shape"] = spec.shape or "pipeline"
        if spec.estimate is not None:
            meta["estimate_factor"] = spec.estimate.factor
        self._reports.save_meta(run_id, meta)

    def launch(
        self,
        spec: LaunchSpec,
        on_event: Callable[[EngineEvent], None],
        before: Callable[[str], None] | None = None,
    ) -> Launch:
        run_id = spec.run_id or self._new_run_id()
        parent = traceparent(self._entropy(16), self._entropy(8))
        self._halt_estimate = None
        guess = spec.estimate
        run = Run(
            id=run_id,
            kind=spec.kind,
            engine=self._engine.name,
            model=spec.model,
            started_at=self._clock.now_iso(),
            status="running",
            trace_id=trace_id_of(parent),
            hu_ref=spec.hu_ref,
            scope=spec.scope,
            prompt_hash=hashlib.sha256(spec.prompt.encode("utf-8")).hexdigest()[:16],
            parent_id=spec.parent_id,
            task_type=spec.task_type,
            depth=spec.depth,
            max_turns=spec.max_turns,
            mode=spec.mode,
            estimate_low=guess.low if guess else None,
            estimate_high=guess.high if guess else None,
            estimate_source=guess.source if guess else "",
            estimate_samples=guess.samples if guess else 0,
            cap_usd=spec.pipeline_budget_usd or spec.max_budget_usd,
            max_wall_s=max(0.0, spec.wall_limit_s or spec.max_wall_s),
        )
        self._ledger.add_run(run)
        outcome: EngineOutcome | None = None
        spawned: LedgerEvent | None = None
        implementation: ImplementationSession | None = None
        received = Received()
        wall = WallTimer(spec.max_wall_s, self._expire)
        raised = False
        self._sent = 0

        def stream_event(event: EngineEvent) -> None:
            received.add(event)
            if not isinstance(event, RunResult):
                on_event(event)
                return
            submitted = self._sent
            if implementation is not None:
                implementation.on_result(event, self.send_turn)
            if self._sent == submitted:
                wall.disarm()

        try:
            self._save_metadata(run_id, spec)
            if before is not None:
                before(run_id)
            with self._telemetry() as status:
                implementation = self._implementation(replace(spec, run_id=run_id))
                port = status.port if status is not None and status.running else None
                request = self._steered(self.request(spec, run_id, parent, port), spec)
                if implementation is not None:
                    request = replace(request, stream_input=True, continue_results=True)
                spawned = spawn_event(run_id, run.trace_id, self._clock.now_ms())
                outcome = self._run_engine(
                    request, stream_event, implementation, run_id, spec.role, wall
                )
        except Exception:
            raised = True
            raise
        finally:
            if spawned is not None:
                self._ledger.add_events([spawned])
            final, finished = self._close(run, spec, outcome, received, raised)
            result = final.result if final is not None else None
            text = result.text if result is not None else ""
            if text and self._reports is not None:
                self._reports.save_report(run_id, text)
            if result is not None:
                on_event(result)
        return Launch(
            run=finished,
            outcome=final or outcome,
            implementation=implementation.report if implementation is not None else None,
            unreadable=self._unreadable(run_id),
        )

    def _unreadable(self, run_id: str) -> int:
        return len(self._ledger.events(EventQuery(run_id=run_id, kind=UNREADABLE_KIND)))

    def _close(
        self,
        run: Run,
        spec: LaunchSpec,
        outcome: EngineOutcome | None,
        received: Received,
        raised: bool = False,
    ) -> tuple[EngineOutcome | None, Run]:
        early = ended_early(outcome)
        result = outcome.result if outcome is not None else None
        models = (
            received.usage
            if early and received.requests
            else (result.models if result is not None else ())
        )
        session = result.session_id if result is not None and result.session_id else ""
        session = session or received.session_id
        rows = _usage_rows(run, models, session, self._engine.name)
        cost = self._received(run.id, outcome, rows) if early else self._cost(outcome, rows)
        cost = self._halted_cost(outcome, cost)
        outcome = _over_cap(outcome, spec, cost)
        result = outcome.result if outcome is not None else None
        turns = result.num_turns if result is not None else 0
        finished = replace(
            run,
            ended_at=self._clock.now_iso(),
            status=_status(outcome, raised),
            model=_main_model(outcome) or (received.main_model if early else "") or run.model,
            turns=max(turns, received.turns) if early else turns,
            end_reason=RAISED_SUBTYPE if outcome is None and raised else _end_reason(outcome),
            cost_usd=cost.value,
            cost_source=cost.kind,
            partial=early,
        )
        stored = [
            *_usage_rows(finished, models, session, self._engine.name),
            *(_denial_events(finished, outcome, self._engine.name) if outcome else []),
        ]
        self._ledger.update_run(finished)
        if stored:
            self._ledger.add_events(stored)
        return outcome, finished

    def _expire(self) -> None:
        engine = self._engine
        if isinstance(engine, RunStop):
            engine.halt(WALL_LIMIT_SUBTYPE)
        else:
            engine.cancel()

    def _run_engine(
        self,
        request: EngineRequest,
        on_event: Callable[[EngineEvent], None],
        implementation: ImplementationSession | None,
        run_id: str,
        role: str,
        wall: WallTimer,
    ) -> EngineOutcome:
        with wall:
            outcome = self._engine.run(request, on_event)
        if implementation is not None:
            outcome = implementation.settle(outcome)
            if self._reports is not None:
                meta = self._reports.meta(run_id) or {}
                self._reports.save_meta(
                    run_id, {**meta, "implementation": implementation.report.payload()}
                )
        if outcome.startup_seconds is not None:
            self._timing.seconds("engine_startup", outcome.startup_seconds, run_id, role)
        return outcome


def _reported(outcome: EngineOutcome | None) -> float | None:
    return outcome.cost_usd if outcome is not None else None


def _main_model(outcome: EngineOutcome | None) -> str:
    if outcome is None or outcome.result is None or not outcome.result.models:
        return ""
    return max(outcome.result.models, key=lambda usage: usage.total).model


def _over_cap(
    outcome: EngineOutcome | None, spec: LaunchSpec, cost: CostEstimate
) -> EngineOutcome | None:
    if (
        outcome is None
        or outcome.result is None
        or spec.max_budget_usd <= 0
        or cost.value is None
        or cost.value <= spec.max_budget_usd
        or not outcome.ok
    ):
        return outcome
    return replace(
        outcome,
        result=replace(
            outcome.result,
            ok=False,
            subtype="error_max_budget_usd",
            terminal_reason="budget_exhausted",
        ),
    )


def _status(outcome: EngineOutcome | None, raised: bool) -> str:
    if outcome is None:
        return "failed" if raised else "interrupted"
    return "ok" if outcome.ok else "failed"


def _end_reason(outcome: EngineOutcome | None) -> str:
    if outcome is not None and outcome.cancelled and not outcome.ok:
        return CANCELLED_SUBTYPE
    result = outcome.result if outcome is not None else None
    if result is not None and cut_by_turns(result.subtype, result.terminal_reason):
        return TURN_LIMIT_SUBTYPE
    return result.subtype if result is not None else ""


def _usage_rows(
    run: Run, models: tuple[ModelUsage, ...], session: str, engine: str
) -> list[LedgerEvent]:
    return [
        LedgerEvent(
            run_id=run.id,
            source=f"{engine}_stream",
            session_id=session,
            trace_id=run.trace_id,
            kind="result_usage",
            model=usage.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            cost_usd=usage.cost_usd,
            ts=run.ended_at,
        )
        for usage in models
    ]


def _denial_events(run: Run, outcome: EngineOutcome, engine: str) -> list[LedgerEvent]:
    if outcome.result is None:
        return []
    return [
        LedgerEvent(
            run_id=run.id,
            source=f"{engine}_stream",
            session_id=outcome.result.session_id,
            trace_id=run.trace_id,
            kind="permission_denied",
            tool_name=denial.tool_name,
            tool_use_id=denial.tool_use_id,
            file_path=denial.path,
            success=False,
            ts=run.ended_at,
        )
        for denial in outcome.result.permission_denials
    ]
