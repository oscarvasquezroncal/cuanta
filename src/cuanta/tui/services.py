from __future__ import annotations

import os
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from cuanta.application.assistant import Improvement
from cuanta.application.bench import BenchResult
from cuanta.application.cat_capsule import CapsuleView
from cuanta.application.doctor import DoctorReport
from cuanta.application.estimate import Estimate
from cuanta.application.home import HomeSnapshot
from cuanta.application.init_project import InitOptions, InitReport
from cuanta.application.instinct_view import (
    BACKENDS,
    BackendStatus,
    JevCard,
    ProbeRow,
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
    preview_of,
    resolve_budget,
)
from cuanta.application.models import CatalogView, ProbeOutcome
from cuanta.application.new_files import NewFilePair
from cuanta.application.results import ResultQuery, ResultView, RunFile
from cuanta.application.routing import RoleStats, RoutePlan, role_stats
from cuanta.application.spectrum import ALL_SESSIONS, Selection, SpectrumResult
from cuanta.application.tests_view import TestsSummary, from_report
from cuanta.domain.assistant import Clarity, Suggestions, content_key
from cuanta.domain.cache import PrefixWindow
from cuanta.domain.capsules import Level
from cuanta.domain.config import Config
from cuanta.domain.drafts import Draft
from cuanta.domain.engine import EngineEvent
from cuanta.domain.errors import NotAvailable
from cuanta.domain.fixes import Fix, FixAction
from cuanta.domain.ledger import Decision, Run
from cuanta.domain.loop import LOOP_OUT_OF_SCOPE, LoopGate, loop_gate
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.models import ModelEntry
from cuanta.domain.progress import ProgressEvent
from cuanta.domain.routing import RoutingPolicy
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
    from cuanta.bootstrap import Container


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

    def save_result(self, run_id: str) -> str: ...

    def export_result(self, run_id: str) -> str: ...

    def result_file(self, run_id: str, path: str) -> RunFile: ...

    def recent_runs(self) -> tuple[Run, ...]: ...

    def spectrum(self, run_id: str) -> SpectrumResult: ...

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

    def settings(self) -> Config: ...

    def models_view(self, refresh: bool) -> CatalogView: ...

    def set_model_tier(self, model: str, tier: str) -> ModelEntry: ...

    def probe_model(self, model: str, spend: bool) -> ProbeOutcome: ...

    def routing_policy(self) -> RoutingPolicy: ...

    def save_routing(self, values: Mapping[str, object]) -> None: ...

    def routing_stats(self) -> tuple[RoleStats, ...]: ...

    def jev_card(self, test: bool) -> JevCard: ...

    def latest_bench(self) -> BenchResult | None: ...

    def instinct_preview(self) -> str: ...

    def assistant_check(self, request: MandateRequest) -> Clarity: ...

    def assistant_preview(self, request: MandateRequest) -> str: ...

    def suggestions(self, request: MandateRequest) -> Suggestions: ...

    def improve(self, request: MandateRequest, spend: bool) -> Improvement: ...

    def team_plan(
        self, request: MandateRequest, options: MandateOptions
    ) -> tuple[RoutePlan, Estimate]: ...

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
    def __init__(self, project: Path) -> None:
        self._project = project
        self._flow: MandateFlow | None = None
        self._clarity: dict[str, Clarity] = {}

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
            flow = container.mandate_flow(container.shared_ledger())
            prepared = flow.prepare(request, signatures, options)
            self._flow = flow
            return flow.run(prepared, CallbackSink(progress), observer)
        finally:
            self._flow = None
            container.close()

    def stop_mandate(self) -> bool:
        flow = self._flow
        return flow.stop() if flow is not None else False

    def _results[T](self, action: Callable[[ResultQuery], T]) -> T:
        container = self._container()
        try:
            return action(container.result_query(container.shared_ledger()))
        finally:
            container.close()

    def result_view(self, run_id: str) -> ResultView | None:
        return self._results(lambda query: query.load(run_id))

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

    def spectrum(self, run_id: str) -> SpectrumResult:
        container = self._container()
        ledger = container.ledger()
        try:
            selection = (
                Selection(since=ALL_SESSIONS) if run_id == ALL_IMPORTED else Selection(run=run_id)
            )
            return container.spectrum_query(ledger).run(selection)
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
            cap = resolve_budget(options, request.type, container.config.budget_usd)
            return plan, container.team_estimate(plan, request.type, options.depth, cap)
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
