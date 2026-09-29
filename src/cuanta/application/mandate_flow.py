from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from cuanta.application.engine_run import DEFAULT_DENIED, EngineLauncher, LaunchSpec
from cuanta.application.estimate import Estimator, ShapeEstimator
from cuanta.application.forecast import (
    Forecaster,
    PlannedForecast,
    forecast_failure,
    publish_forecast,
)
from cuanta.application.instinct import DecisionScope
from cuanta.application.mandate import Composed, MandateReport, MandateService, allowed_tools
from cuanta.application.route_apply import Applied, MandateRouting, RouteOptions
from cuanta.application.scout import SessionWatch, session_scout, watched
from cuanta.application.steering import GovernorSetup, observed, session_steering
from cuanta.domain.agents import role_of
from cuanta.domain.capsules import capsule_id
from cuanta.domain.change_plan import ChangePlan, apply_overrides
from cuanta.domain.depth import (
    DEFAULT_DEPTH,
    MAX_TURNS,
    DepthProfile,
    parse_depth,
    profile,
    read_budget_line,
    turn_limit,
)
from cuanta.domain.detection import GraphMode, Stack
from cuanta.domain.engine import EngineEvent
from cuanta.domain.errors import CuantaError, DomainFailure, NotAvailable
from cuanta.domain.estimates import RunEstimate
from cuanta.domain.evidence_pack import LinesOf
from cuanta.domain.guarantees import readonly_unavailable
from cuanta.domain.mandate import (
    INVESTIGATION,
    MandateRequest,
    MandateType,
    Shape,
    analyst_system_prompt,
    decision_key,
    investigation_builtin_tools,
    investigation_denied,
    investigation_tools,
    missing_fields,
    parse_shape,
    single_context,
)
from cuanta.domain.messages import Message, english, msg
from cuanta.domain.pack import ContextPack
from cuanta.domain.progress import Status, finished, note, started
from cuanta.domain.routing import Provider, Role, RoleRoute, parse_provider
from cuanta.domain.sandbox import SANDBOX_MODE, SandboxLaunch, sandbox_launch
from cuanta.domain.scout import (
    DocsChoice,
    DocsMode,
    DocsReason,
    ScoutMode,
    docs_choice,
    docs_off_line,
    has_pin,
    parse_forced_shape,
    scout_refusal,
    scout_session_block,
)
from cuanta.domain.team import runs_per_role
from cuanta.ports.capsules import CapsuleStore
from cuanta.ports.engine import Engine
from cuanta.ports.progress import ProgressSink

PROMPT_PLACEHOLDER = "<prompt>"
RUN_PLACEHOLDER = "<run id>"
TRACE_PLACEHOLDER = "<trace>"
PREVIEW_COPY = "<isolated copy>"
PACKAGE_MANIFEST = "package.json"


@dataclass(frozen=True, slots=True)
class MandateOptions:
    engine: str = ""
    model: str = ""
    budget_usd: float = 0.0
    hu: str = ""
    parent: str = ""
    route: RouteOptions = field(default_factory=RouteOptions)
    simple: bool = False
    session: str = ""
    depth: str = ""
    no_cap: bool = False
    shape: str = ""
    intake_scope: str = ""
    max_turns: int = 0
    temporary_copy: bool = False
    sandbox: bool = False
    keep_copy: bool = False
    estimate: RunEstimate | None = None
    plan_overrides: tuple[tuple[str, str], ...] = ()
    scout_mode: str = ""


def scout_launch(options: MandateOptions, task_type: str) -> bool:
    return (
        not options.simple
        and task_type != INVESTIGATION
        and parse_shape(options.shape) is Shape.SCOUT
        and options.scout_mode == ScoutMode.LAUNCH.value
    )


def per_role_run(options: MandateOptions, task_type: str, default_engine: str) -> bool:
    single = single_context(task_type, options.simple, parse_shape(options.shape))
    pipeline = not options.simple and not single
    engine = options.engine or default_engine
    claude_launch = parse_provider(engine) is Provider.CLAUDE and scout_launch(options, task_type)
    return runs_per_role(engine, pipeline) or claude_launch


def has_scout(applied: Applied | None) -> bool:
    if applied is None or applied.agents is None:
        return False
    return Role.SCOUT in applied.agents.roles.values()


def isolated(spec: LaunchSpec, sandbox: SandboxLaunch | None, claude: bool) -> LaunchSpec:
    if sandbox is None:
        return spec
    denied = sandbox.denied if claude else ()
    return replace(
        spec,
        temporary_copy=True,
        mode=SANDBOX_MODE,
        env=sandbox.env,
        disallowed_tools=(*spec.disallowed_tools, *denied),
    )


def resolve_budget(options: MandateOptions, task_type: str, default: float) -> float:
    if options.no_cap:
        return 0.0
    if options.budget_usd > 0:
        return options.budget_usd
    if options.depth:
        return profile(parse_depth(options.depth), task_type).cost_cap_usd
    return default


def resolve_max_turns(options: MandateOptions, chosen: DepthProfile | None, default: int) -> int:
    return (
        turn_limit(chosen, options.max_turns if options.max_turns > 0 else default)
        or MAX_TURNS[DEFAULT_DEPTH]
    )


def launch_turns(options: MandateOptions, task_type: str, engine: str, default: int) -> int:
    if engine != "claude":
        return 0
    chosen = (
        profile(parse_depth(options.depth), task_type)
        if options.depth or task_type == INVESTIGATION
        else None
    )
    return resolve_max_turns(options, chosen, default)


@dataclass(frozen=True, slots=True)
class Prepared:
    composed: Composed
    engine_name: str
    launcher: EngineLauncher
    spec: LaunchSpec
    applied: Applied | None = None
    forecast: PlannedForecast | None = None
    forecast_error: Message | None = None
    scout: bool = False
    docs: DocsChoice | None = None


def display_command(parts: tuple[str, ...], prompt: str) -> str:
    shown: list[str] = []
    for part in parts:
        if part == prompt:
            shown.append(f'"{PROMPT_PLACEHOLDER}"')
        else:
            shown.append(f'"{part}"' if " " in part else part)
    return " ".join(shown)


def _field_label(kind: str, field: str) -> str:
    if field in ("why", "tests", "constraints"):
        return f"mandate.field_{kind}_second"
    if field == "what":
        return f"mandate.field_{kind}_what"
    return f"mandate.field_{field}"


class MissingRequestFields(DomainFailure):
    def __init__(self, kind: str, fields: tuple[str, ...]) -> None:
        self.kind = kind
        self.fields = fields
        super().__init__(self.localized(english), english(msg("mandate.fill_fields")))

    def localized(self, translate: Callable[[Message], str]) -> str:
        labels = ", ".join(translate(msg(_field_label(self.kind, field))) for field in self.fields)
        return translate(msg("mandate.missing_fields", fields=labels))


def validate(request: MandateRequest) -> None:
    missing = missing_fields(request)
    if missing:
        raise MissingRequestFields(request.type, missing)
    allowed = {item.value for item in MandateType}
    if request.type not in allowed:
        raise DomainFailure(
            f"unknown type {request.type}", f"use one of {', '.join(sorted(allowed))}"
        )


class MandateFlow:
    def __init__(
        self,
        service: MandateService,
        engines: Callable[[str], Engine | None],
        launchers: Callable[[Engine], EngineLauncher],
        stack: Callable[[], Stack],
        summarize: Callable[[str], tuple[dict[str, int], float | None]],
        cwd: str,
        default_engine: str,
        default_budget: float,
        default_max_turns: int = 0,
        final_suite: Callable[[str], str | None] | None = None,
        routing: MandateRouting | None = None,
        has_agents: Callable[[], bool] | None = None,
        scope: DecisionScope | None = None,
        new_run_id: Callable[[], str] | None = None,
        graph_mode: Callable[[], GraphMode] = lambda: GraphMode.NONE,
        sandbox: SandboxLaunch | None = None,
        estimator: Estimator | None = None,
        shape_estimator: ShapeEstimator | None = None,
        refresh_index: Callable[[], None] | None = None,
        change_plan: Callable[[MandateRequest], ChangePlan] | None = None,
        context_pack: Callable[[MandateRequest, str, str, ChangePlan | None], ContextPack]
        | None = None,
        learn_run: Callable[[str], None] | None = None,
        pipeline_index_tools: bool = False,
        forecaster: Forecaster | None = None,
        governor: GovernorSetup | None = None,
        pipeline_read_discipline: bool = False,
        docs_mode: DocsMode = DocsMode.ON,
        lines_of: LinesOf | None = None,
        capsules: CapsuleStore | None = None,
    ) -> None:
        self._docs_mode = docs_mode
        self._lines_of = lines_of
        self._capsules = capsules
        self._pipeline_read_discipline = pipeline_read_discipline
        self._governor = governor
        self._forecaster = forecaster
        self._pipeline_index_tools = pipeline_index_tools
        self._learn_run = learn_run
        self._refresh_index = refresh_index
        self._change_plan = change_plan
        self._context_pack = context_pack
        self._shape_estimator = shape_estimator
        self._sandbox = sandbox
        self._estimator = estimator
        self._has_agents = has_agents
        self._scope = scope or DecisionScope()
        self._new_run_id = new_run_id
        self._graph_mode = graph_mode
        self._final_suite = final_suite
        self._routing = routing
        self._service = service
        self._engines = engines
        self._launchers = launchers
        self._stack = stack
        self._summarize = summarize
        self._cwd = cwd
        self._default_engine = default_engine
        self._default_budget = default_budget
        self._default_max_turns = default_max_turns
        self._active: Engine | None = None

    @property
    def service(self) -> MandateService:
        return self._service

    def prepare(
        self,
        request: MandateRequest,
        signatures: int,
        options: MandateOptions,
        preview: bool = False,
    ) -> Prepared:
        self._validate(request, options)
        return self._prepare_validated(request, signatures, options, preview)

    def _validate(self, request: MandateRequest, options: MandateOptions) -> None:
        validate(request)
        engine = options.engine or self._default_engine
        refusal = scout_refusal(
            request.type,
            parse_forced_shape(options.shape),
            options.simple,
            parse_provider(engine) is not None,
        )
        if refusal is not None:
            raise DomainFailure(english(refusal[0]), refusal[1])
        if self._refresh_index is not None:
            self._refresh_index()
        if not options.simple and self._has_agents is not None and not self._has_agents():
            raise DomainFailure(
                "this project has no Forge agents yet (.claude/agents)",
                "run cuanta init first, or use simple mode (--simple)",
            )

    def _prepare_validated(
        self,
        request: MandateRequest,
        signatures: int,
        options: MandateOptions,
        preview: bool,
    ) -> Prepared:
        run_id = "" if preview or self._new_run_id is None else self._new_run_id()
        key = decision_key(request)
        self._scope.set(run_id, key, preview)
        engine_name = options.engine or self._default_engine
        engine = self._engines(engine_name)
        if engine is None:
            raise DomainFailure(f"unknown engine {engine_name}", "use claude, codex or opencode")
        investigation = request.type == INVESTIGATION
        refusal = readonly_unavailable(engine_name) if investigation else None
        if refusal is not None:
            raise DomainFailure(english(refusal))
        shape = parse_shape(options.shape)
        single = single_context(request.type, options.simple, shape)
        claude = engine_name == "claude"
        graph_available = self._graph_mode() is GraphMode.CLI
        if not claude:
            tools: tuple[str, ...] = ()
        elif investigation:
            tools = investigation_tools(options.simple, shape, graph_available)
        else:
            tools = allowed_tools(self._stack(), graph_available)
        launcher = self._launchers(engine)
        route, wanted, docs = self._route(
            request, options, claude and not single and not investigation
        )
        applied = (
            self._routing.apply(request, route, engine_name, graph_available)
            if self._routing is not None and not options.simple
            else None
        )
        if applied is not None and single:
            applied = replace(applied, agents=None, agents_file="")
        protection = self._protection(request, options.plan_overrides)
        if claude and applied is not None and protection is not None and self._routing is not None:
            applied = self._routing.protect(applied, protection, options.session)
        pack = (
            self._context_pack(request, options.depth, Role.ORCHESTRATOR.value, protection)
            if self._context_pack is not None
            else None
        )
        applied = self._enrich(applied, request, options.depth, protection)
        depth = self._depth(options.depth, request.type)
        cap = resolve_budget(options, request.type, self._default_budget)
        guess = self._estimate(options, applied, request.type, preview)
        budget_line = read_budget_line(depth, graph_available) if depth is not None else ""
        system = (
            analyst_system_prompt(self._analyst_body(), budget_line, graph_available)
            if single and claude
            else ""
        )
        base = LaunchSpec(
            kind="mandate",
            prompt="",
            cwd=self._cwd,
            allowed_tools=tools,
            disallowed_tools=(
                investigation_denied(options.simple, shape) if investigation else DEFAULT_DENIED
            ),
            model=options.model
            or (applied.orchestrator or applied.single if applied is not None else ""),
            agents_file=applied.agents_file if applied is not None else "",
            effort=depth.effort if depth is not None and claude else "",
            append_system_prompt=system,
            unset_env=applied.unset if applied is not None else (),
            max_budget_usd=cap,
            max_turns=(resolve_max_turns(options, depth, self._default_max_turns) if claude else 0),
            tools=(
                investigation_builtin_tools(options.simple, shape, graph_available)
                if claude and investigation
                else None
            ),
            hu_ref=options.hu.upper(),
            parent_id=options.parent,
            run_id=run_id,
            session=options.session,
            task_type=request.type,
            depth=options.depth,
            read_only=investigation,
            temporary_copy=options.temporary_copy,
            estimate=guess,
            shape=(Shape.SINGLE if options.simple or single else Shape.PIPELINE).value,
            change_plan=protection,
            stable_prefix=pack is not None,
            index_tools=(
                True
                if claude and applied is not None and applied.agents and self._pipeline_index_tools
                else None
            ),
            read_discipline=(
                True
                if claude
                and applied is not None
                and applied.agents
                and self._pipeline_read_discipline
                else None
            ),
        )
        sandbox = self._launch(options)
        base = isolated(base, sandbox, claude)

        def command(prompt: str) -> list[str]:
            preview = launcher.request(
                replace(base, prompt=prompt),
                RUN_PLACEHOLDER,
                TRACE_PLACEHOLDER,
                None,
            )
            return engine.command(preview)

        extra = "" if system or not options.depth else budget_line
        if sandbox is not None and sandbox.note:
            extra = f"{extra}\n{sandbox.note}" if extra else sandbox.note
        composed = self._service.compose(
            request,
            signatures,
            command,
            options.simple,
            options.route.clarity,
            "\n".join(part for part in (extra, self._team_block(applied, wanted, docs)) if part),
            shape,
            graph_available,
            self._packed_prompt(pack, ""),
        )
        if run_id:
            self._service.link_decisions(key, run_id)
            if options.intake_scope:
                self._service.link_decisions(options.intake_scope, run_id)
        spec = replace(base, prompt=composed.prompt, scope=composed.hint.option)
        return Prepared(
            composed,
            engine_name,
            launcher,
            spec,
            applied,
            *self._forecast(
                request.type,
                applied,
                engine_name,
                options,
                base,
                protection,
                Shape.SCOUT.value if wanted and has_scout(applied) else base.shape,
            ),
            scout=wanted and has_scout(applied),
            docs=docs,
        )

    def _route(
        self, request: MandateRequest, options: MandateOptions, native: bool
    ) -> tuple[RouteOptions, bool, DocsChoice | None]:
        route = replace(options.route, depth=options.depth) if options.depth else options.route
        if not native or options.simple:
            return route, False, None
        trial = options.sandbox or self._sandbox is not None
        docs = docs_choice(
            self._docs_mode, request, trial, has_pin(options.route.role_models, Role.DOCS)
        )
        scout = parse_shape(options.shape) is Shape.SCOUT
        return replace(route, scout=scout, docs=docs.on), scout, docs

    def _team_block(self, applied: Applied | None, scout: bool, docs: DocsChoice | None) -> str:
        if applied is None or applied.agents is None:
            return ""
        docs_on = docs is None or docs.on
        if scout and has_scout(applied):
            return scout_session_block(docs_on)
        if docs is not None and not docs_on:
            return docs_off_line()
        return ""

    def _forecast(
        self,
        task_type: str,
        applied: Applied | None,
        engine_name: str,
        options: MandateOptions,
        spec: LaunchSpec,
        protection: ChangePlan | None,
        shape: str = "",
    ) -> tuple[PlannedForecast | None, Message | None]:
        provider = parse_provider(engine_name)
        if self._forecaster is None or applied is None or provider is None or options.simple:
            return None, None
        shape = shape or spec.shape
        try:
            planned = self._forecaster.plan(
                task_type,
                applied.plan,
                provider,
                options.depth,
                shape,
                spec.max_budget_usd,
                protection,
                native=shape in {Shape.PIPELINE.value, Shape.SCOUT.value},
                model=options.model,
                max_turns=spec.max_turns,
            )
        except (CuantaError, ValueError) as error:
            return None, forecast_failure(error)
        return planned, None

    def _enrich(
        self,
        applied: Applied | None,
        request: MandateRequest,
        depth: str,
        protection: ChangePlan | None,
    ) -> Applied | None:
        if (
            applied is None
            or applied.agents is None
            or self._routing is None
            or self._context_pack is None
        ):
            return applied
        contexts = {
            role: self._packed_prompt(
                self._context_pack(request, depth, role.value, protection), ""
            )
            for role in (Role.SENIOR, Role.TESTER)
            if role in applied.agents.roles.values()
        }
        return self._routing.enrich(applied, contexts)

    def _packed_prompt(self, pack: ContextPack | None, prompt: str) -> str:
        if pack is None:
            return prompt
        return "\n\n".join(part for part in (pack.stable_prefix, pack.excerpts, prompt) if part)

    def _protection(
        self, request: MandateRequest, overrides: tuple[tuple[str, str], ...]
    ) -> ChangePlan | None:
        if self._change_plan is None:
            return None
        return apply_overrides(self._change_plan(request), overrides)

    def _estimate(
        self,
        options: MandateOptions,
        applied: Applied | None,
        task_type: str,
        preview: bool,
    ) -> RunEstimate | None:
        if options.estimate is not None:
            return options.estimate
        if preview:
            return None
        plan = applied.plan if applied is not None else None
        if self._shape_estimator is not None:
            shape = options.simple or single_context(
                task_type, options.simple, parse_shape(options.shape)
            )
            return self._shape_estimator(
                plan, task_type, options.depth, (Shape.SINGLE if shape else Shape.PIPELINE).value
            )
        if self._estimator is None:
            return None
        return self._estimator(plan, task_type, options.depth)

    def _launch(self, options: MandateOptions) -> SandboxLaunch | None:
        if self._sandbox is not None or not options.sandbox:
            return self._sandbox
        linked = PACKAGE_MANIFEST in self._stack().manifests
        return sandbox_launch(self._cwd, PREVIEW_COPY, linked)

    def _depth(self, depth: str, task_type: str) -> DepthProfile | None:
        if not depth and task_type != INVESTIGATION:
            return None
        return profile(parse_depth(depth), task_type)

    def _analyst_body(self) -> str:
        if self._routing is None:
            return ""
        for definition in self._routing.definitions():
            if role_of(definition.name) is Role.ANALYST:
                return definition.prompt
        return ""

    def run(
        self,
        prepared: Prepared,
        progress: ProgressSink,
        observer: Callable[[EngineEvent], None] | None = None,
        verdict: bool = True,
    ) -> MandateReport:
        engine = prepared.launcher.engine
        name = prepared.engine_name
        self._active = engine
        try:
            if not engine.available():
                raise NotAvailable(f"{name} not found on PATH", "install it or pick another engine")
            missing = engine.missing_flags()
            if missing:
                raise NotAvailable(
                    f"{name} lacks flags cuanta needs: {', '.join(missing)}", f"upgrade {name}"
                )
            self._record_forecast(prepared, progress)
            steering = session_steering(
                self._governor, prepared.launcher, prepared.spec, prepared.forecast, progress
            )
            if prepared.docs is not None and prepared.docs.reason is not DocsReason.FORCED_ON:
                progress.publish(note(Status.INFO, prepared.docs.message))
            watch = SessionWatch() if prepared.scout else None
            sink = watched(observer, watch) if watch is not None else observer
            report = self._service.run(
                prepared.composed,
                prepared.launcher,
                prepared.spec if steering is None else replace(prepared.spec, steer=True),
                progress,
                self._summarize,
                sink if steering is None else observed(sink, steering),
            )
            if steering is not None:
                report = replace(report, governor=tuple(steering.taken))
            if watch is not None:
                report = replace(report, scout=self._session_scout(prepared, watch, report))
            if prepared.docs is not None and prepared.docs.reason is not DocsReason.FORCED_ON:
                report = replace(report, docs=prepared.docs)
        finally:
            self._active = None
        applied = prepared.applied
        if self._routing is not None and applied is not None:
            self._routing.record(report.run.id, prepared.composed.request.type, applied)
        report = self.verdict(report, progress) if verdict else report
        if self._routing is not None and applied is not None:
            self._routing.close(report.run.id, report.tests, report.run.cost_usd)
            rows = self._routing.audit(report.run.id, applied, report.handoffs)
            report = replace(report, audit=rows)
        self._service.close_decisions(report.run.id, report.tests if report.ok else "failed")
        self._service.save_meta(report)
        if self._learn_run is not None:
            self._learn_run(report.run.id)
        return report

    def _session_scout(
        self, prepared: Prepared, watch: SessionWatch, report: MandateReport
    ) -> dict[str, object]:
        plan = prepared.spec.change_plan
        planned = tuple(target.path for target in plan.edit) if plan is not None else ()
        return session_scout(
            watch, self._lines_of, planned, report.changed_files, self._store, report.run.id
        )

    def _store(self, text: str) -> str:
        if self._capsules is None:
            return ""
        digest, _, _ = self._capsules.put(text)
        return capsule_id(digest)

    def _record_forecast(self, prepared: Prepared, progress: ProgressSink) -> None:
        planned = prepared.forecast
        run_id = prepared.spec.run_id
        if prepared.forecast_error is not None:
            progress.publish(note(Status.WARN, prepared.forecast_error))
        if self._forecaster is None or planned is None or not run_id:
            return
        try:
            stored = self._forecaster.record(run_id, planned, prepared.composed.request)
        except (CuantaError, ValueError) as error:
            progress.publish(note(Status.WARN, forecast_failure(error)))
            return
        publish_forecast(progress, stored)

    def verdict(self, report: MandateReport, progress: ProgressSink) -> MandateReport:
        if self._final_suite is None or report.run.status == "interrupted":
            return report
        progress.publish(started("verdict", msg("mandate.verdict")))
        try:
            status = self._final_suite(report.run.id)
        except CuantaError as error:
            progress.publish(
                finished("verdict", Status.WARN, msg("stage.error", error=error.message))
            )
            return report
        if status is None:
            progress.publish(finished("verdict", Status.SKIP, msg("mandate.verdict_skipped")))
            return report
        outcome = Status.OK if status == "green" else Status.FAIL
        progress.publish(finished("verdict", outcome, msg("mandate.verdict_status", status=status)))
        return replace(report, tests=status)

    def stop(self) -> bool:
        engine = self._active
        if engine is None:
            return False
        engine.cancel()
        return True


@dataclass(frozen=True, slots=True)
class MandateSetup:
    engines: tuple[tuple[str, bool], ...]
    default_engine: str
    models: tuple[str, ...]
    budget_usd: float
    forge_ready: bool = True
    init_estimate: float | None = None
    max_turns: int = 0


@dataclass(frozen=True, slots=True)
class MandatePreview:
    prompt: str
    command: str
    engine: str
    scope: str
    confidence: float
    roles: tuple[RoleRoute, ...] = ()
    per_role: bool = False


def preview_of(prepared: Prepared) -> MandatePreview:
    composed = prepared.composed
    applied = prepared.applied
    return MandatePreview(
        prompt=composed.prompt,
        command=display_command(composed.command, composed.prompt),
        engine=prepared.engine_name,
        scope=composed.hint.option,
        confidence=composed.hint.probability,
        roles=(
            tuple(route for route in applied.plan.routes if route.model is not None)
            if applied is not None and applied.agents is not None
            else ()
        ),
    )


ENGINE_MODEL_PREFIXES = {"claude": ("claude-",), "codex": ("gpt-", "o")}


def models_for(engine: str, models: tuple[str, ...]) -> tuple[str, ...]:
    prefixes = ENGINE_MODEL_PREFIXES.get(engine)
    if prefixes is None:
        return models
    return tuple(name for name in models if name.startswith(prefixes))
