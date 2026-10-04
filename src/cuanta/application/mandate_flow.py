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
from cuanta.application.progress import Phase, SlowSteps
from cuanta.application.route_apply import Applied, MandateRouting, RouteOptions
from cuanta.application.scout import SessionWatch, session_scout, watched
from cuanta.application.steering import GovernorSetup, observed, session_steering
from cuanta.application.timing import PhaseRecorder
from cuanta.domain.agents import role_of
from cuanta.domain.anchors import anchor_notes
from cuanta.domain.capsules import capsule_id
from cuanta.domain.change_plan import ChangePlan, apply_overrides, plan_notes
from cuanta.domain.claude_variants import (
    DELEGATION_TOOLS,
    resolve_variant,
    session_denied_tools,
)
from cuanta.domain.depth import (
    DepthProfile,
    parse_depth,
    profile,
    read_budget_line,
)
from cuanta.domain.detection import GraphMode, Stack
from cuanta.domain.engine import EngineEvent
from cuanta.domain.errors import CuantaError, DomainFailure, NotAvailable
from cuanta.domain.estimates import RunEstimate
from cuanta.domain.evidence_pack import LinesOf
from cuanta.domain.guarantees import readonly_unavailable
from cuanta.domain.implementation import (
    AUTO_PROFILE,
    ImplementationProfile,
    fast_choice,
    implementation_prompt,
    large_feature,
    resolve_profile,
)
from cuanta.domain.limits import (
    NO_LIMITS,
    LimitRequest,
    LimitSettings,
    RunLimits,
    resolve_limits,
)
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
from cuanta.domain.progress import (
    FORECAST_STEP,
    PLAN_STEP,
    Note,
    Status,
    finished,
    note,
    started,
)
from cuanta.domain.routing import Provider, Role, RoleRoute, parse_provider
from cuanta.domain.sandbox import SANDBOX_MODE, SandboxLaunch, sandbox_launch
from cuanta.domain.scout import (
    DocsChoice,
    DocsMode,
    DocsReason,
    ScoutMode,
    docs_choice,
    docs_off_line,
    docs_refusal,
    docs_setting,
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
    budget_usd: float | None = None
    hu: str = ""
    parent: str = ""
    route: RouteOptions = field(default_factory=RouteOptions)
    simple: bool = False
    session: str = ""
    depth: str = ""
    shape: str = ""
    intake_scope: str = ""
    max_turns: int | None = None
    max_wall_min: float | None = None
    limits: str = ""
    temporary_copy: bool = False
    sandbox: bool = False
    keep_copy: bool = False
    estimate: RunEstimate | None = None
    plan_overrides: tuple[tuple[str, str], ...] = ()
    scout_mode: str = ""
    profile: str = ""
    variant: str = ""
    pure: bool = False
    docs: str = ""
    required: tuple[str, ...] | None = None
    type_stated: bool = True


def scout_launch(options: MandateOptions, task_type: str) -> bool:
    return (
        not options.simple
        and task_type != INVESTIGATION
        and parse_shape(options.shape) is Shape.SCOUT
        and options.scout_mode == ScoutMode.LAUNCH.value
    )


def team_requested(
    options: MandateOptions, cross_engine: bool = False, pinned: bool = False
) -> bool:
    return (
        cross_engine
        or pinned
        or options.simple
        or bool(options.route.role_models)
        or bool(options.route.preset)
        or parse_forced_shape(options.shape) is not None
        or options.docs == DocsMode.ON.value
    )


def resolved_profile(
    options: MandateOptions,
    default_profile: str,
    default_engine: str,
    task_type: str,
    cross_engine: bool = False,
    ready: Callable[[str], bool] = lambda _: True,
    pinned: bool = False,
) -> ImplementationProfile:
    engine = options.engine or default_engine
    found = resolve_profile(
        options.profile,
        default_profile,
        engine,
        task_type,
        team_requested(options, cross_engine, pinned),
        options.type_stated,
    )
    automatic = (options.profile or default_profile or AUTO_PROFILE) == AUTO_PROFILE
    if found is ImplementationProfile.FAST and automatic and not ready(engine):
        return ImplementationProfile.BALANCED
    return found


def fast_defaults(options: MandateOptions, task_type: str, large: bool) -> MandateOptions:
    if options.profile != ImplementationProfile.FAST or options.model:
        return options
    choice = fast_choice(task_type, large)
    if choice is None:
        return options
    return replace(options, model=choice.model, variant=options.variant or choice.variant)


def per_role_run(options: MandateOptions, task_type: str, default_engine: str) -> bool:
    if options.profile == ImplementationProfile.FAST:
        return False
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


def single_applied(applied: Applied | None, options: MandateOptions) -> Applied | None:
    if applied is None:
        return None
    if options.profile != ImplementationProfile.FAST:
        return replace(applied, agents=None, agents_file="")
    senior = applied.plan.route(Role.SENIOR)
    model = senior.model if senior is not None else None
    chosen = options.model or (model.id if model is not None else applied.single)
    return replace(applied, agents=None, agents_file="", single=chosen, orchestrator="")


def implementation_spec(base: LaunchSpec, claude: bool) -> LaunchSpec:
    fast = base.profile == ImplementationProfile.FAST
    if fast:
        base = replace(base, read_discipline=False, session="lean")
    if base.variant:
        base = replace(base, effort=resolve_variant(base.variant, base.model).effort)
    if base.pure and not base.model:
        raise DomainFailure("pure implementation requires an explicit or resolved model")
    if not base.read_only and claude:
        base = replace(
            base,
            append_system_prompt=implementation_prompt(
                False, fast and not base.variant.endswith("ultracode")
            ),
        )
    return base


def stepped_prepared(prepared: Prepared, large: bool) -> Prepared:
    if prepared.spec.profile != ImplementationProfile.FAST or not large:
        return prepared
    return replace(
        prepared,
        spec=replace(
            prepared.spec,
            implementation_steps=True,
            append_system_prompt=implementation_prompt(
                True, not prepared.spec.variant.endswith("ultracode")
            ),
        ),
    )


def limit_request(options: MandateOptions) -> LimitRequest:
    return LimitRequest(options.limits, options.budget_usd, options.max_turns, options.max_wall_min)


def mandate_limits(options: MandateOptions, task_type: str, settings: LimitSettings) -> RunLimits:
    chosen = profile(parse_depth(options.depth), task_type)
    return resolve_limits(limit_request(options), settings, chosen)


def launch_turns(limits: RunLimits, engine: str) -> int:
    return limits.max_turns if parse_provider(engine) is Provider.CLAUDE else 0


def effective_limits(limits: RunLimits, engine: str) -> RunLimits:
    return replace(limits, max_turns=launch_turns(limits, engine))


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
    read_hooks: bool = False
    preparation_seconds: float | None = None
    limits: RunLimits = NO_LIMITS
    pack_notes: tuple[Message, ...] = ()
    requested_limits: RunLimits = NO_LIMITS


def pack_notes(
    request: MandateRequest, protection: ChangePlan | None, pack: ContextPack | None
) -> tuple[Message, ...]:
    found = anchor_notes(pack.anchors, pack.budget) if pack is not None else ()
    return (*plan_notes(request, protection), *found)


def template_note(prepared: Prepared) -> Note | None:
    pending = prepared.composed.template
    if pending is None:
        return None
    return note(Status.INFO, pending.borrowed if prepared.spec.temporary_copy else pending.planned)


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


def validate(request: MandateRequest, required: tuple[str, ...] | None = None) -> None:
    missing = missing_fields(request, required)
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
        read_discipline: bool = False,
        timing: PhaseRecorder | None = None,
        default_profile: str = "balanced",
        default_variant: str = "",
        implementation_tools: tuple[str, ...] = (),
        fast_ready: Callable[[str], bool] | None = None,
        limits: LimitSettings | None = None,
        steps: SlowSteps | None = None,
    ) -> None:
        self._steps = steps if steps is not None else SlowSteps(None)
        self._fast_ready = fast_ready or self._engine_steerable
        self._implementation_tools = implementation_tools
        self._default_profile = default_profile
        self._default_variant = default_variant
        self._timing = timing or PhaseRecorder()
        self._read_discipline = read_discipline
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
        self._limits = (
            limits
            if limits is not None
            else LimitSettings(fixed=RunLimits(max(0.0, default_budget), max(0, default_max_turns)))
        )
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
        self._timing.reset()
        started = self._timing.start()
        options = replace(
            options,
            profile=resolved_profile(
                options,
                self._default_profile,
                self._default_engine,
                request.type,
                ready=self._fast_ready,
                pinned=self._routing is not None and self._routing.pinned(options.route.mode),
            ).value,
            variant=options.variant or self._default_variant,
        )
        self._validate(request, options)
        with self._timing.measure("forecast_plan"), self._steps.sequence() as phase:
            phase(PLAN_STEP, msg("progress.plan"))
            prepared = self._prepare_validated(request, signatures, options, preview, phase)
        return replace(prepared, preparation_seconds=self._timing.elapsed(started))

    def _validate(self, request: MandateRequest, options: MandateOptions) -> None:
        validate(request, options.required)
        engine = options.engine or self._default_engine
        fast = options.profile == ImplementationProfile.FAST
        if fast and (engine != "claude" or request.type == INVESTIGATION):
            raise DomainFailure("fast implementation requires a Claude writing request")
        if options.pure and engine != "claude":
            raise DomainFailure("pure implementation requires Claude")
        refusal = scout_refusal(
            request.type,
            parse_forced_shape(options.shape),
            options.simple,
            parse_provider(engine) is not None,
        )
        if refusal is not None:
            raise DomainFailure(english(refusal[0]), refusal[1])
        refused = docs_refusal(
            options.docs,
            request.type,
            simple=options.simple,
            fast=fast,
            pinned=has_pin(options.route.role_models, Role.DOCS),
        )
        if refused is not None:
            raise DomainFailure(english(refused[0]), refused[1])
        if self._refresh_index is not None:
            with self._timing.measure("index_refresh"):
                self._refresh_index()
        if (
            not options.simple
            and not fast
            and self._has_agents is not None
            and not self._has_agents()
        ):
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
        phase: Phase,
    ) -> Prepared:
        run_id = "" if preview or self._new_run_id is None else self._new_run_id()
        key = decision_key(request)
        self._scope.set(run_id, key, preview)
        protection = self._protection(request, options.plan_overrides)
        large = (
            options.profile == ImplementationProfile.FAST
            and request.type == MandateType.FEATURE
            and large_feature(request.type, self._edit_tokens(protection))
        )
        options = fast_defaults(options, request.type, large)
        engine_name = options.engine or self._default_engine
        engine = self._engines(engine_name)
        if engine is None:
            raise DomainFailure(f"unknown engine {engine_name}", "use claude, codex or opencode")
        investigation = request.type == INVESTIGATION
        fast = options.profile == ImplementationProfile.FAST
        refusal = readonly_unavailable(engine_name) if investigation else None
        if refusal is not None:
            raise DomainFailure(english(refusal))
        shape = parse_shape(options.shape)
        single = fast or single_context(request.type, options.simple, shape)
        claude = engine_name == "claude"
        graph_available = self._graph_mode() is GraphMode.CLI
        tools = self._tools(options, investigation, claude, graph_available)
        launcher = self._launchers(engine)
        route, wanted, docs = self._route(
            request, options, claude and not single and not investigation
        )
        applied = (
            self._routing.apply(request, route, engine_name, graph_available)
            if self._routing is not None and not options.simple
            else None
        )
        applied = self._launch_models(applied, options, single, fast)
        if claude and applied is not None and protection is not None and self._routing is not None:
            applied = self._routing.protect(applied, protection, options.session)
        pack = (
            self._context_pack(request, options.depth, Role.ORCHESTRATOR.value, protection)
            if self._context_pack is not None
            else None
        )
        applied = self._enrich(applied, request, options.depth, protection)
        depth = self._depth(options.depth, request.type)
        requested = mandate_limits(options, request.type, self._limits)
        limits = effective_limits(requested, engine_name)
        guess = self._estimate(options, applied, request.type, preview)
        budget_line = read_budget_line(depth, graph_available) if depth is not None else ""
        system = (
            analyst_system_prompt(self._analyst_body(), budget_line, graph_available)
            if single and claude and investigation
            else ""
        )
        base = LaunchSpec(
            kind="mandate",
            prompt="",
            cwd=self._cwd,
            allowed_tools=tools,
            disallowed_tools=(
                investigation_denied(options.simple, shape) if investigation else DEFAULT_DENIED
            )
            + (session_denied_tools(options.variant) if fast else ()),
            model=options.model
            or (applied.orchestrator or applied.single if applied is not None else ""),
            agents_file=applied.agents_file if applied is not None else "",
            effort=depth.effort if depth is not None and claude else "",
            append_system_prompt=system,
            unset_env=applied.unset if applied is not None else (),
            max_budget_usd=limits.budget_usd,
            max_turns=limits.max_turns,
            max_wall_s=limits.wall_s,
            tools=(
                self._implementation_tools
                if fast and self._implementation_tools
                else investigation_builtin_tools(options.simple, shape, graph_available)
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
                False
                if fast
                else True
                if claude
                and applied is not None
                and applied.agents
                and self._pipeline_read_discipline
                else None
            ),
            profile=options.profile,
            variant=options.variant,
            pure=options.pure or fast,
            verify_commands=protection.verify
            if protection is not None and not investigation
            else (),
        )
        base = implementation_spec(base, claude)
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
            implementation=fast,
        )
        if run_id:
            self._service.link_decisions(key, run_id)
            if options.intake_scope:
                self._service.link_decisions(options.intake_scope, run_id)
        spec = replace(base, prompt=composed.prompt, scope=composed.hint.option)
        prepared = Prepared(
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
                request=request,
                phase=phase,
            ),
            scout=wanted and has_scout(applied),
            docs=docs,
            read_hooks=self._hooked(launcher, spec),
            limits=limits,
            pack_notes=pack_notes(request, protection, pack),
            requested_limits=requested,
        )
        return stepped_prepared(prepared, large)

    def _launch_models(
        self, applied: Applied | None, options: MandateOptions, single: bool, fast: bool
    ) -> Applied | None:
        if single:
            applied = single_applied(applied, options)
        if applied is None or self._routing is None:
            return applied
        if not (options.pure or fast):
            return applied if single else self._routing.main_model(applied, options.model)
        model = options.model or applied.orchestrator or applied.single
        return self._routing.pure(applied, model, fast and not options.pure)

    def _tools(
        self, options: MandateOptions, investigation: bool, claude: bool, graph: bool
    ) -> tuple[str, ...]:
        if not claude:
            return ()
        if investigation:
            return investigation_tools(options.simple, parse_shape(options.shape), graph)
        tools = allowed_tools(self._stack(), graph)
        if options.profile != ImplementationProfile.FAST:
            return tools
        if options.variant.endswith("ultracode"):
            return (*tools, "Workflow")
        return tuple(tool for tool in tools if tool not in DELEGATION_TOOLS)

    def _engine_steerable(self, name: str) -> bool:
        engine = self._engines(name)
        if engine is None:
            return False
        return self._launchers(engine).steerable()

    def _edit_tokens(self, protection: ChangePlan | None) -> int:
        if self._forecaster is None or protection is None:
            return 0
        return self._forecaster.edit_tokens(protection)

    def _hooked(self, launcher: EngineLauncher, spec: LaunchSpec) -> bool:
        wanted = self._read_discipline if spec.read_discipline is None else spec.read_discipline
        return wanted and launcher.owns_profile(spec)

    def _route(
        self, request: MandateRequest, options: MandateOptions, native: bool
    ) -> tuple[RouteOptions, bool, DocsChoice | None]:
        route = replace(options.route, depth=options.depth) if options.depth else options.route
        if not native or options.simple:
            return route, False, None
        trial = options.sandbox or self._sandbox is not None
        mode, flag = docs_setting(options.docs, self._docs_mode)
        pinned = has_pin(options.route.role_models, Role.DOCS)
        docs = docs_choice(mode, request, trial, pinned, flag)
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
        request: MandateRequest | None = None,
        phase: Phase | None = None,
    ) -> tuple[PlannedForecast | None, Message | None]:
        if phase is not None:
            phase(FORECAST_STEP, msg("progress.forecast"))
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
                model=spec.model,
                max_turns=spec.max_turns,
                implementation_profile=spec.profile,
                variant=spec.variant or spec.effort,
                request=request,
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
            for role in (Role.SCOUT, Role.SENIOR, Role.TESTER)
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
        wall_start = self._timing.start()
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
            told = self._keep_template(prepared)
            if told is not None:
                progress.publish(told)
            self._record_forecast(prepared, progress)
            steering = session_steering(
                self._governor, prepared.launcher, prepared.spec, prepared.forecast, progress
            )
            if prepared.docs is not None and prepared.docs.reason is not DocsReason.FORCED_ON:
                progress.publish(note(Status.INFO, prepared.docs.message))
            for message in prepared.pack_notes:
                progress.publish(note(Status.WARN, message))
            watch = SessionWatch() if prepared.scout else None
            sink = watched(observer, watch) if watch is not None else observer
            report = self._service.run(
                prepared.composed,
                prepared.launcher,
                prepared.spec if steering is None else replace(prepared.spec, steer=True),
                progress,
                self._summarize,
                sink if steering is None else observed(sink, steering),
                self._timing.flush,
            )
            self._timing.flush(report.run.id)
            if steering is not None:
                report = replace(report, governor=tuple(steering.taken), governed=True)
            if prepared.read_hooks:
                report = replace(report, read_hooks=True)
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
            cost = None if report.run.partial else report.run.cost_usd
            self._routing.close(report.run.id, report.tests, cost)
            rows = self._routing.audit(report.run.id, applied, report.handoffs)
            report = replace(report, audit=rows)
        self._service.close_decisions(report.run.id, report.tests if report.ok else "failed")
        self._service.save_meta(report)
        if self._learn_run is not None:
            self._learn_run(report.run.id)
        elapsed = self._timing.elapsed(wall_start)
        if elapsed is not None:
            self._timing.seconds(
                "run_wall", elapsed + (prepared.preparation_seconds or 0.0), report.run.id
            )
        return report

    def _keep_template(self, prepared: Prepared) -> Note | None:
        pending = prepared.composed.template
        if pending is None or prepared.spec.temporary_copy:
            return template_note(prepared)
        return self._service.keep_template(pending)

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
        if report.implementation is not None:
            return report
        if self._final_suite is None or report.run.status == "interrupted":
            return report
        progress.publish(started("verdict", msg("mandate.verdict")))
        try:
            with self._timing.measure("verification", report.run.id):
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
    forge_ready: bool = True
    init_estimate: float | None = None
    limits: LimitSettings = field(default_factory=LimitSettings)
    profile: str = AUTO_PROFILE
    variant: str = ""
    pinned: bool = False


@dataclass(frozen=True, slots=True)
class MandatePreview:
    prompt: str
    command: str
    engine: str
    scope: str
    confidence: float
    roles: tuple[RoleRoute, ...] = ()
    per_role: bool = False
    template_note: Note | None = None


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
        template_note=template_note(prepared),
    )


ENGINE_MODEL_PREFIXES = {"claude": ("claude-",), "codex": ("gpt-", "o")}


def models_for(engine: str, models: tuple[str, ...]) -> tuple[str, ...]:
    prefixes = ENGINE_MODEL_PREFIXES.get(engine)
    if prefixes is None:
        return models
    return tuple(name for name in models if name.startswith(prefixes))
