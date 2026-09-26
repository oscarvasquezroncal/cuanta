from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace

from cuanta.application.run_reports import RunReports
from cuanta.domain.engine import (
    TURN_LIMIT_SUBTYPE,
    EngineEvent,
    EngineOutcome,
    EngineRequest,
    RunResult,
    cut_by_turns,
)
from cuanta.domain.ids import trace_id_of, traceparent
from cuanta.domain.ledger import LedgerEvent, Run
from cuanta.domain.messages import msg
from cuanta.domain.overhead import spawn_event
from cuanta.domain.plugins import FULL, LEAN
from cuanta.domain.pricing import CostEstimate, PriceTable, estimate, estimate_cost
from cuanta.domain.telemetry import claude_env, run_env
from cuanta.ports.engine import Engine
from cuanta.ports.ledger import Ledger
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


@dataclass(frozen=True, slots=True)
class Launch:
    run: Run
    outcome: EngineOutcome


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
    ) -> None:
        self._lean_files = lean_files
        self._default_session = default_session
        self._reports = reports
        self._engine = engine
        self._ledger = ledger
        self._clock = clock
        self._new_run_id = new_run_id
        self._entropy = entropy
        self._project = project_name
        self._port = port
        self._listener = listener
        self._prices = prices

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

    def request(
        self, spec: LaunchSpec, run_id: str, parent: str, port: int | None
    ) -> EngineRequest:
        env = run_env(run_id, parent)
        if port is not None and self._engine.name == "claude":
            env.update(claude_env(port, self._project, run_id, parent))
        mcp_config, settings_file = "", ""
        session = spec.session or self._default_session
        if session == LEAN and self._engine.name == "claude" and self._lean_files is not None:
            mcp_config, settings_file = self._lean_files()
        return EngineRequest(
            prompt=spec.prompt,
            cwd=spec.cwd,
            env=env,
            allowed_tools=spec.allowed_tools,
            disallowed_tools=spec.disallowed_tools,
            model=spec.model,
            max_budget_usd=spec.max_budget_usd,
            agents_file=spec.agents_file,
            effort=spec.effort,
            append_system_prompt=spec.append_system_prompt,
            unset_env=spec.unset_env,
            mcp_config=mcp_config,
            settings_file=settings_file,
            tools=spec.tools,
            max_turns=spec.max_turns,
            stable_prefix=spec.stable_prefix,
            persist_session=spec.persist_session,
            read_only=spec.read_only,
            temporary_copy=spec.temporary_copy,
        )

    @contextmanager
    def _telemetry(self) -> Iterator[ListenerStatus | None]:
        if self._listener is None:
            with nullcontext(None) as nothing:
                yield nothing
            return
        with self._listener.scoped(self._port) as status:
            yield status

    def launch(
        self,
        spec: LaunchSpec,
        on_event: Callable[[EngineEvent], None],
        before: Callable[[str], None] | None = None,
    ) -> Launch:
        run_id = spec.run_id or self._new_run_id()
        parent = traceparent(self._entropy(16), self._entropy(8))
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
        )
        self._ledger.add_run(run)
        if before is not None:
            before(run_id)
        outcome: EngineOutcome | None = None
        spawned: LedgerEvent | None = None

        def stream_event(event: EngineEvent) -> None:
            if not isinstance(event, RunResult):
                on_event(event)

        try:
            with self._telemetry() as status:
                port = status.port if status is not None and status.running else None
                request = self.request(spec, run_id, parent, port)
                spawned = spawn_event(run_id, run.trace_id, self._clock.now_ms())
                outcome = self._engine.run(request, stream_event)
        finally:
            if spawned is not None:
                self._ledger.add_events([spawned])
            usage = _usage_events(run, outcome, self._engine.name) if outcome else []
            cost = self._cost(outcome, usage)
            if (
                outcome is not None
                and outcome.result is not None
                and spec.max_budget_usd > 0
                and cost.value is not None
                and cost.value > spec.max_budget_usd
                and outcome.ok
            ):
                outcome = replace(
                    outcome,
                    result=replace(
                        outcome.result,
                        ok=False,
                        subtype="error_max_budget_usd",
                        terminal_reason="budget_exhausted",
                    ),
                )
            status_text = "interrupted" if outcome is None else ("ok" if outcome.ok else "failed")
            result = outcome.result if outcome is not None else None
            end_reason = "" if result is None else result.subtype
            if result is not None and cut_by_turns(result.subtype, result.terminal_reason):
                end_reason = TURN_LIMIT_SUBTYPE
            finished = replace(
                run,
                ended_at=self._clock.now_iso(),
                status=status_text,
                model=_main_model(outcome) or run.model,
                turns=result.num_turns if result is not None else 0,
                end_reason=end_reason,
            )
            usage = _usage_events(finished, outcome, self._engine.name) if outcome else []
            finished = replace(finished, cost_usd=cost.value, cost_source=cost.kind)
            self._ledger.update_run(finished)
            if usage:
                self._ledger.add_events(usage)
            text = outcome.result.text if outcome and outcome.result else ""
            if text and self._reports is not None:
                self._reports.save_report(run_id, text)
            if result is not None:
                on_event(result)
        return Launch(run=finished, outcome=outcome)


def _reported(outcome: EngineOutcome | None) -> float | None:
    return outcome.cost_usd if outcome is not None else None


def _main_model(outcome: EngineOutcome | None) -> str:
    if outcome is None or outcome.result is None or not outcome.result.models:
        return ""
    return max(outcome.result.models, key=lambda usage: usage.total).model


def _usage_events(run: Run, outcome: EngineOutcome, engine: str) -> list[LedgerEvent]:
    if outcome.result is None:
        return []
    return [
        LedgerEvent(
            run_id=run.id,
            source=f"{engine}_stream",
            session_id=outcome.result.session_id,
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
        for usage in outcome.result.models
    ]
