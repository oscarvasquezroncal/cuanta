from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import secrets
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, cast

from cuanta.adapters.system.clock import SystemClock
from cuanta.adapters.system.config_files import global_config_path, project_config_path, read_table
from cuanta.adapters.system.platform import home_dir
from cuanta.adapters.system.process_runner import SubprocessRunner
from cuanta.adapters.system.workspace import LocalHome, LocalWorkspace
from cuanta.domain.config import Config, layer_from_env, layer_from_table, merge
from cuanta.domain.ids import make_run_id
from cuanta.domain.sandbox import STATE_ROOT_ENV
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
    from cuanta.adapters.system.git_files import GitView
    from cuanta.application.affected import AffectedGateway
    from cuanta.application.assistant import Improvement, PromptAssistant
    from cuanta.application.bench import Attempt, BenchResult, BenchRunner
    from cuanta.application.cache_probe import CacheProbeReport
    from cuanta.application.cache_state import PrefixQuery
    from cuanta.application.cat_capsule import CatCapsule
    from cuanta.application.code_index import IndexService
    from cuanta.application.costs import CostsQuery
    from cuanta.application.cross_engine import CrossEnginePipeline, CrossReport
    from cuanta.application.cross_files import CodexFileGuard
    from cuanta.application.detect import DetectProject
    from cuanta.application.doctor import Doctor
    from cuanta.application.drafts import Drafts
    from cuanta.application.engine_run import EngineLauncher, LaunchSpec
    from cuanta.application.estimate import Estimate
    from cuanta.application.file_costs import FileCostsQuery
    from cuanta.application.forecast import Forecaster, JevEnvelope, PlannedForecast, PlanSizes
    from cuanta.application.gateway import RunGateway
    from cuanta.application.home import HomeQuery
    from cuanta.application.implementer import ImplementationSession
    from cuanta.application.index_read import IndexRead
    from cuanta.application.index_summaries import SummaryResult
    from cuanta.application.init_project import InitProject
    from cuanta.application.instinct import DecisionMaker, DecisionScope
    from cuanta.application.instinct_view import JevCard
    from cuanta.application.intake import IntakeService
    from cuanta.application.ledger_view import RunsQuery
    from cuanta.application.mandate import MandateReport, MandateService
    from cuanta.application.mandate_flow import MandateFlow, MandateOptions, Prepared
    from cuanta.application.mandate_template import MandateTemplates
    from cuanta.application.map import MapQuery
    from cuanta.application.mcp import McpServer
    from cuanta.application.models import ModelService, ProbeOutcome
    from cuanta.application.new_files import NewFileReview
    from cuanta.application.outcomes import RunOutcomes
    from cuanta.application.progress import SlowSteps
    from cuanta.application.queue import MandateQueue
    from cuanta.application.refresh import RefreshProject
    from cuanta.application.results import ResultQuery
    from cuanta.application.route_apply import MandateRouting
    from cuanta.application.routing import RouteAdvisor, RoutePlan
    from cuanta.application.sandbox import SandboxResult, SandboxRunner
    from cuanta.application.session_profile import LeanProfile
    from cuanta.application.spectrum import SpectrumQuery
    from cuanta.application.steering import GovernorSetup
    from cuanta.application.telemetry import TelemetryService, TranscriptImport
    from cuanta.application.tests_view import LatestTests
    from cuanta.application.trials import TrialStore
    from cuanta.application.verification import Verifier
    from cuanta.domain.anatomy import AnatomyReport
    from cuanta.domain.assistant import Suggestions
    from cuanta.domain.bench import BenchTask, Condition, PlannedRun, ProofRecord
    from cuanta.domain.bench_proof import Arm
    from cuanta.domain.change_plan import ChangePlan
    from cuanta.domain.code_index import IndexRow, IndexStatus
    from cuanta.domain.engine import EngineEvent
    from cuanta.domain.estimates import RunEstimate
    from cuanta.domain.governor_report import BlockedCalls
    from cuanta.domain.instinct import Choice
    from cuanta.domain.ledger import LedgerEvent, Run
    from cuanta.domain.limits import LimitSettings, RunLimits
    from cuanta.domain.mandate import MandateRequest
    from cuanta.domain.messages import Message
    from cuanta.domain.overhead import SessionOverhead
    from cuanta.domain.pack import ContextPack
    from cuanta.domain.pricing import PriceTable
    from cuanta.domain.role_budgets import OvershootMargins, RepairBudget
    from cuanta.domain.routing import CostRange, Provider, RoutingPolicy
    from cuanta.domain.sandbox import SandboxLaunch
    from cuanta.domain.scout import DocsChoice, ShapeChoice
    from cuanta.domain.shells import Shell
    from cuanta.domain.terminal import TerminalReport
    from cuanta.domain.verification import VerificationPolicy, VerificationResult
    from cuanta.ports.engine import Engine
    from cuanta.ports.instinct import Instinct
    from cuanta.ports.ledger import Ledger
    from cuanta.ports.listener import ListenerControl
    from cuanta.ports.sandbox import SandboxCopy
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
    state_root: Path | None = None
    extra_env: tuple[tuple[str, str], ...] = ()
    run_mode: str = ""
    verbose: bool = False
    request_files: tuple[str, ...] = ()
    _shared: Ledger | None = field(default=None, repr=False)
    _opened: list[Ledger] = field(default_factory=list, repr=False)
    decision_scope: DecisionScope = field(default_factory=_new_scope, repr=False)
    _pack_cache: dict[str, ContextPack] = field(default_factory=dict, repr=False)
    _fast_ready: dict[str, bool] = field(default_factory=dict, repr=False)
    _issued: list[str] = field(default_factory=list, repr=False)
    progress: ProgressSink | None = field(default=None, repr=False)
    _index_reported: bool = field(default=False, init=False, repr=False)
    _workspace: LocalWorkspace | None = field(default=None, init=False, repr=False)
    _jev: Instinct | None = field(default=None, init=False, repr=False)

    @classmethod
    def for_project(cls, project: Path, verbose: bool = False) -> Container:
        state = os.environ.get(STATE_ROOT_ENV, "").strip()
        return cls(
            project=project,
            config=load_config(project),
            state_root=Path(state) if state else None,
            verbose=verbose,
        )

    def new_run_id(self) -> str:
        run_id = make_run_id(self.clock.now_ms(), secrets.token_bytes(10))
        self._issued.append(run_id)
        return run_id

    def recorded_run(self) -> str:
        from cuanta.domain.ledger import launched_run

        if not self._issued:
            return ""
        if self._shared is None and not (self.cuanta_dir() / "ledger.db").is_file():
            return ""
        ledger = self.shared_ledger()
        for run_id in reversed(self._issued):
            run = ledger.get_run(run_id)
            if run is not None:
                return launched_run(run, ledger.get_run).id
        return ""

    def workspace(self) -> LocalWorkspace:
        if self._workspace is None:
            self._workspace = LocalWorkspace(self.project, self.state_project(), self.home)
        return self._workspace

    def git_view(self) -> GitView:
        from cuanta.adapters.system.git_files import load_git_view

        return load_git_view(self.state_project(), self.home, os.environ)

    def use_request_files(self, paths: Sequence[Path]) -> None:
        root = self.project.resolve()
        found: list[str] = []
        for path in paths:
            try:
                found.append(path.expanduser().resolve().relative_to(root).as_posix())
            except ValueError:
                continue
        self.request_files = tuple(dict.fromkeys(found))

    def index_service(self, rebuild: bool = False) -> IndexService:
        from cuanta.adapters.graph.file_graph import graph_path
        from cuanta.adapters.graph.index_ast import AstIndexExtractor
        from cuanta.adapters.graph.index_graph import LocalIndexGraph
        from cuanta.adapters.storage.sqlite_index import SqliteIndex
        from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
        from cuanta.adapters.system.index_inventory import LocalIndexInventory
        from cuanta.adapters.system.index_knowledge import LocalIndexKnowledge
        from cuanta.application.code_index import IndexService, indexable_paths
        from cuanta.domain.messages import msg
        from cuanta.domain.progress import INDEX_STEP, file_count

        database = self.project / ".cuanta" / "index.db"
        inventory = LocalIndexInventory(
            self.project, frozenset(self.config.exclusions), self.git_view
        )
        if rebuild or not database.exists():
            with self.slow_steps().step(INDEX_STEP, msg("progress.index")) as done:
                paths = indexable_paths(inventory, self.config.exclusions)
                done(file_count(len(paths)))
        index = SqliteIndex(database, rebuild=rebuild)
        ledger = None
        state = self.state_project()
        ledger_path = graph_path(state, ".cuanta/ledger.db")
        history_status = "missing"
        if ledger_path is not None:
            safe = all(
                not os.path.lexists(state / relative) or graph_path(state, relative) is not None
                for relative in (".cuanta/ledger.db-wal", ".cuanta/ledger.db-shm")
            )
            if safe:
                try:
                    ledger = SqliteLedger(ledger_path, read_only=True)
                    history_status = "available"
                except (OSError, ValueError, sqlite3.Error) as error:
                    history_status = "unavailable:" + type(error).__name__
            else:
                history_status = "unavailable:linked-state"
        elif os.path.lexists(state / ".cuanta/ledger.db"):
            history_status = "unavailable:linked-state"
        index.set_meta({"history_status": history_status})

        def commands() -> tuple[str, ...]:
            stack = self.detector().run(with_engines=False).stack
            package = inventory.read("package.json")
            try:
                data = json.loads(package or "{}")
                scripts = data.get("scripts", {}) if isinstance(data, dict) else {}
            except ValueError:
                scripts = {}
            lint = (
                f"{stack.package_manager or 'npm'} run lint"
                if isinstance(scripts, dict) and isinstance(scripts.get("lint"), str)
                else ""
            )
            return tuple(
                dict.fromkeys(
                    value
                    for value in (
                        stack.typecheck_command,
                        lint,
                        stack.build_command,
                        stack.test_command,
                    )
                    if value
                )
            )

        return IndexService(
            index,
            inventory,
            self.clock.now_iso,
            recovered=index.recovered,
            extractor=AstIndexExtractor(),
            graph=LocalIndexGraph(self.project, self.runner),
            knowledge=LocalIndexKnowledge(self.project, self.state_project(), ledger),
            verify_commands=commands,
            close_knowledge=ledger.close if ledger else None,
            exclusions=self.config.exclusions,
            reclaimed=lambda: index.reclaimed_bytes,
        )

    def refresh_index(self) -> None:
        if not self.config.index_enabled:
            return
        service = self.index_service()
        try:
            self.indexed(service)
        finally:
            service.close()

    def slow_steps(self) -> SlowSteps:
        from cuanta.application.progress import SlowSteps

        return SlowSteps(self.progress)

    def indexed(self, service: IndexService) -> IndexStatus:
        from cuanta.domain.messages import msg
        from cuanta.domain.progress import INDEX_STEP, file_count

        if self._index_reported:
            return service.update()
        self._index_reported = True
        with self.slow_steps().step(INDEX_STEP, msg("progress.index")) as done:
            status = service.update()
            done(file_count(status.files))
        return status

    def index_reader(self) -> IndexRead:
        from cuanta.application.index_read import IndexRead

        return IndexRead(self.index_service(), self.clock.now_iso)

    def map_query(self, rebuild: bool = False) -> MapQuery:
        from cuanta.application.index_read import IndexRead
        from cuanta.application.map import MapQuery

        return MapQuery(IndexRead(self.index_service(rebuild), self.clock.now_iso))

    def learn_run(self, run_id: str, notes_root: Path | None = None) -> None:
        if not self.config.index_enabled:
            return
        from cuanta.adapters.graph.file_graph import graph_path
        from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
        from cuanta.application.index_learning import IndexLearning
        from cuanta.application.run_reports import RunReports
        from cuanta.domain.index_facts import revalidate_fact
        from cuanta.domain.index_limit import IndexTooLarge

        state = self.state_project()
        path = graph_path(state, ".cuanta/ledger.db")
        if path is None or any(
            os.path.lexists(state / name) and graph_path(state, name) is None
            for name in (".cuanta/ledger.db-wal", ".cuanta/ledger.db-shm")
        ):
            return
        known = False
        try:
            existing = SqliteLedger(path, read_only=True)
            try:
                run = existing.get_run(run_id)
                if run is None or not run.ended_at:
                    return
                known = True
                report_ids = tuple(
                    child.id
                    for child in existing.runs(kind=run.kind, since=run.started_at)
                    if child.parent_id == run_id
                )
            finally:
                existing.close()
            notes: tuple[IndexRow, ...] = ()
            if notes_root is not None and graph_path(notes_root, ".cuanta/index.db") is not None:
                source = Container(notes_root, self.config, runner=self.runner).index_service()
                try:
                    files = {item.path: item for item in source.index.files()}
                    notes = tuple(
                        verified
                        for row in source.index.rows("notes")
                        if row.provenance.startswith("agent-note:")
                        and not (
                            verified := revalidate_fact(
                                row, files.get(row.path), source.inventory.read(row.path)
                            )
                        ).stale
                    )
                finally:
                    source.close()
            service = self.index_service()
            try:
                IndexLearning(service, self.state_workspace()).run(run_id, notes, report_ids)
            finally:
                service.close()
        except (OSError, ValueError, sqlite3.Error, IndexTooLarge) as error:
            if not known:
                return
            reports = RunReports(self.state_workspace())
            meta = reports.meta(run_id) or {}
            reports.save_meta(run_id, {**meta, "index_learning_error": type(error).__name__})

    def change_plan(self, request: MandateRequest) -> ChangePlan:
        from cuanta.adapters.system.index_inventory import LocalIndexInventory
        from cuanta.application.change_plan import IndexChangePlan
        from cuanta.domain.change_plan import compile_change_plan

        excluded = frozenset(self.request_files)
        if not self.config.index_enabled:
            inventory = LocalIndexInventory(
                self.project, frozenset(self.config.exclusions), self.git_view
            )
            candidates = inventory.candidates()
            return compile_change_plan(
                request, candidates, (), (), (), (), (), (), excluded=excluded
            )
        reader = self.index_reader()
        try:
            self.indexed(reader.service)
            return IndexChangePlan(reader.service.index, self.clock.now_iso, excluded).compile(
                request
            )
        finally:
            reader.close()

    def context_pack(
        self,
        request: MandateRequest,
        depth: str = "",
        role: str = "",
        plan: ChangePlan | None = None,
    ) -> ContextPack:
        from cuanta.application.context_pack import IndexContextPack

        reader = self.index_reader()
        try:
            self.indexed(reader.service)
            return IndexContextPack(
                reader, self._pack_cache, frozenset(self.request_files)
            ).compile(request, depth, role, plan)
        finally:
            reader.close()

    def pack_notes(
        self, request: MandateRequest, depth: str, plan: ChangePlan
    ) -> tuple[Message, ...]:
        from cuanta.application.mandate_flow import pack_notes

        enabled = self.config.index_enabled and self.config.pack_enabled
        pack = self.context_pack(request, depth, "", plan) if enabled else None
        return pack_notes(request, plan, pack)

    def index_reranker(self) -> DecisionMaker | None:
        from cuanta.application.instinct import consent_ok
        from cuanta.ports.instinct import BatchInstinct

        if not self.config.instinct_share_paths or self.config.instinct != "jev":
            return None
        backend = self.instinct_backend()
        if (
            not isinstance(backend, BatchInstinct)
            or not consent_ok(backend, self.config.remote_consent)
            or not backend.available()[0]
        ):
            return None
        return self.decisions(self.ledger())

    def index_summaries(self, service: IndexService, confirmed: bool) -> SummaryResult:
        from cuanta.application.engine_run import LaunchSpec
        from cuanta.application.index_summaries import IndexSummaries
        from cuanta.domain.errors import NotAvailable
        from cuanta.domain.models import Tier, probe_cost
        from cuanta.domain.routing import candidates

        entries = self.model_service().view().entries
        available = candidates(entries, ("claude",), Tier.ECONOMY)
        chosen = available[0] if available else None
        if chosen is None:
            raise NotAvailable("no economy-tier model in the catalog", "run cuanta models refresh")

        def launch(prompt: str, cap: float) -> tuple[str, float | None]:
            engine = self.engine(chosen.engine)
            if engine is None or not engine.available():
                raise NotAvailable(f"{chosen.engine} not found on PATH", "install it first")
            ledger = self.ledger()
            result = self.launcher(engine, ledger, telemetry=False).launch(
                LaunchSpec(
                    kind="index-summary",
                    prompt=prompt,
                    cwd=str(self.project),
                    allowed_tools=(),
                    tools=(),
                    read_only=False,
                    persist_session=False,
                    model=chosen.resolved or chosen.id,
                    max_budget_usd=cap,
                    max_turns=1,
                ),
                lambda _: None,
            )
            text = result.outcome.result.text if result.outcome and result.outcome.result else ""
            return text, result.run.cost_usd

        return IndexSummaries(
            service,
            chosen.key,
            lambda prompt, output: probe_cost(chosen, len(prompt.encode()) // 4 + 1, output),
            launch,
        ).run(confirmed)

    def state_project(self) -> Path:
        return self.state_root or self.project

    def state_workspace(self) -> LocalWorkspace:
        return LocalWorkspace(self.state_project(), home=self.home)

    def sandbox_container(
        self, copy_root: Path, env: tuple[tuple[str, str], ...] = ()
    ) -> Container:
        return replace(self, project=copy_root, state_root=self.state_project(), extra_env=env)

    def classic(self) -> None:
        from cuanta.domain.run_mode import CLASSIC, classic_config

        self.config = classic_config(self.config)
        self.run_mode = CLASSIC

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
        from cuanta.adapters.graph.index_graph import LocalIndexGraph
        from cuanta.application.forge import ForgeStage, VerifyStage
        from cuanta.application.init_project import GraphStage, HandoffWriter, InitProject
        from cuanta.application.mandate_template import MandateTemplates
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
            graph_stage=GraphStage(
                workspace, GraphifyTool(self.runner), LocalIndexGraph(self.project, self.runner)
            ),
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
                templates=MandateTemplates(workspace, kit),
            ),
            verify_stage=VerifyStage(workspace, self.ledger, self.clock),
            run_id_factory=self.new_run_id,
            monotonic=self.clock.monotonic,
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

    def mandate_templates(self) -> MandateTemplates:
        from cuanta.application.mandate_template import MandateTemplates

        return MandateTemplates(self.state_workspace(), self.forge_kit())

    def engine(self, name: str) -> Engine | None:
        factory = plugin_factories("cuanta.engines", BUILTIN_ENGINES).get(name)
        if factory is None:
            return None
        return cast("Engine", factory(self.runner))

    def fast_ready(self, name: str) -> bool:
        from cuanta.ports.engine import TurnInput

        if name not in self._fast_ready:
            engine: object = self.engine(name)
            steerable = isinstance(engine, TurnInput) and engine.accepts_turns()
            self._fast_ready[name] = steerable and bool(self.static_checks())
        return self._fast_ready[name]

    def static_checks(self) -> tuple[str, ...]:
        stack = self.detector().run(with_engines=False).stack
        package = self.workspace().read_text("package.json")
        try:
            parsed = json.loads(package or "{}")
        except ValueError:
            parsed = {}
        scripts = parsed.get("scripts", {}) if isinstance(parsed, dict) else {}
        lint = (
            f"{stack.package_manager or 'npm'} run lint"
            if isinstance(scripts, dict) and isinstance(scripts.get("lint"), str)
            else ""
        )
        return tuple(command for command in (stack.typecheck_command, lint) if command)

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
            reports=RunReports(self.state_workspace()),
            lean_files=self.lean_profile().files,
            default_session=self.config.run_session,
            guard_files=self.guard_profile,
            index_tools=self.config.index_enabled and self.config.index_tools,
            index_server=self.index_server_command(),
            implementer=lambda spec: self.implementation_session(spec, ledger),
        )

    def implementation_session(
        self, spec: LaunchSpec, ledger: Ledger
    ) -> ImplementationSession | None:
        from cuanta.application.implementer import ImplementationSession, VerificationBaselines
        from cuanta.application.timing import PhaseRecorder
        from cuanta.domain.errors import DomainFailure
        from cuanta.domain.implementation import (
            DIAGNOSTICS_VERSION,
            baseline_key,
            unavailable_checks,
        )

        if spec.read_only or spec.kind not in {"mandate", "cross"}:
            return None
        stack = self.detector().run(with_engines=False).stack
        commands = tuple(
            dict.fromkeys(
                command
                for command in (
                    *self.static_checks(),
                    *spec.verify_commands,
                    stack.build_command if "build" in spec.prompt.casefold() else "",
                )
                if command
            )
        )
        if not commands:
            if spec.implementation_steps:
                raise DomainFailure("ordered implementation requires verification commands")
            return None
        verifier = self.verifier()
        timing = PhaseRecorder(self.clock, ledger)
        snapshot = self.project_snapshot()
        for name in ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "uv.lock"):
            digest = self.workspace().sha256(name)
            if digest is not None:
                snapshot[name] = digest
        context = baseline_key(
            dict(self.extra_env),
            (stack.language_version, stack.framework_version),
            DIAGNOSTICS_VERSION,
        )
        with timing.measure("verification", spec.run_id, spec.role):
            baseline = VerificationBaselines(self.state_workspace()).get(
                snapshot, commands, verifier.run_checks, lambda: False, context
            )
        unavailable = unavailable_checks(baseline, commands)
        if unavailable:
            raise DomainFailure(
                "verification is unusable, so the writer was not started: "
                + "; ".join(unavailable),
                "install the missing tool, or make the check pass or print its failures, "
                "then run again",
            )
        return ImplementationSession(
            commands,
            baseline,
            verifier.run_checks,
            self.clock.monotonic,
            spec.max_budget_usd,
            self.config.repair_rounds,
            self.config.repair_timeout_s,
            spec.implementation_steps,
            spec.max_turns,
            measured=lambda phase, seconds: timing.seconds(phase, seconds, spec.run_id, spec.role),
            snapshot=self.project_snapshot,
        )

    def guard_profile(self, spec: LaunchSpec) -> tuple[str, str]:
        import shlex
        import sys

        from cuanta.domain.change_plan import EXECUTION, ChangePlan, deny_rules
        from cuanta.domain.read_discipline import READ_LINE_LIMIT

        plan = spec.change_plan or ChangePlan(read_only=spec.read_only)
        denied = tuple(
            dict.fromkeys(
                (
                    *deny_rules(plan),
                    *(EXECUTION if plan.guard or plan.read_only else ()),
                )
            )
        )
        hooks: dict[str, object] | None = None
        disciplined = (
            self.config.read_discipline if spec.read_discipline is None else spec.read_discipline
        )
        limit = self.config.read_max_lines
        extra = [str(limit)] if limit != READ_LINE_LIMIT else []
        if disciplined:
            hooks = {}
            for mode, event in (("pre", "PreToolUse"), ("post", "PostToolUse")):
                command = shlex.join(
                    [Path(sys.executable).as_posix(), "-m", "cuanta.cli.hooks", mode, *extra]
                )
                hooks[event] = [
                    {
                        "matcher": "Read|Grep|Bash|PowerShell",
                        "hooks": [{"type": "command", "command": command, "timeout": 10}],
                    }
                ]
        wanted = self.config.index_tools if spec.index_tools is None else spec.index_tools
        mcp = self.index_mcp_config() if self.config.index_enabled and wanted else None
        return self.lean_profile().files(
            permissions_deny=denied,
            owned_hooks=hooks,
            owned_mcp=mcp,
            model=spec.model,
            pure=spec.pure,
            variant=spec.variant,
        )

    def index_server_command(self) -> tuple[str, ...]:
        import sys

        if not self.config.index_enabled:
            return ()
        return (sys.executable, "-m", "cuanta", "--project", str(self.project), "mcp", "serve")

    def index_mcp_config(self) -> dict[str, object]:
        import sys

        return {
            "mcpServers": {
                "cuanta": {
                    "type": "stdio",
                    "command": sys.executable,
                    "args": ["-m", "cuanta", "--project", str(self.project), "mcp", "serve"],
                }
            }
        }

    def mcp_server(self, run_id: str = "") -> McpServer:
        from cuanta import __version__
        from cuanta.application.index_tools import TOOLS, IndexTools
        from cuanta.application.mcp import McpServer

        reader = self.index_reader()
        tools = IndexTools(reader)
        sequence = 0
        nonce = secrets.token_hex(8)

        def observe(
            name: str, arguments: Mapping[str, object], result: object, elapsed: float
        ) -> None:
            nonlocal sequence
            sequence += 1
            self.record_mcp_call(run_id, name, arguments, result, elapsed, f"{nonce}:{sequence}")

        def initialized(version: str, client: Mapping[str, object]) -> None:
            self.record_mcp_call(
                run_id,
                "initialize",
                {},
                {
                    "protocol_version": version,
                    "client_name": client.get("name", ""),
                    "client_version": client.get("version", ""),
                },
                0,
                f"{nonce}:initialize",
            )

        return McpServer(tools.call, TOOLS, __version__, observe, initialized, close=reader.close)

    def record_mcp_call(
        self,
        run_id: str,
        name: str,
        arguments: Mapping[str, object],
        result: object,
        elapsed: float,
        identity: str,
    ) -> None:
        from contextlib import suppress

        from cuanta.adapters.graph.file_graph import graph_path
        from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
        from cuanta.domain.code_index import index_path
        from cuanta.domain.ledger import LedgerEvent
        from cuanta.domain.spectrum import estimated_tokens
        from cuanta.domain.stable import stable_json

        if not run_id:
            return
        path = graph_path(self.state_project(), ".cuanta/ledger.db")
        state = self.state_project()
        if path is None or any(
            os.path.lexists(state / relative) and graph_path(state, relative) is None
            for relative in (".cuanta/ledger.db-wal", ".cuanta/ledger.db-shm")
        ):
            return
        relative = ""
        candidate = arguments.get("path")
        if isinstance(candidate, str):
            with suppress(ValueError):
                relative = index_path(candidate)
        encoded = json.dumps(
            result, ensure_ascii=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        metadata: dict[str, object] = {"returned_tokens_estimate": estimated_tokens(len(encoded))}
        if name == "initialize" and isinstance(result, dict):
            metadata.update(
                {
                    key: value
                    for key, value in result.items()
                    if isinstance(value, str) and len(value) <= 100
                }
            )
        with suppress(OSError, ValueError, sqlite3.Error):
            existing = SqliteLedger(path, read_only=True)
            try:
                known = existing.get_run(run_id) is not None
            finally:
                existing.close()
            if not known:
                return
            ledger = SqliteLedger(path)
            try:
                ledger.add_events(
                    (
                        LedgerEvent(
                            run_id=run_id,
                            source="cuanta_mcp",
                            agent="uncertain",
                            kind="index_handshake" if name == "initialize" else "index_call",
                            tool_name="mcp__cuanta__" + name,
                            tool_use_id=identity,
                            tool_result_bytes=len(encoded),
                            duration_ms=max(0, round(elapsed * 1000)),
                            success=not (
                                isinstance(result, dict)
                                and (result.get("isError") is True or "error" in result)
                            ),
                            file_path=relative,
                            ts=self.clock.now_iso(),
                            raw=stable_json(metadata),
                        ),
                    )
                )
            finally:
                ledger.close()

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
            if self._jev is None:
                self._jev = JevInstinct(
                    timeout_s=self.config.instinct_timeout_s,
                    scope=lambda: self.decision_scope.run_id or self.decision_scope.request_hash,
                )
            return self._jev
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
        spend: float | None = 0.0
        count = 0
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

    def probe_cache_ttl(
        self,
        gaps_text: str,
        budget_usd: float,
        per_run_usd: float,
        tools: tuple[str, ...],
        model: str,
        spend: bool,
        keep: bool,
        save: bool,
        progress: ProgressSink,
    ) -> CacheProbeReport:
        from cuanta.adapters.engines.claude_code import PROBE_FLAGS, ClaudeCodeEngine
        from cuanta.adapters.system.prices import load_prices
        from cuanta.adapters.system.scratch import discard_scratch, scratch_project
        from cuanta.application.cache_probe import (
            CacheProbeOptions,
            CacheProbeReport,
            CacheTtlProbe,
        )
        from cuanta.domain.cache_probe import parse_gaps, plan_ceiling
        from cuanta.domain.errors import DomainFailure, NotAvailable
        from cuanta.domain.models import Tier, find
        from cuanta.domain.routing import candidates

        gaps = parse_gaps(gaps_text)
        if budget_usd <= 0 or per_run_usd <= 0 or per_run_usd > budget_usd:
            raise DomainFailure("the per-run cap must be positive and no larger than the total cap")
        root = scratch_project()
        sub = replace(
            Container.for_project(root), runner=self.runner, clock=self.clock, home=self.home
        )
        try:
            entries = sub.model_service().view().entries
            if model:
                chosen = find(entries, model)
                if chosen is None or chosen.engine != "claude":
                    raise DomainFailure(f"unknown Claude model {model}", "run cuanta models list")
            else:
                matches = candidates(entries, ("claude",), Tier.ECONOMY)
                if not matches:
                    raise NotAvailable(
                        "no Claude economy model in the catalog", "run cuanta models refresh"
                    )
                chosen = matches[0]
            resolved = chosen.resolved or chosen.id
            price = load_prices().lookup(resolved)
            plan = plan_ceiling(price, gaps, budget_usd, per_run_usd)
            expected_auth = "api" if os.environ.get("ANTHROPIC_API_KEY") else "unknown"
            if not spend:
                return CacheProbeReport(plan=plan, model=resolved, expected_auth=expected_auth)
            engine = sub.engine("claude")
            if engine is None or not engine.available():
                raise NotAvailable("claude not found on PATH", "install Claude Code first")
            if isinstance(engine, ClaudeCodeEngine):
                missing = [flag for flag in PROBE_FLAGS if flag not in engine.help_text()]
                if missing:
                    raise NotAvailable(
                        f"Claude Code lacks {', '.join(missing)}", "upgrade Claude Code"
                    )
            ledger = sub.ledger()
            probe = CacheTtlProbe(
                sub.launcher(engine, ledger, telemetry=False),
                str(root),
                self.clock,
                lambda: date.today().isoformat(),
                secrets.token_hex(6),
                price,
            )
            result = probe.run(
                CacheProbeOptions(resolved, gaps, budget_usd, per_run_usd, tools), progress
            )
            saved = save and result.saveable
            if saved:
                values: dict[str, object] = {
                    "cache.ttl_s": result.ttl_s,
                    "cache.auth": result.auth.value,
                    "cache.engine_version": result.engine_version,
                    "cache.measured_on": result.measured_on,
                    "cache.model": result.model,
                    "cache.ttl_lower_s": result.lower_s,
                    "cache.ttl_upper_s": result.upper_s or 0,
                    "cache.verdict": result.verdict.value,
                    "cache.api_key_source": result.api_key_source,
                    "cache.engine": "claude",
                    "cache.tools": ",".join(result.tools),
                }
                for key, value in values.items():
                    self.set_global_value(key, value)
            return CacheProbeReport(
                plan=plan,
                model=resolved,
                expected_auth=expected_auth,
                result=result,
                saved=saved,
                scratch_path=str(root) if keep else "",
            )
        finally:
            sub.close()
            if not keep or not spend:
                discard_scratch(root)

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
        engine: str = "",
    ) -> tuple[RoutePlan, CostRange]:
        from cuanta.application.estimate import similar_costs
        from cuanta.application.routing import RouteInputs, with_overrides
        from cuanta.domain.depth import parse_depth, profile
        from cuanta.domain.mandate import MandateRequest as Request
        from cuanta.domain.routing import cost_range, depth_capped, roles_that_run

        policy = with_overrides(self.routing_policy(), route, preset, role_models)
        if engine:
            policy = replace(policy, engines=(engine,))
        if depth:
            policy = depth_capped(policy, profile(parse_depth(depth), task_type).tier_cap)
        if scope is None:
            self.preview_scope(Request(type=task_type, what=what, where=where))
        ledger = self.shared_ledger()
        latest = ledger.test_runs(limit=1, project_only=True)
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

    def docs_choice(self, request: MandateRequest, options: MandateOptions) -> DocsChoice | None:
        from cuanta.domain.mandate import INVESTIGATION
        from cuanta.domain.routing import Role
        from cuanta.domain.scout import docs_choice, docs_setting, has_pin, parse_docs_mode

        if options.simple or request.type == INVESTIGATION:
            return None
        mode, flag = docs_setting(options.docs, parse_docs_mode(self.config.docs_mode))
        return docs_choice(
            mode,
            request,
            options.sandbox,
            has_pin(options.route.role_models, Role.DOCS),
            flag,
        )

    def shape_plan(self, plan: RoutePlan, shape: ShapeChoice, docs: DocsChoice | None) -> RoutePlan:
        from cuanta.application.routing import without_role
        from cuanta.domain.routing import Role

        found = self.route_advisor(self.shared_ledger()).scout_plan(plan) if shape.scout else plan
        return without_role(found, Role.DOCS) if docs is not None and not docs.on else found

    def pure_plan(self, plan: RoutePlan, model: str) -> RoutePlan:
        return self.route_advisor(self.shared_ledger()).pure(plan, model)

    def main_model_plan(self, plan: RoutePlan, model: str) -> RoutePlan:
        return self.route_advisor(self.shared_ledger()).main_model(plan, model)

    def team_shape(
        self, request: MandateRequest, options: MandateOptions, cross_engine: bool = False
    ) -> ShapeChoice:
        from cuanta.application.mandate_flow import launch_turns
        from cuanta.application.routing import route_model, with_overrides
        from cuanta.domain.change_plan import apply_overrides
        from cuanta.domain.errors import CuantaError, DomainFailure
        from cuanta.domain.mandate import INVESTIGATION, Shape
        from cuanta.domain.messages import english
        from cuanta.domain.routing import Role, RouteMode, parse_provider
        from cuanta.domain.scout import (
            ScoutMode,
            ShapeChoice,
            choose_shape,
            eligible,
            exploration_share,
            parse_forced_shape,
            parse_scout_mode,
            pinned_shape,
            pure_shape,
            scout_refusal,
        )
        from cuanta.domain.team import runs_per_role

        threshold = self.config.scout_threshold
        forced = parse_forced_shape(options.shape)
        engine = options.engine or self.config.engine
        provider = parse_provider(engine)
        route = options.route
        routed = with_overrides(self.routing_policy(), route.mode).mode is not RouteMode.OFF
        refusal = scout_refusal(request.type, forced, options.simple, provider is not None, routed)
        if refusal is not None:
            raise DomainFailure(english(refusal[0]), refusal[1])
        per_role = cross_engine or runs_per_role(engine, True)
        mode = ScoutMode.LAUNCH if per_role else parse_scout_mode(self.config.scout_mode)
        if forced is not None or provider is None:
            return choose_shape(request.type, forced, None, threshold, mode)
        by_pin = pinned_shape(route.role_models)
        if by_pin is not None and routed and not options.simple and request.type != INVESTIGATION:
            return ShapeChoice(by_pin, threshold=threshold, mode=mode, pinned=True)
        if not routed or not eligible(request.type, options.simple):
            return choose_shape(request.type, None, None, threshold, mode)
        plan, _ = self.plan_route(
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
            engine=engine,
        )
        docs = self.docs_choice(request, options)
        plan = self.shape_plan(plan, ShapeChoice(Shape.PIPELINE), docs)
        if options.pure:
            plan = self.pure_plan(
                plan, options.model or ("" if per_role else route_model(plan, Role.ORCHESTRATOR))
            )
            skipped = pure_shape(plan.pure, threshold, mode)
            if skipped is not None:
                return skipped
        limits = self.run_limits(options, request.type)
        try:
            forecast = self.team_forecast(
                request.type,
                plan,
                provider,
                options.depth,
                Shape.PIPELINE.value,
                limits.budget_usd,
                apply_overrides(self.change_plan(request), options.plan_overrides),
                native=not per_role,
                model="" if per_role else options.model,
                max_turns=0 if per_role else launch_turns(limits, engine),
            )
        except (CuantaError, ValueError):
            return choose_shape(request.type, None, None, threshold, mode)
        buckets = forecast.envelope.buckets
        share = exploration_share(buckets.exploration, buckets.total)
        return choose_shape(request.type, None, share, threshold, mode)

    def limit_settings(self) -> LimitSettings:
        from cuanta.domain.limits import limit_settings

        return limit_settings(self.config)

    def run_limits(self, options: MandateOptions, task_type: str) -> RunLimits:
        from cuanta.application.mandate_flow import mandate_limits

        return mandate_limits(options, task_type, self.limit_settings())

    def shaped_options(
        self, request: MandateRequest, options: MandateOptions, cross_engine: bool = False
    ) -> tuple[MandateOptions, ShapeChoice]:
        from cuanta.application.mandate_flow import resolved_profile
        from cuanta.domain.implementation import ImplementationProfile
        from cuanta.domain.mandate import Shape
        from cuanta.domain.scout import ShapeChoice

        profile = resolved_profile(
            options,
            self.config.implementation_profile,
            self.config.engine,
            request.type,
            cross_engine,
            self.fast_ready,
            self.routing_pinned(options.route.mode),
        )
        options = replace(options, profile=profile.value)
        if profile is ImplementationProfile.FAST:
            return options, ShapeChoice(Shape.SINGLE)
        choice = self.team_shape(request, options, cross_engine)
        if not choice.scout:
            return options, choice
        return replace(options, shape=choice.shape.value, scout_mode=choice.mode.value), choice

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

    def mandate_queue(self) -> MandateQueue:
        from cuanta.adapters.storage.queue_file import FileQueueStore
        from cuanta.application.init_project import ensure_cuanta_dir
        from cuanta.application.queue import MandateQueue

        return MandateQueue(
            FileQueueStore(self.cuanta_dir()),
            self.clock.now_iso,
            lambda: ensure_cuanta_dir(self.state_workspace()),
        )

    def estimate_shapes(self, runs: Sequence[Run]) -> dict[str, str]:
        from cuanta.application.results import stored_shape
        from cuanta.application.run_reports import RunReports

        reports = RunReports(self.state_workspace())
        shapes: dict[str, str] = {}
        for run in runs:
            if run.kind == "cross":
                shapes[run.id] = "pipeline"
            elif run.kind == "mandate":
                single, known = stored_shape(reports.meta(run.id) or {}, run, run.task_type)
                if known:
                    shapes[run.id] = "single" if single else "pipeline"
        return shapes

    def run_estimate(
        self, plan: RoutePlan | None, task_type: str, depth: str, shape: str = "pipeline"
    ) -> RunEstimate:
        from cuanta.adapters.system.prices import load_prices
        from cuanta.application.estimate import run_estimate

        runs = self.shared_ledger().runs()
        return run_estimate(
            plan, runs, load_prices(), task_type, depth, shape, self.estimate_shapes(runs)
        )

    def team_estimate(
        self,
        plan: RoutePlan,
        task_type: str,
        depth: str,
        cap: float,
        shape: str = "pipeline",
        repair: bool = True,
        docs_off: bool = False,
    ) -> Estimate:
        from cuanta.adapters.system.prices import load_prices
        from cuanta.application.estimate import estimate

        runs = self.shared_ledger().runs()
        return estimate(
            plan,
            runs,
            load_prices(),
            task_type,
            depth,
            cap,
            shape,
            self.estimate_shapes(runs),
            repair,
            docs_off,
        )

    def plan_sizes(self, plan: ChangePlan) -> PlanSizes:
        from cuanta.application.forecast import plan_sizes

        try:
            return self._indexed_sizes(plan)
        except (OSError, ValueError, sqlite3.DatabaseError):
            return plan_sizes(plan, ())

    def _indexed_sizes(self, plan: ChangePlan) -> PlanSizes:
        from cuanta.adapters.storage.sqlite_index import SqliteIndex
        from cuanta.adapters.system.index_inventory import LocalIndexInventory
        from cuanta.application.forecast import plan_sizes

        if not self.config.index_enabled:
            inventory = LocalIndexInventory(
                self.project, frozenset(self.config.exclusions), self.git_view
            )
            return plan_sizes(plan, inventory.candidates())
        path = self.project / ".cuanta" / "index.db"
        if not path.is_file():
            return plan_sizes(plan, ())
        index = SqliteIndex(path, read_only=True)
        try:
            return plan_sizes(plan, index.files())
        finally:
            index.close()

    def envelope_advisor(self, ledger: Ledger, prices: PriceTable) -> JevEnvelope | None:
        if not self.config.instinct_envelope:
            return None
        from cuanta.adapters.instinct.jev import MODEL, JevInstinct
        from cuanta.application.forecast import JevEnvelope

        backend = JevInstinct()
        return JevEnvelope(
            backend,
            ledger,
            self.clock.now_iso,
            prices.lookup(MODEL),
            backend.name in self.config.remote_consent,
            lambda request: self.blast_radius(f"{request.what} {request.where}"),
        )

    def forecaster(self, ledger: Ledger) -> Forecaster:
        from cuanta.adapters.system.prices import load_prices
        from cuanta.application.forecast import Forecaster
        from cuanta.application.run_reports import RunReports

        prices = load_prices()
        return Forecaster(
            ledger,
            prices,
            lambda: self.model_service().view().entries,
            self.plan_sizes,
            self.prefix_query().clock,
            self.clock.now_iso,
            self.envelope_advisor(ledger, prices),
            self.estimate_shapes,
            lambda run_id: RunReports(self.state_workspace()).meta(run_id) or {},
        )

    def team_forecast(
        self,
        task_type: str,
        plan: RoutePlan,
        provider: Provider,
        depth: str,
        shape: str,
        cap: float,
        change_plan: ChangePlan | None,
        native: bool = False,
        model: str = "",
        max_turns: int = 0,
        implementation_profile: str = "balanced",
        variant: str = "",
        request: MandateRequest | None = None,
    ) -> PlannedForecast:
        return self.forecaster(self.shared_ledger()).plan(
            task_type,
            plan,
            provider,
            depth,
            shape,
            cap,
            change_plan,
            native,
            model,
            max_turns,
            implementation_profile,
            variant,
            request=request,
        )

    def role_budget(
        self,
        plan: RoutePlan,
        task_type: str,
        depth: str,
        cap: float,
        repair: bool = True,
        docs_off: bool = False,
    ) -> RepairBudget:
        from cuanta.domain.role_budgets import RepairBudget

        found = self.team_estimate(plan, task_type, depth, cap, repair=repair, docs_off=docs_off)
        shares = {cost.role: cost.share for cost in found.roles if cost.share > 0}
        return RepairBudget(shares, found.repair_usd, found.repair_from_docs)

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

    def cross_engine(
        self,
        ledger: Ledger,
        budget_usd: float,
        max_turns: int = 0,
        sandbox: SandboxLaunch | None = None,
        checkpoint: Callable[[], Message | None] | None = None,
        depth: str = "",
        implementation: MandateOptions | None = None,
        wall_s: float = 0.0,
    ) -> CrossEnginePipeline:
        from cuanta.application.cross_engine import CrossEnginePipeline
        from cuanta.application.run_reports import RunReports
        from cuanta.application.timing import PhaseRecorder
        from cuanta.domain.implementation import AUTO_PROFILE, ImplementationProfile
        from cuanta.domain.run_mode import classic_meta
        from cuanta.domain.scout import docs_setting, parse_docs_mode

        profile = (
            implementation.profile if implementation is not None else ""
        ) or self.config.implementation_profile
        docs_mode, docs_flag = docs_setting(
            implementation.docs if implementation is not None else "",
            parse_docs_mode(self.config.docs_mode),
        )

        reports = RunReports(self.state_workspace())
        mode = classic_meta(self.run_mode)

        def save_metrics(run_id: str, metrics: Mapping[str, object]) -> None:
            reports.save_meta(run_id, {**(reports.meta(run_id) or {}), **metrics, **mode})

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
            max_turns=max_turns,
            wall_s=wall_s,
            monotonic=self.clock.monotonic,
            sandbox=sandbox,
            checkpoint=checkpoint,
            estimator=self.run_estimate,
            depth=depth,
            allocator=self.role_budget,
            refresh_index=self.refresh_index,
            change_plan=self.change_plan,
            snapshot=self.project_snapshot,
            save_metrics=save_metrics,
            context_pack=(
                self.context_pack
                if self.config.index_enabled and self.config.pack_enabled
                else None
            ),
            learn_run=self.learn_run if sandbox is None else None,
            verifier=self.verifier().run,
            lines_of=self.project_lines,
            reads_of=lambda run_id: self.read_ranges(ledger, run_id),
            margin=lambda: self.budget_margin(ledger),
            index_tools=self.pipeline_index_tools,
            new_files=self.codex_file_guard() if os.name == "nt" else None,
            build_blocked=self.build_blocked(),
            forecaster=self.forecaster(ledger),
            new_run_id=self.new_run_id,
            governor=self.governor_setup(ledger) if self.config.governor else None,
            read_discipline=self.pipeline_read_discipline,
            read_max_lines=self.config.read_max_lines,
            docs_mode=docs_mode,
            docs_flag=docs_flag,
            timing=PhaseRecorder(self.clock, ledger),
            implementation_profile=ImplementationProfile.BALANCED.value
            if profile == AUTO_PROFILE
            else profile,
            variant=implementation.variant or self.config.implementation_variant
            if implementation is not None
            else self.config.implementation_variant,
            pure=implementation.pure if implementation is not None else False,
            model=implementation.model if implementation is not None else "",
        )

    def governor_setup(self, ledger: Ledger | None = None) -> GovernorSetup:
        from cuanta.adapters.system.prices import load_prices
        from cuanta.application.steering import GovernorSetup
        from cuanta.domain.governor import seconds_rate

        def per_second(model: str) -> float:
            return seconds_rate(ledger.runs(), model) if ledger is not None else 0.0

        return GovernorSetup(self.clock.monotonic, load_prices(), per_second)

    def pipeline_read_discipline(self, engine: str) -> bool:
        return self.config.pipeline_read_discipline and engine in {"claude", "codex"}

    def blocked_calls(self, ledger: Ledger, run_ids: Sequence[str]) -> BlockedCalls:
        from cuanta.domain.governor_report import blocked_calls
        from cuanta.ports.ledger import EventQuery

        return blocked_calls(
            event for run_id in run_ids for event in ledger.events(EventQuery(run_id=run_id))
        )

    def verifier(self) -> Verifier:
        from cuanta.application.verification import Verifier

        env = {name: value for name, value in self.extra_env if name != STATE_ROOT_ENV}
        return Verifier(self.runner, self.project, os.name == "nt", env or None)

    def project_lines(self, relative: str) -> list[str] | None:
        text = self.workspace().read_text(relative)
        return text.splitlines() if text is not None else None

    def read_ranges(self, ledger: Ledger, run_id: str) -> tuple[tuple[str, int, int], ...]:
        from cuanta.domain.read_efficiency import read_ranges
        from cuanta.ports.ledger import EventQuery

        return read_ranges(ledger.events(EventQuery(run_id=run_id)), str(self.project))

    def budget_margin(self, ledger: Ledger) -> OvershootMargins:
        from cuanta.adapters.models.tiers import load_tier_table
        from cuanta.application.run_reports import RunReports
        from cuanta.domain.role_budgets import Overshoot, overshoot_margins

        reports = RunReports(self.state_workspace())
        samples: list[Overshoot] = []
        for run in ledger.runs():
            if (
                run.engine != "claude"
                or run.end_reason != "error_max_budget_usd"
                or run.cost_usd is None
                or run.partial
            ):
                continue
            native = (reports.meta(run.id) or {}).get("native_cap_usd")
            if isinstance(native, int | float) and native > 0:
                samples.append(Overshoot(run.model, float(native), run.cost_usd))
            elif run.cap_usd and (run.kind != "cross" or run.parent_id):
                samples.append(Overshoot(run.model, run.cap_usd, run.cost_usd))
        return overshoot_margins(samples, load_tier_table().aliases)

    def build_blocked(self) -> frozenset[str]:
        return frozenset({"codex"}) if os.name == "nt" else frozenset()

    def pipeline_index_tools(self, engine: str) -> bool:
        return (
            self.config.index_enabled
            and self.config.pipeline_index_tools
            and engine in {"claude", "codex"}
        )

    def codex_file_guard(self) -> CodexFileGuard:
        from cuanta.application.cross_files import CodexFileGuard

        return CodexFileGuard(self.workspace(), frozenset(self.config.exclusions))

    def project_snapshot(self) -> dict[str, str]:
        workspace = self.workspace()
        scan = workspace.scan(frozenset(self.config.exclusions), collect_files=True, all_files=True)
        return {
            path: f"{digest}:{scan.modes.get(path, 0)}"
            for path in scan.files
            if (digest := workspace.sha256(path)) is not None
        }

    def bench_runner(
        self,
        fixtures: Path,
        kit: Path,
        model: str,
        scratch: Path | None,
        keep: bool,
        *,
        shape: str = "",
        pack: str = "on",
        depth: str = "",
    ) -> BenchRunner:
        import sys

        from cuanta.adapters.bench.sandbox import LocalBenchSandbox
        from cuanta.application.bench import BenchExecutor, BenchRunner

        sandbox = LocalBenchSandbox(fixtures, kit, self.runner, sys.executable, scratch, keep)

        def attempt(
            task: BenchTask, condition: Condition, root: str, cap: float, session: str, index: str
        ) -> Attempt:
            return self.bench_attempt(
                task,
                condition,
                root,
                cap,
                model,
                session,
                index,
                shape=shape,
                pack=pack,
                depth=depth,
            )

        def arm_attempt(task: BenchTask, planned: PlannedRun, root: str) -> Attempt:
            return self.bench_attempt(
                task,
                planned.condition,
                root,
                planned.cap_usd,
                model,
                planned.session,
                planned.index,
                shape=shape,
                pack=pack,
                depth=depth,
                planned=planned,
            )

        executor = BenchExecutor(sandbox, attempt, self.clock.monotonic, arm_attempt)
        return BenchRunner(executor, self.workspace(), executor.group)

    def bench_tasks(self, directory: Path, suite: str) -> tuple[BenchTask, ...]:
        from cuanta.adapters.bench.tasks import load_tasks
        from cuanta.domain.bench import select

        return select(load_tasks(directory), suite)

    def result_query(self, ledger: Ledger) -> ResultQuery:
        from datetime import date

        from cuanta.application.results import ResultQuery

        return ResultQuery(self.state_workspace(), ledger, date.today, self.clock.now_iso)

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

    def last_run(self) -> str:
        from cuanta.domain.errors import DomainFailure
        from cuanta.domain.ledger import last_launched
        from cuanta.domain.messages import english, msg

        latest = last_launched(self.runs_query().run())
        if latest is None:
            raise DomainFailure(english(msg("runs.none_yet")), english(msg("runs.none_yet_hint")))
        return latest.id

    def stored_reports(self) -> frozenset[str]:
        from cuanta.application.run_reports import RUNS_DIR

        folder = self.state_project() / RUNS_DIR
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
            if run.cost_usd is not None and run.cost_usd > 0 and not run.partial
        ]
        return percentile(costs, 0.5)

    def latest_bench(self) -> BenchResult | None:
        from cuanta.application.bench import load_bench

        return load_bench(self.workspace())

    def _bench_baseline(
        self,
        task: BenchTask,
        root: str,
        cap: float,
        model: str,
        session: str,
        depth: str,
        ledger: Ledger,
    ) -> tuple[Run, str, str | None]:
        from cuanta.application.engine_run import DEFAULT_DENIED, LaunchSpec
        from cuanta.application.mandate import allowed_tools
        from cuanta.domain.depth import parse_depth, profile
        from cuanta.domain.errors import NotAvailable
        from cuanta.domain.mandate import (
            Shape,
            investigation_builtin_tools,
            investigation_denied,
            investigation_tools,
        )

        chosen_depth = parse_depth(depth)
        engine = self.engine("claude")
        if engine is None or not engine.available():
            raise NotAvailable("claude not found on PATH", "install Claude Code")
        stack = self.detector().run(with_engines=False).stack
        spec = LaunchSpec(
            kind="bench",
            prompt=task.prompt,
            cwd=root,
            allowed_tools=(
                investigation_tools(True, Shape.SINGLE, False)
                if task.request.type == "investigation"
                else allowed_tools(stack)
            ),
            disallowed_tools=(
                tuple(dict.fromkeys((*DEFAULT_DENIED, *investigation_denied(True, Shape.SINGLE))))
                if task.request.type == "investigation"
                else DEFAULT_DENIED
            ),
            tools=(
                investigation_builtin_tools(True, Shape.SINGLE, False)
                if task.request.type == "investigation"
                else None
            ),
            model=model,
            max_budget_usd=cap,
            session=session,
            temporary_copy=True,
            read_only=task.request.type == "investigation",
            task_type=task.request.type,
            shape="single",
            depth=chosen_depth.value,
            effort=(
                profile(chosen_depth, task.request.type).effort
                if depth or task.request.type == "investigation"
                else ""
            ),
            max_turns=(
                profile(chosen_depth, task.request.type).max_turns
                if depth or task.request.type == "investigation"
                else 0
            ),
        )
        launch = self.launcher(engine, ledger, telemetry=True).launch(spec, lambda _: None)
        run = launch.run
        result = launch.outcome.result if launch.outcome is not None else None
        subtype = result.subtype if result is not None else ""
        answer = result.text if result is not None else None
        return run, subtype, answer

    def bench_attempt(
        self,
        task: BenchTask,
        condition: Condition,
        root: str,
        cap: float,
        model: str,
        session: str = "lean",
        index: str = "on",
        *,
        shape: str = "",
        pack: str = "on",
        depth: str = "",
        planned: PlannedRun | None = None,
    ) -> Attempt:
        from cuanta.application.bench import Attempt
        from cuanta.application.spectrum import Selection
        from cuanta.domain.bench import Condition, bench_boundaries
        from cuanta.domain.bench_proof import arm_config, planned_arm
        from cuanta.domain.depth import parse_depth
        from cuanta.domain.overhead import session_overhead
        from cuanta.domain.spectrum import LeakKind
        from cuanta.ports.ledger import EventQuery

        arm = planned_arm(planned)
        sub = replace(Container.for_project(Path(root)), runner=self.runner, clock=self.clock)
        selected = "off" if condition is Condition.BASELINE else index
        sub.config = arm_config(
            replace(
                sub.config,
                index_enabled=selected == "on",
                index_tools=selected == "on",
                pack_enabled=selected == "on" and pack == "on",
            ),
            arm,
        )
        plan = sub.change_plan(task.request)
        before = sub.project_snapshot()
        ledger = sub.ledger()
        try:
            models: tuple[tuple[str, str, str], ...] = ()
            answer: str | None = None
            actual_shape = "single"
            report: MandateReport | None = None
            chosen_depth = parse_depth(depth)
            if condition is Condition.BASELINE:
                run, subtype, answer = sub._bench_baseline(
                    task, root, cap, model, session, depth, ledger
                )
            else:
                report = sub._bench_mandate(
                    task, condition, cap, model, session, shape, depth, ledger, arm
                )
                run, subtype, answer = report.run, "", report.text
                actual_shape = "single" if report.single else "pipeline"
                models = tuple(
                    (row.agent, row.planned, ", ".join(row.actual)) for row in report.audit
                )
            spectrum = sub.spectrum_query(ledger).run(Selection(run=run.id)).report
            outside, protected = bench_boundaries(plan, before, sub.project_snapshot())
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
                index=selected,
                answer=answer,
                anatomy=spectrum.anatomy,
                read_efficiency=spectrum.read_efficiency,
                shape=actual_shape,
                pack="on" if sub.config.pack_enabled else "off",
                depth=run.depth or chosen_depth.value,
                exploration_tokens_estimate=spectrum.index.exploration_tokens_estimate,
                raw_reads=spectrum.index.raw_reads,
                index_calls=spectrum.index.index_calls,
                out_of_plan_edits=tuple(
                    dict.fromkeys((*spectrum.index.out_of_plan_edits, *outside))
                ),
                guard_violations=tuple(
                    dict.fromkeys((*spectrum.index.guard_violations, *protected))
                ),
                proof=sub._bench_proof(
                    planned, arm, run, plan, root, events, spectrum.anatomy, overhead, report
                ),
                partial=run.partial,
            )
        finally:
            sub.close()

    def _bench_mandate(
        self,
        task: BenchTask,
        condition: Condition,
        cap: float,
        model: str,
        session: str,
        shape: str,
        depth: str,
        ledger: Ledger,
        arm: Arm | None,
    ) -> MandateReport:
        from cuanta.application.mandate_flow import MandateOptions, per_role_run
        from cuanta.application.progress import RecordingSink
        from cuanta.application.route_apply import RouteOptions
        from cuanta.domain.bench import Condition
        from cuanta.domain.errors import DomainFailure
        from cuanta.domain.implementation import ImplementationProfile
        from cuanta.domain.limits import LimitsMode

        flow = self.mandate_flow(ledger)
        mode = "auto" if condition is Condition.ROUTED else "off"
        options = MandateOptions(
            engine="claude",
            profile=ImplementationProfile.BALANCED.value,
            model=model,
            budget_usd=cap,
            limits=LimitsMode.DEPTH.value,
            route=RouteOptions(mode=mode),
            session=session,
            temporary_copy=True,
            shape=shape,
            depth=depth,
        )
        if arm is not None and arm.shape:
            options, _ = self.shaped_options(task.request, replace(options, shape=arm.shape))
            if per_role_run(options, task.request.type, "claude"):
                raise DomainFailure(
                    "the bench runs the Claude team natively; this shape needs one launch per role",
                    "set runs.scout_mode = native for the bench",
                )
        return flow.run(flow.prepare(task.request, 0, options), RecordingSink())

    def _bench_proof(
        self,
        planned: PlannedRun | None,
        arm: Arm | None,
        run: Run,
        plan: ChangePlan,
        root: str,
        events: Sequence[LedgerEvent],
        anatomy: AnatomyReport,
        overhead: SessionOverhead,
        report: MandateReport | None,
    ) -> ProofRecord | None:
        from cuanta.domain.bench_proof import exploration_leak, proof_record
        from cuanta.domain.governor_report import blocked_calls
        from cuanta.domain.read_efficiency import read_calls

        if planned is None or arm is None:
            return None
        hooks = self.config.read_discipline or self.config.pipeline_read_discipline
        split = overhead.split
        leak: tuple[int, int] | None = None
        if anatomy.totals.requests > 0:
            calls = read_calls(events, root)
            lines = {
                path: len(found)
                for path in {call.path for call in calls if not call.search}
                if ".." not in path.split("/") and (found := self.project_lines(path)) is not None
            }
            leak = exploration_leak(calls, plan, lines, self.config.read_max_lines)
        return proof_record(
            arm,
            planned,
            run.end_reason,
            leak[0] if leak is not None else None,
            blocked_calls(events) if hooks else None,
            report.scout if report is not None else None,
            overhead.cache.read if overhead.cache is not None else None,
            split.fixed if split is not None else None,
            report.governor if report is not None else (),
            rule_tokens=leak[1] if leak is not None else None,
            answered=bool(report.text) if report is not None else None,
        )

    def bench_overshoot(self) -> float:
        from cuanta.domain.bench_proof import overshoot_allowance

        if not (self.cuanta_dir() / "ledger.db").is_file():
            return overshoot_allowance({})
        return overshoot_allowance(self.budget_margin(self.shared_ledger()).samples)

    def mandate_service(self, ledger: Ledger) -> MandateService:
        from cuanta.application.mandate import MandateService
        from cuanta.domain.run_mode import classic_meta

        return MandateService(
            self.state_workspace(),
            ledger,
            self.decisions(ledger),
            self.clock.now_iso,
            frozenset(self.config.exclusions),
            scanned=self.workspace(),
            meta=classic_meta(self.run_mode),
            templates=self.mandate_templates(),
        )

    def mandate_flow(self, ledger: Ledger, sandbox: SandboxLaunch | None = None) -> MandateFlow:
        from cuanta.application.mandate_flow import MandateFlow
        from cuanta.application.spectrum import Selection
        from cuanta.application.timing import PhaseRecorder
        from cuanta.domain.scout import parse_docs_mode
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
            default_max_turns=self.config.max_turns,
            limits=self.limit_settings(),
            verification=lambda run_id, policy, progress: self.verify_suite(
                ledger, run_id, policy, progress
            ),
            verify_policy=self.verification_policy(),
            routing=self.mandate_routing(ledger),
            has_agents=self.has_forge_agents,
            scope=self.decision_scope,
            new_run_id=self.new_run_id,
            graph_mode=lambda: self.detector().graph_mode()[0],
            sandbox=sandbox,
            estimator=self.run_estimate,
            shape_estimator=self.run_estimate,
            refresh_index=self.refresh_index,
            change_plan=self.change_plan,
            context_pack=(
                self.context_pack
                if self.config.index_enabled and self.config.pack_enabled
                else None
            ),
            learn_run=self.learn_run if sandbox is None else None,
            pipeline_index_tools=self.config.index_enabled and self.config.pipeline_index_tools,
            forecaster=self.forecaster(ledger),
            governor=self.governor_setup(ledger) if self.config.governor else None,
            pipeline_read_discipline=self.config.pipeline_read_discipline,
            read_discipline=self.config.read_discipline,
            docs_mode=parse_docs_mode(self.config.docs_mode),
            lines_of=self.project_lines,
            capsules=self.capsule_store(),
            timing=PhaseRecorder(self.clock, ledger),
            default_profile=self.config.implementation_profile,
            default_variant=self.config.implementation_variant,
            implementation_tools=self.config.implementation_tools,
            fast_ready=self.fast_ready,
            steps=self.slow_steps(),
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
            index_tools=self.config.index_enabled
            and (self.config.index_tools or self.config.pipeline_index_tools),
            default_session=self.config.run_session,
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

    def verification_policy(self) -> VerificationPolicy:
        from cuanta.domain.verification import VerificationPolicy

        return VerificationPolicy(self.config.verify_mode, self.config.verify_timeout_s)

    def verify_suite(
        self, ledger: Ledger, run_id: str, policy: VerificationPolicy, progress: ProgressSink
    ) -> VerificationResult:
        from cuanta.domain.errors import NotAvailable
        from cuanta.domain.messages import msg
        from cuanta.domain.progress import Status, note, started
        from cuanta.domain.verification import VerificationResult, pytest_parallel

        detection = self.detector().run(with_engines=False)
        gateway = self.affected_gateway(ledger)

        def begin(runner: str, reason: Message) -> None:
            progress.publish(note(Status.INFO, reason))
            progress.publish(started("verdict", msg("verify.running", runner=runner)))

        try:
            choice, _ = self.gateway(ledger).choose(
                detection.stack, self.config, detection.verify_tier
            )
            return gateway.verify(
                detection.stack,
                self.config,
                detection.verify_tier,
                run_id,
                policy,
                pytest_parallel(self.workspace().read_text("pyproject.toml") or "", choice.name),
                begin,
            )
        except NotAvailable:
            return VerificationResult("skipped", msg("mandate.verdict_skipped"))

    def trial_store(self, ledger: Ledger) -> TrialStore:
        from cuanta.application.trials import TrialStore

        return TrialStore(self.state_workspace(), ledger, self.clock.now_iso)

    def sandbox_runner(self, ledger: Ledger) -> SandboxRunner:
        from cuanta.adapters.system.sandbox_cleanup import BackgroundCleanup
        from cuanta.adapters.system.warm_sandbox import WarmSandbox
        from cuanta.application.sandbox import SandboxRunner, TrialRecorder
        from cuanta.application.timing import PhaseRecorder

        sandbox = WarmSandbox(now_iso=self.clock.now_iso)
        recorder = TrialRecorder(sandbox, self.state_workspace(), self.clock.now_iso)
        return SandboxRunner(
            sandbox,
            recorder,
            ledger,
            self.state_project(),
            after_record=lambda copy, run_id: self.learn_run(run_id, copy.root),
            timing=PhaseRecorder(self.clock, ledger),
            cleanup=lambda copy, run_id: BackgroundCleanup(
                copy, run_id, self.state_project() / ".cuanta"
            ),
        )

    def run_metrics(self, ledger: Ledger, run_id: str) -> dict[str, object]:
        from dataclasses import asdict

        from cuanta.application.spectrum import Selection

        totals = self.spectrum_query(ledger).run(Selection(run=run_id)).report.totals
        view = self.result_query(ledger).load(run_id)
        split = view.split if view is not None else None
        cache = view.cache if view is not None else None
        return {
            "tokens": {
                "fresh_input": totals.fresh_input,
                "cache_read": totals.cache_read,
                "cache_write": totals.cache_write,
                "output": totals.output,
                "reasoning": totals.reasoning,
            },
            "duration_s": view.duration_s if view is not None else None,
            "first_request": (
                {"tokens": split.first_request, "fixed": split.fixed, "request": split.request}
                if split is not None
                else None
            ),
            "cache": cache.state.value if cache is not None else None,
            "index": asdict(view.index) if view is not None else None,
        }

    def run_sandboxed(
        self,
        ledger: Ledger,
        request: MandateRequest,
        signatures: int,
        options: MandateOptions,
        progress: ProgressSink,
        observer: Callable[[EngineEvent], None] | None = None,
        verdict: bool = True,
        on_start: Callable[[MandateFlow, Prepared], None] | None = None,
    ) -> SandboxResult:
        from cuanta.application.mandate import report_payload

        def flow_for(copy: SandboxCopy, launch: SandboxLaunch) -> MandateFlow:
            return self.sandbox_container(copy.root, launch.env).mandate_flow(ledger, launch)

        def payload(report: MandateReport) -> dict[str, object]:
            return {**report_payload(report), **self.run_metrics(ledger, report.run.id)}

        return self.sandbox_runner(ledger).run_mandate(
            flow_for,
            request,
            signatures,
            options,
            progress,
            observer,
            verdict,
            payload,
            on_start,
        )

    def run_sandboxed_cross(
        self,
        ledger: Ledger,
        request: MandateRequest,
        plan: RoutePlan,
        progress: ProgressSink,
        budget_usd: float,
        max_turns: int,
        keep: bool,
        depth: str = "",
        on_start: Callable[[CrossEnginePipeline], None] | None = None,
        implementation: MandateOptions | None = None,
        wall_s: float = 0.0,
        observer: Callable[[EngineEvent], None] | None = None,
    ) -> SandboxResult:
        from cuanta.application.cross_engine import cross_metrics

        def pipeline_for(
            copy: SandboxCopy, launch: SandboxLaunch, checkpoint: Callable[[], Message | None]
        ) -> CrossEnginePipeline:
            sub = self.sandbox_container(copy.root, launch.env)
            pipeline = sub.cross_engine(
                ledger, budget_usd, max_turns, launch, checkpoint, depth, implementation, wall_s
            )
            if on_start is not None:
                on_start(pipeline)
            return pipeline

        def payload(report: CrossReport) -> dict[str, object]:
            return {
                "ok": report.ok,
                "spent_usd": report.spent_usd,
                "partial": report.partial,
                **cross_metrics(report),
                "steps": [
                    {
                        "role": step.role.value,
                        "engine": step.engine,
                        "model": step.model,
                        "run_id": step.run_id,
                        "ok": step.ok,
                        "cost_usd": step.cost_usd,
                        "cost_source": step.cost_source,
                        "partial": step.partial,
                        "handoff_chars": len(step.handoff),
                        **self.run_metrics(ledger, step.run_id),
                    }
                    for step in report.steps
                ],
            }

        return self.sandbox_runner(ledger).run_cross(
            pipeline_for, request, plan, progress, keep, payload, observer
        )

    def new_file_review(self) -> NewFileReview:
        from cuanta.application.new_files import NewFileReview

        return NewFileReview(self.workspace())

    def terminal_report(self) -> TerminalReport:
        from cuanta.adapters.system.terminal import probe_terminal
        from cuanta.domain.terminal import classify

        return classify(probe_terminal(), os.environ)

    def file_costs_query(self) -> FileCostsQuery:
        from cuanta.adapters.storage.sqlite_index import SqliteIndex
        from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
        from cuanta.application.file_costs import FileCostsQuery

        state = self.cuanta_dir()
        return FileCostsQuery(
            lambda: SqliteIndex(state / "index.db", read_only=True),
            lambda: SqliteLedger(state / "ledger.db", read_only=True, immutable=True),
            self.clock.now_iso,
            error_types=(OSError, ValueError, sqlite3.DatabaseError),
        )

    def costs_query(self) -> CostsQuery:
        from cuanta.application.costs import CostsQuery
        from cuanta.application.run_reports import RunReports

        has_ledger = (self.cuanta_dir() / "ledger.db").is_file
        workspace = self.state_workspace()
        reports = RunReports(workspace)

        def completion(run_id: str) -> str:
            value = (reports.meta(run_id) or {}).get("completion")
            return value if isinstance(value, str) else ""

        return CostsQuery(self.ledger, has_ledger, self.clock.now_iso, completion, workspace)

    def run_outcomes(self, ledger: Ledger) -> RunOutcomes:
        from cuanta.application.outcomes import RunOutcomes

        return RunOutcomes(ledger, self.trial_store(ledger), self.clock.now_iso, self.learn_run)

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
            self.state_project(),
            self.ledger,
            keep_prompts=self.config.store_prompts,
            linger_s=linger_s,
            verbose=self.verbose,
        )

    def shell(self) -> Shell:
        from cuanta.adapters.system.shell import detect_shell

        return detect_shell()

    def spectrum_query(self, ledger: Ledger) -> SpectrumQuery:
        from cuanta.adapters.system.prices import load_prices
        from cuanta.application.read_reporting import ReadReporting
        from cuanta.application.run_reports import RunReports
        from cuanta.application.spectrum import SpectrumQuery

        workspace = self.state_workspace()
        reporting = ReadReporting(workspace, ledger, str(self.project))
        return SpectrumQuery(
            ledger,
            load_prices(),
            metadata=RunReports(workspace).meta,
            reports=reporting.report,
            roots=reporting.roots,
        )

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
        return self.state_project() / ".cuanta"

    def ledger(self) -> SqliteLedger:
        from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
        from cuanta.application.init_project import ensure_cuanta_dir

        ensure_cuanta_dir(self.state_workspace())
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
            scratch=self.project / ".cuanta" / "tmp",
            triage=SignatureTriage(ledger, self.decisions(ledger)),
            env=dict(self.extra_env),
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
            remember=self.state_root is None,
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
            workspace=self.state_workspace(),
            now_iso=self.clock.now_iso,
        )

    def routing_pinned(self, mode: str = "") -> bool:
        from cuanta.application.routing import policy_pinned

        return policy_pinned(self.routing_policy(), mode)

    def routing_policy(self) -> RoutingPolicy:
        from cuanta.adapters.models.tiers import load_tier_table
        from cuanta.domain.routing import parse_policy

        policy = parse_policy(dict(load_config(self.project).routing))
        return replace(policy, tier_defaults=load_tier_table().defaults)

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
            self.prefix_query(),
            self.config.engine,
            self.clock.now_iso,
            lambda: len(self.mandate_queue().entries()),
        )

    def prefix_query(self) -> PrefixQuery:
        from cuanta.application.cache_state import PrefixQuery

        return PrefixQuery(
            self.ledger,
            (self.cuanta_dir() / "ledger.db").is_file,
            self.config.cache_ttl_s,
            self.clock.now_ms,
            self.config.cache_auth,
            lambda: bool(os.environ.get("ANTHROPIC_API_KEY")),
            self.config.cache_model,
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
                doctor.template_check(self.mandate_templates()),
                doctor.user_forge_check(
                    lambda: installed_plugins(self.home), self.forge_kit().vendored_version
                ),
                doctor.session_check(self.ledger, (self.cuanta_dir() / "ledger.db").is_file),
                doctor.agents_md_check(
                    self.home_reader(), self.ledger, (self.cuanta_dir() / "ledger.db").is_file
                ),
                doctor.instinct_check(
                    self.config.instinct,
                    jev.available,
                    jev.setup_warning,
                    self.ledger,
                    (self.cuanta_dir() / "ledger.db").is_file,
                ),
                doctor.rulebook_check(workspace),
                doctor.gateway_check(workspace),
                doctor.suggestions_check(workspace),
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


def record_failure(project: Path, error: BaseException) -> str:
    import traceback
    from datetime import datetime

    from cuanta.adapters.system.log_file import LogFile

    relative = f".cuanta/logs/{datetime.now().date().isoformat()}.log"
    LogFile(project / relative).write("run failed", "".join(traceback.format_exception(error)))
    return relative
