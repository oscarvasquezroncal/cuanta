from __future__ import annotations

import os
import secrets
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from cuanta.application.assistant import Improvement
from cuanta.application.bench import BenchResult
from cuanta.application.cat_capsule import CapsuleView
from cuanta.application.cross_engine import (
    TEAM_ROLES,
    CrossEnginePipeline,
    CrossReport,
    request_block,
)
from cuanta.application.doctor import DoctorReport
from cuanta.application.estimate import Estimate
from cuanta.application.forecast import forecast_failure
from cuanta.application.home import HomeSnapshot
from cuanta.application.init_project import InitOptions, InitReport
from cuanta.application.instinct_view import (
    BACKENDS,
    BackendStatus,
    JevCard,
    ProbeRow,
    calibration,
    preview_state,
    probe,
)
from cuanta.application.intake import Understanding
from cuanta.application.loop import FixLoop, FixStep, LoopReport, TestStep
from cuanta.application.mandate import MandateReport
from cuanta.application.mandate_flow import (
    MandateFlow,
    MandateOptions,
    MandatePreview,
    MandateSetup,
    launch_turns,
    per_role_run,
    preview_of,
    resolve_budget,
)
from cuanta.application.map import MapFile, MapQuery, MapStatus
from cuanta.application.models import CatalogView, ProbeOutcome
from cuanta.application.new_files import NewFilePair
from cuanta.application.results import ResultQuery, ResultView, RunFile
from cuanta.application.routing import RoleStats, RoutePlan, role_stats
from cuanta.application.spectrum import ALL_SESSIONS, Selection, SpectrumResult
from cuanta.application.tests_view import TestsSummary, from_report
from cuanta.domain.assistant import Clarity, Suggestions, content_key
from cuanta.domain.cache import PrefixWindow
from cuanta.domain.calibration import Calibration
from cuanta.domain.capsules import Level
from cuanta.domain.change_plan import ChangePlan, apply_overrides
from cuanta.domain.code_index import SearchHit
from cuanta.domain.config import Config
from cuanta.domain.drafts import Draft
from cuanta.domain.engine import EngineEvent
from cuanta.domain.errors import CuantaError, DomainFailure, NotAvailable
from cuanta.domain.fixes import Fix, FixAction
from cuanta.domain.handoff import Handoff, parse_workflow
from cuanta.domain.instinct import Choice
from cuanta.domain.ledger import Decision, Run
from cuanta.domain.loop import LOOP_OUT_OF_SCOPE, LoopGate, loop_gate
from cuanta.domain.mandate import MandateRequest, Shape, parse_shape, single_context
from cuanta.domain.messages import Message, english, msg
from cuanta.domain.models import ModelEntry
from cuanta.domain.progress import ProgressEvent
from cuanta.domain.real_costs import CostReport
from cuanta.domain.routing import RoutingPolicy, parse_provider
from cuanta.domain.scout import ShapeChoice
from cuanta.domain.team import (
    ProviderAdvice,
    RoleCard,
    provider_attempts,
    recommend_provider,
    team_cards,
)
from cuanta.domain.telemetry import WiringPlan, WiringReport
from cuanta.domain.terminal import TerminalReport

EventSink = Callable[[EngineEvent], None]
ProgressCallback = Callable[[ProgressEvent], None]
ENGINE_NAMES = ("claude", "codex", "opencode")
ALL_IMPORTED = "*imported*"

PREVIEW_WHAT = "fix the login timeout when the token expires"
PREVIEW_WHERE = "src/auth/session.py"
PREVIEW_TYPE = "bug"

if TYPE_CHECKING:
    from cuanta.application.trials import TrialStore
    from cuanta.bootstrap import Container


def cross_report(run: Run | None, report: CrossReport) -> MandateReport:
    if run is None:
        raise NotAvailable("the cross-engine run did not start", "check the ledger")
    return MandateReport(
        run=replace(run, cost_usd=report.spent_usd),
        ok=report.ok,
        changed_files=report.changed_files,
        tests=english(msg(f"completion.{report.state.value}")),
        tokens_by_agent={},
        utilization=None,
        handoffs=tuple(step.handoff for step in report.steps),
        tool_calls=0,
        hint=Choice("single", 1.0),
        run_file="",
        text=english(report.stopped) if report.stopped is not None else "",
        task_type=run.task_type,
        change_plan=report.change_plan,
    )


def role_plan(container: Container, request: MandateRequest, options: MandateOptions) -> RoutePlan:
    route = options.route
    roles = dict(route.role_models)
    engine = options.engine or container.config.engine
    plan, _ = container.plan_route(
        request.type,
        request.what,
        request.where,
        route.mode,
        route.preset,
        roles,
        clarity=route.clarity,
        depth=options.depth,
        scope=route.scope,
        risk=route.risk,
        engine=engine,
    )
    plan = container.shape_plan(
        plan, ShapeChoice(parse_shape(options.shape)), container.docs_choice(request, options)
    )
    issues = container.route_advisor(container.shared_ledger()).pin_issues(plan, roles, (engine,))
    if issues:
        raise DomainFailure(
            english(msg("route.pins_rejected")), "; ".join(english(item) for item in issues)
        )
    return plan


def role_preview(request: MandateRequest, plan: RoutePlan, engine: str) -> MandatePreview:
    return MandatePreview(
        prompt=request_block(request),
        command="",
        engine=engine,
        scope="",
        confidence=0.0,
        roles=tuple(
            route for route in plan.routes if route.model is not None and route.role in TEAM_ROLES
        ),
        per_role=True,
    )


@dataclass(frozen=True, slots=True)
class TelemetryPanel:
    listener_running: bool
    port: int
    written: int
    engines: tuple[tuple[WiringReport, WiringPlan], ...]


@dataclass(frozen=True, slots=True)
class LoopState:
    gate: LoopGate
    tier: str
    max_iterations: int
    budget_usd: float


class Services(Protocol):
    @property
    def project(self) -> Path: ...

    def home(self) -> HomeSnapshot: ...

    def prefix_window(self, engine: str) -> PrefixWindow: ...

    def latest_tests(self) -> TestsSummary | None: ...

    def run_tests(self) -> TestsSummary: ...

    def capsule(self, reference: str, level: Level) -> CapsuleView: ...

    def doctor(self) -> DoctorReport: ...

    def apply_fix(self, fix: Fix) -> str: ...

    def copy(self, text: str) -> bool: ...

    def mandate_setup(self) -> MandateSetup: ...

    def failure_evidence(self) -> tuple[str, int]: ...

    def read_evidence(self, path: str) -> str: ...

    def preview_mandate(
        self, request: MandateRequest, signatures: int, options: MandateOptions
    ) -> MandatePreview: ...

    def run_mandate(
        self,
        request: MandateRequest,
        signatures: int,
        options: MandateOptions,
        observer: EventSink,
        progress: ProgressCallback,
    ) -> MandateReport: ...

    def stop_mandate(self) -> bool: ...

    def result_view(self, run_id: str) -> ResultView | None: ...

    def trial_handoff(self, run_id: str) -> Handoff | None: ...

    def apply_trial(self, run_id: str) -> int: ...

    def discard_trial(self, run_id: str) -> None: ...

    def accept_run(self, run_id: str) -> None: ...

    def reject_run(self, run_id: str) -> None: ...

    def real_costs(self) -> CostReport: ...

    def save_result(self, run_id: str) -> str: ...

    def export_result(self, run_id: str) -> str: ...

    def result_file(self, run_id: str, path: str) -> RunFile: ...

    def recent_runs(self) -> tuple[Run, ...]: ...

    def spectrum(self, run_id: str) -> SpectrumResult: ...

    def map_status(self, rebuild: bool = False) -> MapStatus: ...

    def map_search(self, query: str) -> tuple[SearchHit, ...]: ...

    def map_file(self, path: str) -> MapFile: ...

    def map_revalidate(self) -> MapStatus: ...

    def import_sessions(self) -> dict[str, int]: ...

    def reindex_graph(self) -> tuple[bool, str]: ...

    def export_ledger(
        self, fmt: str, table: str, path: str, include_raw: bool
    ) -> tuple[str, int]: ...

    def telemetry_plan(self, engine: str) -> tuple[WiringPlan, ...]: ...

    def run_init(
        self,
        options: InitOptions,
        consent: bool,
        budget_usd: float | None,
        progress: ProgressCallback,
    ) -> InitReport: ...

    def load_new_file(self, path: str) -> NewFilePair: ...

    def resolve_new_file(self, path: str, keep_mine: bool) -> str: ...

    def telemetry_panel(self) -> TelemetryPanel: ...

    def set_telemetry(self, engine: str, on: bool) -> tuple[WiringReport, ...]: ...

    def listener_start(self) -> int: ...

    def listener_stop(self) -> bool: ...

    def instinct_overview(
        self,
    ) -> tuple[tuple[BackendStatus, ...], tuple[Decision, ...]]: ...

    def use_backend(self, name: str, consent: bool) -> None: ...

    def probe_instinct(self) -> list[ProbeRow]: ...

    def instinct_calibration(self) -> tuple[Calibration, ...]: ...

    def settings(self) -> Config: ...

    def models_view(self, refresh: bool) -> CatalogView: ...

    def set_model_tier(self, model: str, tier: str) -> ModelEntry: ...

    def probe_model(self, model: str, spend: bool) -> ProbeOutcome: ...

    def routing_policy(self) -> RoutingPolicy: ...

    def build_warning(self, engine: str) -> Message | None: ...

    def save_routing(self, values: Mapping[str, object]) -> None: ...

    def routing_stats(self) -> tuple[RoleStats, ...]: ...

    def jev_card(self, test: bool) -> JevCard: ...

    def latest_bench(self) -> BenchResult | None: ...

    def instinct_preview(self) -> str: ...

    def assistant_check(self, request: MandateRequest) -> Clarity: ...

    def assistant_preview(self, request: MandateRequest) -> str: ...

    def suggestions(self, request: MandateRequest) -> Suggestions: ...

    def improve(self, request: MandateRequest, spend: bool) -> Improvement: ...

    def change_plan(self, request: MandateRequest) -> ChangePlan: ...

    def team_plan(
        self, request: MandateRequest, options: MandateOptions
    ) -> tuple[RoutePlan, Estimate]: ...

    def team_cards(
        self, plan: RoutePlan, estimate: Estimate, options: MandateOptions, task_type: str
    ) -> tuple[RoleCard, ...]: ...

    def team_advice(self, task_type: str) -> ProviderAdvice | None: ...

    def understand(self, story: str) -> Understanding: ...

    def last_story(self) -> str: ...

    def drafts(self, current: str) -> tuple[Draft, ...]: ...

    def autosave(self, draft_id: str, story: str) -> None: ...

    def launched(self, draft_id: str, story: str) -> None: ...

    def load_draft(self, draft_id: str) -> str: ...

    def terminal_report(self) -> TerminalReport: ...

    def save_setting(self, dotted: str, value: object) -> None: ...

    def loop_state(self) -> LoopState: ...

    def run_loop(
        self, max_iterations: int, budget_usd: float, progress: ProgressCallback
    ) -> LoopReport: ...


class CallbackSink:
    def __init__(self, callback: ProgressCallback) -> None:
        self._callback = callback

    def publish(self, event: ProgressEvent) -> None:
        self._callback(event)


class ContainerServices:
    def build_warning(self, engine: str) -> Message | None:
        return msg("guarantee.codex_builds") if engine == "codex" and os.name == "nt" else None

    def __init__(self, project: Path) -> None:
        self._project = project
        self._flow: MandateFlow | CrossEnginePipeline | None = None
        self._starting = False
        self._stop_requested = False
        self._stop_lock = threading.Lock()
        self._clarity: dict[str, Clarity] = {}
        self._plan_cache: tuple[MandateRequest, ChangePlan] | None = None

    @property
    def project(self) -> Path:
        return self._project

    def _container(self) -> Container:
        from cuanta.bootstrap import Container

        return Container.for_project(self._project)

    def home(self) -> HomeSnapshot:
        container = self._container()
        try:
            return container.home_query().run()
        finally:
            container.close()

    def prefix_window(self, engine: str) -> PrefixWindow:
        container = self._container()
        try:
            return container.prefix_query().run(engine or container.config.engine)
        finally:
            container.close()

    def latest_tests(self) -> TestsSummary | None:
        container = self._container()
        try:
            return container.latest_tests().run()
        finally:
            container.close()

    def run_tests(self) -> TestsSummary:
        container = self._container()
        detection = container.detector().run(with_engines=False)
        ledger = container.ledger()
        try:
            started = container.clock.now_iso()
            scoped = container.affected_gateway(ledger).run(
                detection.stack,
                container.config,
                detection.verify_tier,
                False,
                run_id=os.environ.get("CUANTA_RUN_ID", ""),
            )
            return from_report(scoped.report, started)
        finally:
            container.close()

    def capsule(self, reference: str, level: Level) -> CapsuleView:
        container = self._container()
        ledger = container.ledger()
        try:
            return container.cat_capsule(ledger).run(reference, level, None)
        finally:
            container.close()

    def doctor(self) -> DoctorReport:
        container = self._container()
        try:
            return container.doctor().run()
        finally:
            container.close()

    def apply_fix(self, fix: Fix) -> str:
        container = self._container()
        try:
            if fix.action is FixAction.TELEMETRY_ON:
                reports = container.telemetry().enable(fix.argument or "all")
                return "; ".join(f"{item.engine}: {item.state.value}" for item in reports)
            if fix.action is FixAction.LISTENER_START:
                control = container.listener()
                status = control.start_background(control.free_port(container.config.port))
                return f"127.0.0.1:{status.port}" if status.running else "not running"
            return ""
        finally:
            container.close()

    def copy(self, text: str) -> bool:
        return self._container().copy_to_clipboard(text)

    def mandate_setup(self) -> MandateSetup:
        container = self._container()
        engines = []
        for name in ENGINE_NAMES:
            engine = container.engine(name)
            engines.append((name, engine is not None and engine.available()))
        try:
            return MandateSetup(
                tuple(engines),
                container.config.engine,
                container.known_models(),
                container.config.budget_usd,
                container.has_forge_agents(),
                container.init_estimate(),
                container.config.max_turns,
            )
        finally:
            container.close()

    def failure_evidence(self) -> tuple[str, int]:
        container = self._container()
        try:
            return container.mandate_service(container.shared_ledger()).from_failure()
        finally:
            container.close()

    def read_evidence(self, path: str) -> str:
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = self._project / target
        return target.read_text(encoding="utf-8", errors="replace")

    def preview_mandate(
        self, request: MandateRequest, signatures: int, options: MandateOptions
    ) -> MandatePreview:
        container = self._container()
        try:
            options, _ = container.shaped_options(request, options)
            engine = options.engine or container.config.engine
            if per_role_run(options, request.type, container.config.engine):
                return role_preview(request, role_plan(container, request, options), engine)
            flow = container.mandate_flow(container.shared_ledger())
            return preview_of(flow.prepare(request, signatures, options, preview=True))
        finally:
            container.close()

    def run_mandate(
        self,
        request: MandateRequest,
        signatures: int,
        options: MandateOptions,
        observer: EventSink,
        progress: ProgressCallback,
    ) -> MandateReport:
        container = self._container()
        try:
            options, _ = container.shaped_options(request, options)
            if per_role_run(options, request.type, container.config.engine):
                return self._run_cross(container, request, options, progress)
            if options.sandbox:
                return self._run_sandboxed(
                    container, request, signatures, options, observer, progress
                )
            flow = container.mandate_flow(container.shared_ledger())
            prepared = flow.prepare(request, signatures, options)
            self._flow = flow
            return flow.run(prepared, CallbackSink(progress), observer)
        finally:
            self._flow = None
            container.close()

    def _run_cross(
        self,
        container: Container,
        request: MandateRequest,
        options: MandateOptions,
        progress: ProgressCallback,
    ) -> MandateReport:
        self._starting, self._stop_requested = True, False
        try:
            report = self._cross(container, request, options, CallbackSink(progress))
        finally:
            self._starting = False
        return cross_report(
            container.shared_ledger().get_run(report.steps[0].run_id) if report.steps else None,
            report,
        )

    def _cross(
        self,
        container: Container,
        request: MandateRequest,
        options: MandateOptions,
        sink: CallbackSink,
    ) -> CrossReport:
        from cuanta.application.mandate_flow import resolve_max_turns
        from cuanta.domain.depth import parse_depth, profile

        ledger = container.shared_ledger()
        plan = role_plan(container, request, options)
        cap = resolve_budget(options, request.type, container.config.budget_usd)
        turns = resolve_max_turns(
            options, profile(parse_depth(options.depth), request.type), container.config.max_turns
        )

        def started(pipeline: CrossEnginePipeline) -> None:
            with self._stop_lock:
                if self._stop_requested:
                    raise DomainFailure(english(msg("sandbox.stopped_before_launch")))
                self._flow = pipeline

        if not options.sandbox:
            pipeline = container.cross_engine(ledger, cap, turns, depth=options.depth)
            started(pipeline)
            return pipeline.run(request, plan, sink)
        isolated = container.run_sandboxed_cross(
            ledger,
            request,
            plan,
            sink,
            cap,
            turns,
            options.keep_copy,
            options.depth,
            on_start=started,
        )
        if isolated.cross is None:
            raise NotAvailable("the isolated run ended without a report", "check the ledger")
        return isolated.cross

    def _run_sandboxed(
        self,
        container: Container,
        request: MandateRequest,
        signatures: int,
        options: MandateOptions,
        observer: EventSink,
        progress: ProgressCallback,
    ) -> MandateReport:
        def started(flow: MandateFlow, _: object) -> None:
            with self._stop_lock:
                if self._stop_requested:
                    raise DomainFailure(english(msg("sandbox.stopped_before_launch")))
                self._flow = flow

        self._starting, self._stop_requested = True, False
        try:
            result = container.run_sandboxed(
                container.shared_ledger(),
                request,
                signatures,
                options,
                CallbackSink(progress),
                observer,
                on_start=started,
            )
        finally:
            self._starting = False
        if result.report is None:
            raise NotAvailable("the isolated run ended without a report", "check the ledger")
        return result.report

    def _trials[T](self, action: Callable[[Container, TrialStore], T]) -> T:
        container = self._container()
        try:
            return action(container, container.trial_store(container.shared_ledger()))
        finally:
            container.close()

    def trial_handoff(self, run_id: str) -> Handoff | None:
        return self._trials(
            lambda container, store: store.handoff(
                run_id, parse_workflow(container.config.git_workflow), container.shell()
            )
        )

    def apply_trial(self, run_id: str) -> int:
        return self._trials(lambda _, store: len(store.apply(run_id).changes))

    def discard_trial(self, run_id: str) -> None:
        self._trials(lambda _, store: store.discard(run_id))

    def stop_mandate(self) -> bool:
        with self._stop_lock:
            flow = self._flow
            if flow is None:
                if self._starting:
                    self._stop_requested = True
                    return True
                return False
        return flow.stop()

    def _results[T](self, action: Callable[[ResultQuery], T]) -> T:
        container = self._container()
        try:
            return action(container.result_query(container.shared_ledger()))
        finally:
            container.close()

    def result_view(self, run_id: str) -> ResultView | None:
        return self._results(lambda query: query.load(run_id))

    def _map[T](self, action: Callable[[MapQuery], T], rebuild: bool = False) -> T:
        container = self._container()
        query = container.map_query(rebuild)
        try:
            return action(query)
        finally:
            query.close()
            container.close()

    def map_status(self, rebuild: bool = False) -> MapStatus:
        return self._map(lambda query: query.status(), rebuild)

    def map_search(self, query: str) -> tuple[SearchHit, ...]:
        return self._map(lambda current: current.search(query))

    def map_file(self, path: str) -> MapFile:
        return self._map(lambda query: query.file(path))

    def map_revalidate(self) -> MapStatus:
        return self._map(lambda query: query.revalidate())

    def _view(self, query: ResultQuery, run_id: str) -> ResultView:
        view = query.load(run_id)
        if view is None:
            raise NotAvailable(f"run {run_id} is not in the ledger", "pick another run")
        return view

    def save_result(self, run_id: str) -> str:
        return self._results(lambda query: query.save_to_docs(self._view(query, run_id)))

    def export_result(self, run_id: str) -> str:
        return self._results(lambda query: query.export_markdown(self._view(query, run_id)))

    def result_file(self, run_id: str, path: str) -> RunFile:
        return self._results(lambda query: query.file(run_id, path))

    def recent_runs(self) -> tuple[Run, ...]:
        container = self._container()
        try:
            return container.runs_query().run()
        finally:
            container.close()

    def accept_run(self, run_id: str) -> None:
        container = self._container()
        try:
            container.run_outcomes(container.shared_ledger()).accept(run_id)
        finally:
            container.close()

    def reject_run(self, run_id: str) -> None:
        container = self._container()
        try:
            container.run_outcomes(container.shared_ledger()).reject(run_id)
        finally:
            container.close()

    def real_costs(self) -> CostReport:
        container = self._container()
        try:
            return container.costs_query().report()
        finally:
            container.close()

    def spectrum(self, run_id: str) -> SpectrumResult:
        container = self._container()
        ledger = container.ledger()
        try:
            selection = (
                Selection(since=ALL_SESSIONS) if run_id == ALL_IMPORTED else Selection(run=run_id)
            )
            result = container.spectrum_query(ledger).run(selection)
            return replace(result, trend=container.costs_query().trend())
        finally:
            container.close()

    def import_sessions(self) -> dict[str, int]:
        container = self._container()
        ledger = container.ledger()
        try:
            service = container.transcript_import(ledger)
            service.run()
            return dict(service.added)
        finally:
            container.close()

    def reindex_graph(self) -> tuple[bool, str]:
        return self._container().reindex_graph()

    def export_ledger(self, fmt: str, table: str, path: str, include_raw: bool) -> tuple[str, int]:
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = self._project / target
        container = self._container()
        try:
            final, rows = container.export_ledger(fmt, table, target, include_raw)
            return str(final), rows
        finally:
            container.close()

    def telemetry_plan(self, engine: str) -> tuple[WiringPlan, ...]:
        return self._container().telemetry().plan(engine)

    def run_init(
        self,
        options: InitOptions,
        consent: bool,
        budget_usd: float | None,
        progress: ProgressCallback,
    ) -> InitReport:
        container = self._container()
        try:
            use_case = container.init_project(
                CallbackSink(progress), telemetry_consent=consent, budget_usd=budget_usd
            )
            return use_case.run(options)
        finally:
            container.close()

    def load_new_file(self, path: str) -> NewFilePair:
        return self._container().new_file_review().load(path)

    def resolve_new_file(self, path: str, keep_mine: bool) -> str:
        review = self._container().new_file_review()
        return review.keep_mine(path) if keep_mine else review.use_new(path)

    def telemetry_panel(self) -> TelemetryPanel:
        container = self._container()
        service = container.telemetry()
        status = service.status()
        plans = {plan.engine: plan for plan in service.plan()}
        engines = tuple(
            (report, plans[report.engine]) for report in status.engines if report.engine in plans
        )
        listener = status.listener
        return TelemetryPanel(listener.running, status.port, listener.written, engines)

    def set_telemetry(self, engine: str, on: bool) -> tuple[WiringReport, ...]:
        service = self._container().telemetry()
        return service.enable(engine) if on else service.disable(engine)

    def listener_start(self) -> int:
        container = self._container()
        control = container.listener()
        status = control.start_background(control.free_port(container.config.port))
        return status.port

    def listener_stop(self) -> bool:
        return self._container().listener().stop()

    def instinct_overview(self) -> tuple[tuple[BackendStatus, ...], tuple[Decision, ...]]:
        container = self._container()
        current = container.config.instinct
        consent = set(container.config.remote_consent)
        statuses = []
        for name in BACKENDS:
            backend = container.instinct_backend(name)
            available, detail = backend.available()
            statuses.append(
                BackendStatus(
                    name, available, detail, backend.remote, name in consent, name == current
                )
            )
        decisions: tuple[Decision, ...] = ()
        if (container.cuanta_dir() / "ledger.db").is_file():
            ledger = container.ledger()
            try:
                decisions = ledger.decisions(limit=50)
            finally:
                container.close()
        return tuple(statuses), decisions

    def use_backend(self, name: str, consent: bool) -> None:
        container = self._container()
        allowed = [item for item in container.config.remote_consent if item != name]
        if consent:
            allowed.append(name)
        container.set_project_value("instinct.consent", allowed)
        container.set_project_value("instinct.backend", name)

    def probe_instinct(self) -> list[ProbeRow]:
        container = self._container()
        ledger = container.ledger()
        try:
            return probe(container.decisions(ledger))
        finally:
            container.close()

    def instinct_calibration(self) -> tuple[Calibration, ...]:
        container = self._container()
        try:
            if not (container.cuanta_dir() / "ledger.db").is_file():
                return ()
            return calibration(container.ledger())
        finally:
            container.close()

    def settings(self) -> Config:
        return self._container().config

    def models_view(self, refresh: bool) -> CatalogView:
        return self._container().model_service().view(refresh)

    def set_model_tier(self, model: str, tier: str) -> ModelEntry:
        return self._container().model_service().set_tier(model, tier)

    def probe_model(self, model: str, spend: bool) -> ProbeOutcome:
        return self._container().probe_model(model, spend)

    def routing_policy(self) -> RoutingPolicy:
        return self._container().routing_policy()

    def save_routing(self, values: Mapping[str, object]) -> None:
        container = self._container()
        for key, value in values.items():
            container.set_project_path(("routing", *key.split(".")), value)

    def routing_stats(self) -> tuple[RoleStats, ...]:
        container = self._container()
        if not (container.cuanta_dir() / "ledger.db").is_file():
            return ()
        ledger = container.ledger()
        try:
            return role_stats(ledger.routing_decisions())
        finally:
            ledger.close()

    def jev_card(self, test: bool) -> JevCard:
        return self._container().jev_card(test)

    def latest_bench(self) -> BenchResult | None:
        return self._container().latest_bench()

    def instinct_preview(self) -> str:
        return preview_state(PREVIEW_WHAT, PREVIEW_WHERE, PREVIEW_TYPE)

    def assistant_check(self, request: MandateRequest) -> Clarity:
        key = content_key(request)
        if key in self._clarity:
            return self._clarity[key]
        container = self._container()
        ledger = container.ledger()
        try:
            container.preview_scope(request)
            clarity = container.prompt_assistant(ledger).check(request)
        finally:
            ledger.close()
        self._clarity[key] = clarity
        return clarity

    def assistant_preview(self, request: MandateRequest) -> str:
        container = self._container()
        ledger = container.ledger()
        try:
            return container.prompt_assistant(ledger).preview(request)
        finally:
            ledger.close()

    def suggestions(self, request: MandateRequest) -> Suggestions:
        return self._container().suggestions(request)

    def improve(self, request: MandateRequest, spend: bool) -> Improvement:
        return self._container().improve_request(request, spend)

    def team_plan(
        self, request: MandateRequest, options: MandateOptions
    ) -> tuple[RoutePlan, Estimate]:
        container = self._container()
        route = options.route
        try:
            options, choice = container.shaped_options(request, options)
            plan, _ = container.plan_route(
                request.type,
                request.what,
                request.where,
                route.mode,
                route.preset,
                dict(route.role_models),
                clarity=route.clarity,
                depth=options.depth,
                scope=route.scope,
                risk=route.risk,
                engine=options.engine or container.config.engine,
            )
            docs = container.docs_choice(request, options)
            plan = container.shape_plan(plan, choice, docs)
            cap = resolve_budget(options, request.type, container.config.budget_usd)
            shape = options.simple or single_context(
                request.type, options.simple, parse_shape(options.shape)
            )
            shaped = (Shape.SINGLE if shape else Shape.PIPELINE).value
            engine = options.engine or container.config.engine
            provider = parse_provider(engine)
            if provider is None or options.simple:
                return plan, container.team_estimate(plan, request.type, options.depth, cap, shaped)
            protection = apply_overrides(
                self._compiled_plan(container, request), options.plan_overrides
            )
            estimate = replace(
                container.team_estimate(
                    plan,
                    request.type,
                    options.depth,
                    cap,
                    shaped,
                    bool(protection.verify),
                    docs is not None and not docs.on,
                ),
                shape=choice,
                docs=docs,
            )
            per_role = per_role_run(options, request.type, container.config.engine)
            try:
                forecast = container.team_forecast(
                    request.type,
                    plan,
                    provider,
                    options.depth,
                    Shape.SCOUT.value if choice.scout else shaped,
                    cap,
                    protection,
                    native=shaped == Shape.PIPELINE.value and not per_role,
                    model="" if per_role else options.model,
                    max_turns=0
                    if per_role
                    else launch_turns(options, request.type, engine, container.config.max_turns),
                )
            except (CuantaError, ValueError) as error:
                return plan, replace(estimate, forecast_error=forecast_failure(error))
            return plan, replace(estimate, forecast=forecast)
        finally:
            container.close()

    def _compiled_plan(self, container: Container, request: MandateRequest) -> ChangePlan:
        cached = self._plan_cache
        if cached is not None and cached[0] == request:
            return cached[1]
        plan = container.change_plan(request)
        self._plan_cache = (request, plan)
        return plan

    def team_cards(
        self, plan: RoutePlan, estimate: Estimate, options: MandateOptions, task_type: str
    ) -> tuple[RoleCard, ...]:
        container = self._container()
        try:
            launch = estimate.shape is not None and estimate.shape.launch
            if launch or per_role_run(options, task_type, container.config.engine):
                shares = {cost.role: cost.share for cost in estimate.roles if cost.share > 0}
                return team_cards(
                    plan.routes,
                    shares,
                    estimate.cap,
                    container.pipeline_index_tools,
                    container.build_blocked(),
                )
            config = container.config

            def native(engine: str) -> bool:
                return engine == "claude" and config.index_enabled and config.index_tools

            return team_cards(plan.routes, {}, 0.0, native)
        finally:
            container.close()

    def team_advice(self, task_type: str) -> ProviderAdvice | None:
        container = self._container()
        try:
            runs = container.shared_ledger().runs()
            attempts = provider_attempts(runs, container.estimate_shapes(runs))
            return recommend_provider(attempts, task_type)
        finally:
            container.close()

    def change_plan(self, request: MandateRequest) -> ChangePlan:
        container = self._container()
        try:
            plan = container.change_plan(request)
            self._plan_cache = (request, plan)
            return plan
        finally:
            container.close()

    def understand(self, story: str) -> Understanding:
        container = self._container()
        ledger = container.ledger()
        try:
            token = f"intake:{secrets.token_hex(16)}"
            container.decision_scope.set("", token, True)
            return replace(container.intake(ledger).understand(story), intake_scope=token)
        finally:
            ledger.close()

    def last_story(self) -> str:
        return self._container().drafts().last()

    def drafts(self, current: str) -> tuple[Draft, ...]:
        return self._container().drafts().listed(current)

    def autosave(self, draft_id: str, story: str) -> None:
        self._container().drafts().autosave(draft_id, story)

    def launched(self, draft_id: str, story: str) -> None:
        self._container().drafts().launched(draft_id, story)

    def load_draft(self, draft_id: str) -> str:
        return self._container().drafts().load(draft_id)

    def save_setting(self, dotted: str, value: object) -> None:
        self._container().set_project_value(dotted, value)

    def loop_state(self) -> LoopState:
        container = self._container()
        detection = container.detector().run(with_engines=False)
        text = container.workspace().read_text("docs/LOOP.md")
        gate = loop_gate(detection.verify_tier, detection.verify.evidence, text)
        config = container.config
        return LoopState(
            gate, detection.verify_tier.value, config.loop_max_iterations, config.budget_usd
        )

    def run_loop(
        self, max_iterations: int, budget_usd: float, progress: ProgressCallback
    ) -> LoopReport:
        container = self._container()
        detection = container.detector().run(with_engines=False)
        gate = loop_gate(
            detection.verify_tier,
            detection.verify.evidence,
            container.workspace().read_text("docs/LOOP.md"),
        )
        if not gate.allowed:
            raise NotAvailable(f"the loop is gated: {gate.missing}", "see the Loop screen")
        ledger = container.shared_ledger()
        gateway = container.gateway(ledger)
        flow = container.mandate_flow(ledger)
        sink = CallbackSink(progress)
        loop_id = container.new_run_id()
        started = container.clock.now_iso()
        ledger.add_run(Run(id=loop_id, kind="loop", started_at=started, status="running"))

        def run_tests(run_id: str) -> TestStep:
            report = gateway.run(
                detection.stack, container.config, detection.verify_tier, run_id=run_id
            )
            return TestStep(report.status.value, len(report.signatures))

        def fix(run_id: str, _: int) -> FixStep:
            evidence, signatures = flow.service.from_failure()
            request = MandateRequest(
                type="bug",
                what="make cuanta test green by fixing the failing signatures below",
                why=evidence,
                out_of_scope=LOOP_OUT_OF_SCOPE,
            )
            prepared = flow.prepare(request, signatures, MandateOptions(parent=run_id))
            self._flow = flow
            try:
                report = flow.run(prepared, sink)
            finally:
                self._flow = None
            return FixStep(report.run.id, report.ok, report.run.cost_usd)

        try:
            outcome = FixLoop(run_tests, fix, sink).run(loop_id, max_iterations, budget_usd)
            ledger.update_run(
                Run(
                    id=loop_id,
                    kind="loop",
                    started_at=started,
                    ended_at=container.clock.now_iso(),
                    status=outcome.stop.value,
                    cost_usd=outcome.spent_usd,
                )
            )
            return outcome
        finally:
            container.close()

    def terminal_report(self) -> TerminalReport:
        return self._container().terminal_report()
