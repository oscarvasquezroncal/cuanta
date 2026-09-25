from __future__ import annotations

import importlib
import importlib.metadata
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from cuanta.adapters.system.clock import SystemClock
from cuanta.adapters.system.config_files import global_config_path, project_config_path, read_table
from cuanta.adapters.system.platform import home_dir
from cuanta.adapters.system.process_runner import SubprocessRunner
from cuanta.adapters.system.workspace import LocalHome, LocalWorkspace
from cuanta.domain.config import Config, layer_from_env, layer_from_table, merge
from cuanta.domain.ids import make_run_id
from cuanta.ports.progress import ProgressSink
from cuanta.ports.system import Clock, ProcessRunner

BUILTIN_TEST_RUNNERS = {
    "pytest": "cuanta.adapters.testing.pytest_runner:PytestRunner",
    "jest": "cuanta.adapters.testing.jest_runner:JestRunner",
    "vitest": "cuanta.adapters.testing.vitest_runner:VitestRunner",
    "go": "cuanta.adapters.testing.go_runner:GoRunner",
    "cargo": "cuanta.adapters.testing.cargo_runner:CargoRunner",
    "generic": "cuanta.adapters.testing.generic_runner:GenericRunner",
}
BUILTIN_ENGINES = {
    "claude": "cuanta.adapters.engines.claude_code:ClaudeCodeEngine",
    "codex": "cuanta.adapters.engines.codex:CodexEngine",
    "opencode": "cuanta.adapters.engines.opencode:OpenCodeEngine",
}


def _load_object(reference: str) -> Callable[..., object]:
    module_name, _, attribute = reference.partition(":")
    module = importlib.import_module(module_name)
    loaded = getattr(module, attribute)
    if not callable(loaded):
        raise TypeError(f"{reference} is not callable")
    return cast("Callable[..., object]", loaded)


def _new_scope() -> DecisionScope:
    from cuanta.application.instinct import DecisionScope

    return DecisionScope()


def plugin_factories(group: str, builtins: dict[str, str]) -> dict[str, Callable[..., object]]:
    factories: dict[str, Callable[..., object]] = {}
    for name, reference in builtins.items():
        factories[name] = _lazy(reference)
    for entry in importlib.metadata.entry_points(group=group):
        if entry.name not in builtins:
            factories[entry.name] = _lazy(entry.value)
    return factories


def _lazy(reference: str) -> Callable[..., object]:
    def build(*args: object, **kwargs: object) -> object:
        return _load_object(reference)(*args, **kwargs)

    return build


if TYPE_CHECKING:
    from cuanta.adapters.forge.installer import VendoredForgeKit
    from cuanta.adapters.storage.capsule_store import FileCapsuleStore
    from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
    from cuanta.application.affected import AffectedGateway
    from cuanta.application.assistant import Improvement, PromptAssistant
    from cuanta.application.bench import Attempt, BenchResult, BenchRunner
    from cuanta.application.cat_capsule import CatCapsule
    from cuanta.application.cross_engine import CrossEnginePipeline
    from cuanta.application.detect import DetectProject
    from cuanta.application.doctor import Doctor
    from cuanta.application.drafts import Drafts
    from cuanta.application.engine_run import EngineLauncher
    from cuanta.application.estimate import Estimate
    from cuanta.application.gateway import RunGateway
    from cuanta.application.home import HomeQuery
    from cuanta.application.init_project import InitProject
    from cuanta.application.instinct import DecisionMaker, DecisionScope
    from cuanta.application.instinct_view import JevCard
    from cuanta.application.intake import IntakeService
    from cuanta.application.ledger_view import RunsQuery
    from cuanta.application.mandate import MandateService
    from cuanta.application.mandate_flow import MandateFlow
    from cuanta.application.models import ModelService, ProbeOutcome
    from cuanta.application.new_files import NewFileReview
    from cuanta.application.refresh import RefreshProject
    from cuanta.application.results import ResultQuery
    from cuanta.application.route_apply import MandateRouting
    from cuanta.application.routing import RouteAdvisor, RoutePlan
    from cuanta.application.session_profile import LeanProfile
    from cuanta.application.spectrum import SpectrumQuery
    from cuanta.application.telemetry import TelemetryService, TranscriptImport
    from cuanta.application.tests_view import LatestTests
    from cuanta.domain.assistant import Suggestions
    from cuanta.domain.bench import BenchTask, Condition
    from cuanta.domain.instinct import Choice
    from cuanta.domain.mandate import MandateRequest
    from cuanta.domain.messages import Message
    from cuanta.domain.routing import CostRange, RoutingPolicy
    from cuanta.domain.shells import Shell
    from cuanta.domain.terminal import TerminalReport
    from cuanta.ports.engine import Engine
    from cuanta.ports.instinct import Instinct
    from cuanta.ports.ledger import Ledger
    from cuanta.ports.listener import ListenerControl
    from cuanta.ports.test_runner import TestRunner


def load_config(project: Path) -> Config:
    return merge(
        [
            layer_from_table(read_table(global_config_path())),
            layer_from_table(read_table(project_config_path(project))),
            layer_from_env(os.environ),
        ]
    )


@dataclass
class Container:
    project: Path
    config: Config
    runner: ProcessRunner = field(default_factory=SubprocessRunner)
    clock: Clock = field(default_factory=SystemClock)
    home: Path = field(default_factory=home_dir)
    _shared: Ledger | None = field(default=None, repr=False)
    _opened: list[Ledger] = field(default_factory=list, repr=False)
    decision_scope: DecisionScope = field(default_factory=_new_scope, repr=False)

    @classmethod
    def for_project(cls, project: Path) -> Container:
        return cls(project=project, config=load_config(project))

    def new_run_id(self) -> str:
        return make_run_id(self.clock.now_ms(), secrets.token_bytes(10))

    def workspace(self) -> LocalWorkspace:
        return LocalWorkspace(self.project)

    def home_reader(self) -> LocalHome:
        return LocalHome(self.home)

    def detector(self) -> DetectProject:
        from cuanta.application.detect import DetectProject

        return DetectProject(
            self.workspace(),
            self.home_reader(),
            self.runner,
            frozenset(self.config.exclusions),
        )

    def init_project(
        self,
        progress: ProgressSink,
        telemetry_consent: bool = False,
        scope: str = "project",
        budget_usd: float | None = None,
    ) -> InitProject:
        from cuanta.adapters.graph.graphify import GraphifyTool
        from cuanta.application.forge import ForgeStage, VerifyStage
        from cuanta.application.init_project import GraphStage, HandoffWriter, InitProject
        from cuanta.application.telemetry import TelemetryStage

        workspace = self.workspace()
        kit = self.forge_kit(scope)

        def launcher() -> EngineLauncher | None:
            engine = self.engine("claude")
            if engine is None or not engine.available():
                return None
            return self.launcher(engine, self.shared_ledger(), telemetry=telemetry_consent)

        return InitProject(
            workspace=workspace,
            detector=self.detector(),
            graph_stage=GraphStage(workspace, GraphifyTool(self.runner)),
            handoff=HandoffWriter(workspace, self.clock),
            progress=progress,
            telemetry_stage=TelemetryStage(self.telemetry(), telemetry_consent),
            forge_stage=ForgeStage(
                kit,
                launcher,
                progress,
                str(self.project),
                workspace,
                self.config.budget_usd if budget_usd is None else budget_usd,
            ),
            verify_stage=VerifyStage(workspace, self.ledger, self.clock),
            run_id_factory=self.new_run_id,
        )

    def refresh_project(self, progress: ProgressSink) -> RefreshProject:
        from cuanta.adapters.graph.graphify import GraphifyTool
        from cuanta.application.forge import VerifyStage
        from cuanta.application.init_project import GraphStage
        from cuanta.application.refresh import RefreshProject

        workspace = self.workspace()

        def launcher() -> EngineLauncher | None:
            engine = self.engine("claude")
            if engine is None or not engine.available():
                return None
            return self.launcher(engine, self.shared_ledger(), telemetry=True)

        return RefreshProject(
            workspace=workspace,
            detector=self.detector(),
            graph_stage=GraphStage(workspace, GraphifyTool(self.runner)),
            kit=self.forge_kit(),
            launcher_factory=launcher,
            verify_stage=VerifyStage(workspace, self.ledger, self.clock),
            progress=progress,
        )

    def forge_kit(self, scope: str = "project") -> VendoredForgeKit:
        from cuanta.adapters.forge.installer import VendoredForgeKit

        return VendoredForgeKit(self.home if scope == "user" else self.project)

    def engine(self, name: str) -> Engine | None:
        factory = plugin_factories("cuanta.engines", BUILTIN_ENGINES).get(name)
        if factory is None:
            return None
        return cast("Engine", factory(self.runner))

    def shared_ledger(self) -> Ledger:
        if self._shared is None:
            self._shared = self.ledger()
        return self._shared

    def close(self) -> None:
        self._shared = None
        while self._opened:
            self._opened.pop().close()

    def launcher(self, engine: Engine, ledger: Ledger, telemetry: bool) -> EngineLauncher:
        import secrets as entropy_source

        from cuanta.adapters.system.prices import load_prices
        from cuanta.application.engine_run import EngineLauncher
        from cuanta.application.run_reports import RunReports

        return EngineLauncher(
            engine=engine,
            ledger=ledger,
            clock=self.clock,
            new_run_id=self.new_run_id,
            entropy=entropy_source.token_bytes,
            project_name=self.project.name,
            port=self.config.port,
            listener=self.listener() if telemetry else None,
            prices=load_prices(),
            reports=RunReports(self.workspace()),
            lean_files=self.lean_profile().files,
            default_session=self.config.run_session,
        )

    def lean_profile(self) -> LeanProfile:
        from cuanta.adapters.engines.claude_plugins import installed_plugins
        from cuanta.application.session_profile import LeanProfile

        return LeanProfile(self.workspace(), lambda: installed_plugins(self.home))

    def instinct_backend(self, name: str = "") -> Instinct:
        from cuanta.adapters.instinct.heuristic import HeuristicInstinct
        from cuanta.adapters.instinct.jev import JevInstinct
        from cuanta.adapters.instinct.llm import LlmInstinct

        chosen = name or self.config.instinct
        if chosen == "heuristic":
            return HeuristicInstinct()
        if chosen == "jev":
            return JevInstinct()
        if chosen == "llm":
            return LlmInstinct(self.engine("claude"), str(self.project))
        factory = plugin_factories("cuanta.instinct", {}).get(chosen)
        if factory is None:
            from cuanta.domain.errors import DomainFailure

            raise DomainFailure(f"unknown instinct backend {chosen}", "use heuristic, jev or llm")
        return cast("Instinct", factory(self.runner))

    def instinct_connection(self) -> Message | None:
        from cuanta.adapters.instinct.jev import JevInstinct
        from cuanta.domain.errors import CuantaError
        from cuanta.domain.messages import msg

        backend = self.instinct_backend()
        if not isinstance(backend, JevInstinct) or not backend.available()[0]:
            return None
        try:
            return backend.connection()
        except CuantaError as error:
            return msg("instinct.unreachable", error=error.message)

    def jev_card(self, test: bool) -> JevCard:
        from datetime import UTC, datetime, timedelta

        from cuanta.adapters.instinct.jev import MODEL, QUESTION_PATH, JevInstinct
        from cuanta.application.instinct_view import WEEK_DAYS, JevCard, week_spend
        from cuanta.domain.errors import CuantaError
        from cuanta.domain.messages import msg

        backend = JevInstinct()
        since = (datetime.now(UTC) - timedelta(days=WEEK_DAYS)).isoformat(timespec="seconds")
        spend, count = 0.0, 0
        if (self.cuanta_dir() / "ledger.db").is_file():
            ledger = self.ledger()
            try:
                spend, count = week_spend(ledger.decisions(), backend.name, since)
            finally:
                ledger.close()
        card = JevCard(
            key_present=backend.available()[0],
            endpoint=backend.base + QUESTION_PATH,
            model=MODEL,
            latency_ms=None,
            spend_week=spend,
            decisions_week=count,
        )
        if not test:
            return card
        try:
            model, provider, latency = backend.ping()
        except CuantaError as error:
            return replace(card, status=msg("instinct.unreachable", error=error.message), ok=False)
        status = msg("instinct.connected", model=model, provider=provider, latency=latency)
        return replace(card, model=model, latency_ms=latency, status=status, ok=True)

    def probe_model(self, reference: str, spend: bool) -> ProbeOutcome:
        from cuanta.application.engine_run import LaunchSpec
        from cuanta.application.models import (
            PROBE_BUDGET_USD,
            PROBE_INPUT_TOKENS,
            PROBE_OUTPUT_TOKENS,
            PROBE_PROMPT,
            ProbeOutcome,
        )
        from cuanta.domain.errors import DomainFailure, NotAvailable
        from cuanta.domain.models import find, probe_cost

        service = self.model_service()
        entry = find(service.view().entries, reference)
        if entry is None:
            raise DomainFailure(
                f"unknown model {reference}", "run cuanta models list to see the catalog"
            )
        estimate = probe_cost(entry, PROBE_INPUT_TOKENS, PROBE_OUTPUT_TOKENS)
        if not spend:
            return ProbeOutcome(entry, estimate, None, None)
        engine = self.engine(entry.engine)
        if engine is None or not engine.available():
            raise NotAvailable(f"{entry.engine} not found on PATH", "install it first")
        ledger = self.ledger()
        try:
            launch = self.launcher(engine, ledger, telemetry=False).launch(
                LaunchSpec(
                    kind="probe",
                    prompt=PROBE_PROMPT,
                    cwd=str(self.project),
                    allowed_tools=(),
                    model=entry.resolved or entry.id,
                    max_budget_usd=PROBE_BUDGET_USD,
                ),
                lambda _: None,
            )
        finally:
            ledger.close()
        ok = launch.outcome is not None and launch.outcome.ok
        if ok:
            service.mark_probed(entry)
        return ProbeOutcome(entry, estimate, ok, launch.run.cost_usd)

    def decisions(self, ledger: Ledger) -> DecisionMaker:
        from cuanta.adapters.instinct.heuristic import HeuristicInstinct
        from cuanta.application.instinct import DecisionMaker, consent_ok
        from cuanta.domain.messages import english

        backend = self.instinct_backend()
        fallback = HeuristicInstinct()
        usable, reason = backend.available()
        disabled_reason = ""
        if not consent_ok(backend, self.config.remote_consent):
            disabled_reason = f"{backend.name} has no remote consent"
        elif not usable:
            disabled_reason = english(reason)
        return DecisionMaker(
            backend,
            ledger,
            self.clock.now_iso,
            fallback if backend.name != fallback.name else None,
            self.decision_scope,
            disabled_reason,
        )

    def preview_scope(self, request: MandateRequest) -> None:
        from cuanta.domain.mandate import decision_key

        self.decision_scope.set("", decision_key(request), True)

    def has_forge_agents(self) -> bool:
        from cuanta.domain.detection import has_forge_agents

        return has_forge_agents(self.workspace().list_names(".claude/agents"))

    def plan_route(
        self,
        task_type: str,
        what: str,
        where: str,
        route: str = "",
        preset: str = "",
        role_models: dict[str, str] | None = None,
        persistent: bool = False,
        clarity: float | None = None,
        depth: str = "",
        scope: Choice | None = None,
        risk: float | None = None,
    ) -> tuple[RoutePlan, CostRange]:
        from cuanta.application.estimate import similar_costs
        from cuanta.application.routing import RouteInputs, with_overrides
        from cuanta.domain.depth import parse_depth, profile
        from cuanta.domain.mandate import MandateRequest as Request
        from cuanta.domain.routing import cost_range, depth_capped, roles_that_run

        policy = with_overrides(self.routing_policy(), route, preset, role_models)
        if depth:
            policy = depth_capped(policy, profile(parse_depth(depth), task_type).tier_cap)
        if scope is None:
            self.preview_scope(Request(type=task_type, what=what, where=where))
        ledger = self.shared_ledger()
        latest = ledger.test_runs(limit=1)
        inputs = RouteInputs(
            task_type=task_type,
            what=what,
            where=where,
            tests=latest[0].status if latest else "unknown",
            blast_radius=self.blast_radius(f"{what} {where}"),
            persistent_failure=persistent,
            clarity=clarity,
            roles=roles_that_run(task_type),
            scope=scope,
            risk=risk,
        )
        plan = self.route_advisor(ledger).plan(policy, inputs)
        return plan, cost_range(similar_costs(ledger.runs(), task_type, depth))

    def prompt_assistant(self, ledger: Ledger) -> PromptAssistant:
        from cuanta.application.assistant import PromptAssistant

        return PromptAssistant(self.decisions(ledger))

    def suggestions(self, request: MandateRequest) -> Suggestions:
        from cuanta.adapters.graph.file_graph import load_neighbours, load_symbols
        from cuanta.application.assistant import suggest

        workspace = self.workspace()
        scan = workspace.scan(frozenset(self.config.exclusions), collect_files=True)
        return suggest(
            request,
            scan.files,
            load_symbols(self.project),
            load_neighbours(self.project),
            workspace.read_text("CLAUDE.md"),
        )

    def intake(self, ledger: Ledger) -> IntakeService:
        from cuanta.adapters.graph.file_graph import load_neighbours, load_symbols
        from cuanta.application.assistant import suggest
        from cuanta.application.intake import IntakeService
        from cuanta.domain.intake import IntakeFacts, match_places

        workspace = self.workspace()

        def places(facts: IntakeFacts, request: MandateRequest) -> tuple[str, ...]:
            scan = workspace.scan(frozenset(self.config.exclusions), collect_files=True)
            symbols = load_symbols(self.project)
            named = match_places(facts, scan.files, symbols)
            suggested = suggest(
                request,
                scan.files,
                symbols,
                load_neighbours(self.project),
                workspace.read_text("CLAUDE.md"),
            ).files
            return tuple(dict.fromkeys((*named, *suggested)))[:8]

        return IntakeService(self.decisions(ledger), self.routing_policy().min_confidence, places)

    def drafts(self) -> Drafts:
        from cuanta.adapters.storage.drafts import FileDraftStore
        from cuanta.application.drafts import Drafts

        return Drafts(FileDraftStore(self.cuanta_dir() / "drafts"), self.clock.now_iso)

    def team_estimate(self, plan: RoutePlan, task_type: str, depth: str, cap: float) -> Estimate:
        from cuanta.adapters.system.prices import load_prices
        from cuanta.application.estimate import estimate

        return estimate(plan, self.shared_ledger().runs(), load_prices(), task_type, depth, cap)

    def improve_request(self, request: MandateRequest, spend: bool) -> Improvement:
        from cuanta.application.assistant import (
            IMPROVE_BUDGET_USD,
            Improvement,
            changes,
            improve_prompt,
            improvement_estimate,
            parse_improvement,
        )
        from cuanta.application.engine_run import LaunchSpec
        from cuanta.domain.errors import NotAvailable
        from cuanta.domain.models import Tier, probe_cost
        from cuanta.domain.routing import candidates

        entries = self.model_service().view().entries
        engines = (self.config.engine, "claude", "codex", "opencode")
        chosen = next(
            (found[0] for name in engines if (found := candidates(entries, (name,), Tier.ECONOMY))),
            None,
        )
        if chosen is None:
            raise NotAvailable("no economy-tier model in the catalog", "run cuanta models refresh")
        prompt = improve_prompt(request)
        estimate = improvement_estimate(prompt, lambda i, o: probe_cost(chosen, i, o))
        if not spend:
            return Improvement(estimate, chosen.key)
        engine = self.engine(chosen.engine)
        if engine is None or not engine.available():
            raise NotAvailable(f"{chosen.engine} not found on PATH", "install it first")
        ledger = self.ledger()
        try:
            launch = self.launcher(engine, ledger, telemetry=False).launch(
                LaunchSpec(
                    kind="assist",
                    prompt=prompt,
                    cwd=str(self.project),
                    allowed_tools=(),
                    model=chosen.resolved or chosen.id,
                    max_budget_usd=IMPROVE_BUDGET_USD,
                ),
                lambda _: None,
            )
        finally:
            ledger.close()
        text = launch.outcome.result.text if launch.outcome and launch.outcome.result else ""
        proposal = parse_improvement(request, text)
        diff = changes(request, proposal) if proposal is not None else ()
        return Improvement(estimate, chosen.key, proposal, diff, launch.run.cost_usd, True)

    def cross_engine(self, ledger: Ledger, budget_usd: float) -> CrossEnginePipeline:
        from cuanta.application.cross_engine import CrossEnginePipeline

        def launcher(name: str) -> EngineLauncher | None:
            engine = self.engine(name)
            if engine is None or not engine.available():
                return None
            return self.launcher(engine, ledger, telemetry=True)

        return CrossEnginePipeline(
            launchers=launcher,
            definitions=lambda: self.mandate_routing(ledger).definitions(),
            capsules=self.capsule_store(),
            cwd=str(self.project),
            budget_usd=budget_usd,
        )

    def bench_runner(
        self, fixtures: Path, kit: Path, model: str, scratch: Path | None, keep: bool
    ) -> BenchRunner:
        import sys

        from cuanta.adapters.bench.sandbox import LocalBenchSandbox
        from cuanta.application.bench import BenchExecutor, BenchRunner

        sandbox = LocalBenchSandbox(fixtures, kit, self.runner, sys.executable, scratch, keep)

        def attempt(
            task: BenchTask, condition: Condition, root: str, cap: float, session: str
        ) -> Attempt:
            return self.bench_attempt(task, condition, root, cap, model, session)

        executor = BenchExecutor(sandbox, attempt, self.clock.monotonic)
        return BenchRunner(executor, self.workspace())

    def bench_tasks(self, directory: Path, suite: str) -> tuple[BenchTask, ...]:
        from cuanta.adapters.bench.tasks import load_tasks
        from cuanta.domain.bench import select

        return select(load_tasks(directory), suite)

    def result_query(self, ledger: Ledger) -> ResultQuery:
        from datetime import date

        from cuanta.application.results import ResultQuery

        return ResultQuery(self.workspace(), ledger, date.today)

    def resolve_run(self, reference: str) -> str:
        from cuanta.domain.errors import DomainFailure

        ledger = self.shared_ledger()
        exact = ledger.get_run(reference)
        if exact is not None:
            return exact.id
        matches = [run.id for run in ledger.runs() if run.id.startswith(reference)]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise DomainFailure(f"no run matches {reference}", "list them: cuanta runs list")
        raise DomainFailure(f"{reference} matches {len(matches)} runs", "give more of the id")

    def stored_reports(self) -> frozenset[str]:
        from cuanta.application.run_reports import RUNS_DIR

        folder = self.project / RUNS_DIR
        if not folder.is_dir():
            return frozenset()
        return frozenset(
            child.name for child in folder.iterdir() if (child / "report.md").is_file()
        )

    def init_estimate(self) -> float | None:
        from cuanta.domain.routing import percentile

        costs = [
            run.cost_usd
            for run in self.shared_ledger().runs(kind="init")
            if run.cost_usd is not None and run.cost_usd > 0
        ]
        return percentile(costs, 0.5)

    def latest_bench(self) -> BenchResult | None:
        from cuanta.application.bench import load_bench

        return load_bench(self.workspace())

    def bench_attempt(
        self,
        task: BenchTask,
        condition: Condition,
        root: str,
        cap: float,
        model: str,
        session: str = "lean",
    ) -> Attempt:
        from cuanta.application.bench import Attempt
        from cuanta.application.engine_run import LaunchSpec
        from cuanta.application.mandate import allowed_tools
        from cuanta.application.mandate_flow import MandateOptions
        from cuanta.application.progress import RecordingSink
        from cuanta.application.route_apply import RouteOptions
        from cuanta.application.spectrum import Selection
        from cuanta.domain.bench import Condition
        from cuanta.domain.errors import NotAvailable
        from cuanta.domain.overhead import session_overhead
        from cuanta.domain.spectrum import LeakKind
        from cuanta.ports.ledger import EventQuery

        sub = replace(Container.for_project(Path(root)), runner=self.runner, clock=self.clock)
        ledger = sub.ledger()
        try:
            models: tuple[tuple[str, str, str], ...] = ()
            if condition is Condition.BASELINE:
                engine = sub.engine("claude")
                if engine is None or not engine.available():
                    raise NotAvailable("claude not found on PATH", "install Claude Code")
                stack = sub.detector().run(with_engines=False).stack
                spec = LaunchSpec(
                    kind="bench",
                    prompt=task.prompt,
                    cwd=root,
                    allowed_tools=allowed_tools(stack),
                    model=model,
                    max_budget_usd=cap,
                    session=session,
                )
                launch = sub.launcher(engine, ledger, telemetry=True).launch(spec, lambda _: None)
                run = launch.run
                result = launch.outcome.result if launch.outcome is not None else None
                subtype = result.subtype if result is not None else ""
            else:
                flow = sub.mandate_flow(ledger)
                mode = "auto" if condition is Condition.ROUTED else "off"
                options = MandateOptions(
                    engine="claude",
                    model=model,
                    budget_usd=cap,
                    route=RouteOptions(mode=mode),
                    session=session,
                )
                report = flow.run(flow.prepare(task.request, 0, options), RecordingSink())
                run = report.run
                subtype = ""
                models = tuple(
                    (row.agent, row.planned, ", ".join(row.actual)) for row in report.audit
                )
            spectrum = sub.spectrum_query(ledger).run(Selection(run=run.id)).report
            totals = spectrum.totals
            events = ledger.events(EventQuery(run_id=run.id))
            overhead = session_overhead(events, 0)
            split = overhead.split
            seen: dict[str, str] = {}
            for event in events:
                if event.command:
                    seen.setdefault(event.tool_use_id or str(event.id), event.command)
            return Attempt(
                run_id=run.id,
                subtype=subtype,
                cost_usd=run.cost_usd,
                fresh_tokens=totals.fresh_input,
                cache_read_tokens=totals.cache_read,
                cache_write_tokens=totals.cache_write,
                output_tokens=totals.output + totals.reasoning,
                test_output_tokens=sum(
                    leak.tokens for leak in spectrum.leaks if leak.kind is LeakKind.TEST_OUTPUT
                ),
                commands=tuple(seen.values()),
                models=models,
                context_tokens=split.first_request if split is not None else 0,
                loaded=(len(overhead.plugins), len(overhead.servers), len(overhead.hooks)),
            )
        finally:
            sub.close()

    def mandate_service(self, ledger: Ledger) -> MandateService:
        from cuanta.application.mandate import MandateService

        return MandateService(
            self.workspace(),
            ledger,
            self.decisions(ledger),
            self.clock.now_iso,
            frozenset(self.config.exclusions),
            self.capsule_store(),
        )

    def mandate_flow(self, ledger: Ledger) -> MandateFlow:
        from cuanta.application.mandate_flow import MandateFlow
        from cuanta.application.spectrum import Selection
        from cuanta.domain.spectrum import View

        def summarize(run_id: str) -> tuple[dict[str, int], float | None]:
            result = self.spectrum_query(ledger).run(Selection(run=run_id))
            by_agent = {row.key: row.tokens for row in result.rows(View.AGENT)}
            return by_agent, result.report.utilization.value

        return MandateFlow(
            service=self.mandate_service(ledger),
            engines=self.engine,
            launchers=lambda engine: self.launcher(engine, ledger, telemetry=True),
            stack=lambda: self.detector().run(with_engines=False).stack,
            summarize=summarize,
            cwd=str(self.project),
            default_engine=self.config.engine,
            default_budget=self.config.budget_usd,
            final_suite=lambda run_id: self.final_suite(ledger, run_id),
            routing=self.mandate_routing(ledger),
            has_agents=self.has_forge_agents,
            scope=self.decision_scope,
            new_run_id=self.new_run_id,
            graph_mode=lambda: self.detector().graph_mode()[0],
        )

    def mandate_routing(self, ledger: Ledger) -> MandateRouting:
        from cuanta.adapters.models.claude_catalog import settings_env
        from cuanta.application.route_apply import MandateRouting

        return MandateRouting(
            advisor=self.route_advisor(ledger),
            policy=self.routing_policy,
            workspace=self.workspace(),
            ledger=ledger,
            environ=os.environ,
            settings_env=lambda: settings_env(self.home, self.project),
            blast_radius=self.blast_radius,
            clock_iso=self.clock.now_iso,
        )

    def final_suite(self, ledger: Ledger, run_id: str) -> str | None:
        from cuanta.domain.errors import NotAvailable

        detection = self.detector().run(with_engines=False)
        try:
            scoped = self.affected_gateway(ledger).run(
                detection.stack, self.config, detection.verify_tier, False, run_id=run_id
            )
        except NotAvailable:
            return None
        return scoped.report.status.value

    def new_file_review(self) -> NewFileReview:
        from cuanta.application.new_files import NewFileReview

        return NewFileReview(self.workspace())

    def terminal_report(self) -> TerminalReport:
        from cuanta.adapters.system.terminal import probe_terminal
        from cuanta.domain.terminal import classify

        return classify(probe_terminal(), os.environ)

    def runs_query(self) -> RunsQuery:
        from cuanta.application.ledger_view import RunsQuery

        return RunsQuery(self.ledger, (self.cuanta_dir() / "ledger.db").is_file)

    def reindex_graph(self) -> tuple[bool, str]:
        from cuanta.adapters.graph.graphify import GraphifyTool

        tool = GraphifyTool(self.runner)
        if not tool.available():
            return False, "graphify is not installed; run cuanta init to set it up"
        result = tool.update(self.project)
        return result.ok, result.detail

    def export_ledger(
        self, fmt: str, table: str, target: Path, include_raw: bool = False, run_id: str = ""
    ) -> tuple[Path, int]:
        from cuanta.application.export import export_file, target_name

        ledger = self.ledger()
        try:
            exported = export_file(ledger, fmt, table, run_id, include_raw)
        finally:
            ledger.close()
        final = Path(target_name(str(target), exported.suffix))
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(exported.data)
        return final, exported.rows

    def known_models(self) -> tuple[str, ...]:
        from cuanta.adapters.system.prices import load_prices

        return load_prices().names()

    def listener(self, linger_s: float = 1.5) -> ListenerControl:
        from cuanta.adapters.telemetry.listener_control import LocalListenerControl

        return LocalListenerControl(
            self.cuanta_dir(),
            self.project,
            self.ledger,
            keep_prompts=self.config.store_prompts,
            linger_s=linger_s,
        )

    def shell(self) -> Shell:
        from cuanta.adapters.system.shell import detect_shell

        return detect_shell()

    def spectrum_query(self, ledger: Ledger) -> SpectrumQuery:
        from cuanta.adapters.system.prices import load_prices
        from cuanta.application.spectrum import SpectrumQuery

        return SpectrumQuery(ledger, load_prices())

    def set_project_value(self, dotted: str, value: object) -> None:
        from cuanta.adapters.system.config_files import set_value

        set_value(project_config_path(self.project), dotted, value)

    def set_global_value(self, dotted: str, value: object) -> None:
        from cuanta.adapters.system.config_files import set_value

        set_value(global_config_path(), dotted, value)

    def instinct_source(self) -> str:
        if "instinct" in layer_from_env(os.environ):
            return "environment"
        if "instinct" in layer_from_table(read_table(project_config_path(self.project))):
            return "project"
        if "instinct" in layer_from_table(read_table(global_config_path())):
            return "global"
        return "default"

    def global_instinct_consent(self) -> tuple[str, ...]:
        value = layer_from_table(read_table(global_config_path())).get("remote_consent", ())
        return tuple(str(item) for item in value) if isinstance(value, tuple) else ()

    def instinct_setup_warning(self) -> Message | None:
        from cuanta.adapters.instinct.jev import JevInstinct

        backend = self.instinct_backend()
        return backend.setup_warning() if isinstance(backend, JevInstinct) else None

    def set_project_path(self, parts: tuple[str, ...], value: object) -> None:
        from cuanta.adapters.system.config_files import set_path

        set_path(project_config_path(self.project), parts, value)

    def persist_port(self, port: int) -> None:
        from cuanta.adapters.system.config_files import set_value

        set_value(project_config_path(self.project), "listener.port", port)

    def telemetry(self) -> TelemetryService:
        from cuanta.adapters.telemetry.config_editors import (
            Backups,
            ClaudeSettingsWiring,
            CodexConfigWiring,
        )
        from cuanta.application.telemetry import TelemetryService

        backups = Backups(self.cuanta_dir() / "backups")
        codex_installed = self.runner.which("codex") is not None
        return TelemetryService(
            project_name=self.project.name,
            port=self.config.port,
            wirings=[
                ClaudeSettingsWiring(self.project, backups),
                CodexConfigWiring(self.home, backups, codex_installed),
            ],
            listener=self.listener(),
            persist_port=self.persist_port,
        )

    def transcript_import(self, ledger: Ledger) -> TranscriptImport:
        from functools import partial

        from cuanta.adapters.telemetry.transcript_importers import (
            CLAUDE_SOURCE,
            CODEX_SOURCE,
            import_claude,
            import_codex,
        )
        from cuanta.application.telemetry import TranscriptImport

        return TranscriptImport(
            ledger,
            [
                (CLAUDE_SOURCE, partial(import_claude, self.home, self.project)),
                (CODEX_SOURCE, partial(import_codex, self.home, self.project)),
            ],
        )

    def cuanta_dir(self) -> Path:
        return self.project / ".cuanta"

    def ledger(self) -> SqliteLedger:
        from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
        from cuanta.application.init_project import ensure_cuanta_dir

        ensure_cuanta_dir(self.workspace())
        ledger = SqliteLedger(self.cuanta_dir() / "ledger.db")
        self._opened.append(ledger)
        return ledger

    def capsule_store(self) -> FileCapsuleStore:
        from cuanta.adapters.storage.capsule_store import FileCapsuleStore

        return FileCapsuleStore(self.cuanta_dir() / "capsules")

    def test_runner(self, name: str) -> TestRunner | None:
        factory = plugin_factories("cuanta.test_runners", BUILTIN_TEST_RUNNERS).get(name)
        if factory is None:
            return None
        runner = factory(self.runner)
        return cast("TestRunner", runner)

    def gateway(self, ledger: Ledger) -> RunGateway:
        from cuanta.application.gateway import RunGateway
        from cuanta.application.instinct import SignatureTriage

        return RunGateway(
            root=self.project,
            runners=self.test_runner,
            ledger=ledger,
            capsules=self.capsule_store(),
            clock=self.clock,
            new_id=self.new_run_id,
            scratch=self.cuanta_dir() / "tmp",
            triage=SignatureTriage(ledger, self.decisions(ledger)),
        )

    def affected_gateway(self, ledger: Ledger) -> AffectedGateway:
        from cuanta.adapters.graph.file_graph import load_neighbours
        from cuanta.application.affected import AffectedGateway

        return AffectedGateway(
            gateway=self.gateway(ledger),
            workspace=self.workspace(),
            ledger=ledger,
            exclusions=frozenset(self.config.exclusions),
            neighbours=lambda: load_neighbours(self.project),
        )

    def model_service(self) -> ModelService:
        from cuanta.adapters.models.claude_catalog import ClaudeCatalog
        from cuanta.adapters.models.codex_catalog import CodexCatalog
        from cuanta.adapters.models.opencode_catalog import OpenCodeCatalog
        from cuanta.adapters.models.tiers import load_tier_table
        from cuanta.adapters.system.prices import load_prices
        from cuanta.application.models import ModelService
        from cuanta.domain.models import Tier, parse_tier

        prices = load_prices()
        claude = self.engine("claude")

        def overrides() -> dict[str, Tier]:
            pairs = load_config(self.project).model_tiers
            return {name: tier for name, value in pairs if (tier := parse_tier(value)) is not None}

        return ModelService(
            catalogs=(
                ClaudeCatalog(
                    self.home,
                    self.project,
                    os.environ,
                    prices,
                    installed=claude is not None and claude.available(),
                ),
                CodexCatalog(self.runner, self.home, os.environ, prices),
                OpenCodeCatalog(self.runner, self.home, self.project),
            ),
            table=load_tier_table(),
            overrides=overrides,
            save_override=lambda key, tier: self.set_project_path(
                ("models", "tiers", key), tier.value
            ),
            workspace=self.workspace(),
            now_iso=self.clock.now_iso,
        )

    def routing_policy(self) -> RoutingPolicy:
        from cuanta.domain.routing import parse_policy

        return parse_policy(dict(load_config(self.project).routing))

    def blast_radius(self, text: str) -> int:
        from cuanta.adapters.graph.file_graph import load_neighbours
        from cuanta.domain.affected import blast_radius

        scan = self.workspace().scan(frozenset(self.config.exclusions), collect_files=True)
        return len(blast_radius(text, scan.files, load_neighbours(self.project)))

    def route_advisor(self, ledger: Ledger) -> RouteAdvisor:
        from cuanta.application.routing import RouteAdvisor

        service = self.model_service()
        return RouteAdvisor(
            decisions=self.decisions(ledger),
            catalog=lambda: service.view().entries,
            ledger=ledger,
            clock_iso=self.clock.now_iso,
        )

    def latest_tests(self) -> LatestTests:
        from cuanta.application.tests_view import LatestTests

        return LatestTests(self.ledger, (self.cuanta_dir() / "ledger.db").is_file)

    def cat_capsule(self, ledger: Ledger) -> CatCapsule:
        from cuanta.application.cat_capsule import CatCapsule

        return CatCapsule(ledger, self.capsule_store())

    def copy_to_clipboard(self, text: str) -> bool:
        from cuanta.adapters.system.clipboard import copy_native

        return copy_native(text)

    def home_query(self) -> HomeQuery:
        from datetime import date

        from cuanta.application.home import HomeQuery

        kit = self.forge_kit()
        return HomeQuery(
            self.doctor(),
            self.ledger,
            (self.cuanta_dir() / "ledger.db").is_file,
            kit.vendored_version,
            date.today,
        )

    def doctor(self) -> Doctor:
        from cuanta.adapters.engines.claude_plugins import installed_plugins
        from cuanta.adapters.instinct.jev import JevInstinct
        from cuanta.adapters.storage.migrations import LATEST_VERSION
        from cuanta.application import doctor

        workspace = self.workspace()
        jev = JevInstinct()
        return doctor.Doctor(
            self.detector(),
            [
                doctor.python_check,
                doctor.engines_check,
                doctor.verify_check,
                doctor.graph_check,
                doctor.engine_flags_check(self.engine),
                doctor.forge_state_check(workspace),
                doctor.forge_version_check(self.forge_kit()),
                doctor.user_forge_check(
                    lambda: installed_plugins(self.home), self.forge_kit().vendored_version
                ),
                doctor.session_check(self.ledger, (self.cuanta_dir() / "ledger.db").is_file),
                doctor.instinct_check(
                    self.config.instinct,
                    jev.available,
                    jev.setup_warning,
                    self.ledger,
                    (self.cuanta_dir() / "ledger.db").is_file,
                ),
                doctor.rulebook_check(workspace),
                doctor.listener_check(self.listener()),
                doctor.telemetry_check(lambda: self.telemetry().status().engines),
                doctor.terminal_check(self.terminal_report),
                doctor.ledger_check(
                    self.ledger,
                    LATEST_VERSION,
                    (self.cuanta_dir() / "ledger.db").is_file,
                ),
                doctor.cuanta_dir_check(workspace),
            ],
        )
