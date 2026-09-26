from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from cuanta.application.engine_run import DEFAULT_DENIED, EngineLauncher, LaunchSpec
from cuanta.application.instinct import DecisionScope
from cuanta.application.mandate import Composed, MandateReport, MandateService, allowed_tools
from cuanta.application.route_apply import Applied, MandateRouting, RouteOptions
from cuanta.domain.agents import role_of
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
from cuanta.domain.guarantees import readonly_unavailable
from cuanta.domain.mandate import (
    INVESTIGATION,
    MandateRequest,
    MandateType,
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
from cuanta.domain.progress import Status, finished, started
from cuanta.domain.routing import Role
from cuanta.ports.engine import Engine
from cuanta.ports.progress import ProgressSink

PROMPT_PLACEHOLDER = "<prompt>"
RUN_PLACEHOLDER = "<run id>"
TRACE_PLACEHOLDER = "<trace>"


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


@dataclass(frozen=True, slots=True)
class Prepared:
    composed: Composed
    engine_name: str
    launcher: EngineLauncher
    spec: LaunchSpec
    applied: Applied | None = None


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
    ) -> None:
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
        validate(request)
        if not options.simple and self._has_agents is not None and not self._has_agents():
            raise DomainFailure(
                "this project has no Forge agents yet (.claude/agents)",
                "run cuanta init first, or use simple mode (--simple)",
            )
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
        route = replace(options.route, depth=options.depth) if options.depth else options.route
        applied = (
            self._routing.apply(request, route, engine_name, graph_available)
            if self._routing is not None and not options.simple
            else None
        )
        if applied is not None and single:
            applied = replace(applied, agents=None, agents_file="")
        routed = applied.orchestrator or applied.single if applied is not None else ""
        depth = self._depth(options.depth, request.type)
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
            model=options.model or routed,
            agents_file=applied.agents_file if applied is not None else "",
            effort=depth.effort if depth is not None and claude else "",
            append_system_prompt=system,
            unset_env=applied.unset if applied is not None else (),
            max_budget_usd=resolve_budget(options, request.type, self._default_budget),
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
        )

        def command(prompt: str) -> list[str]:
            preview = launcher.request(
                replace(base, prompt=prompt), RUN_PLACEHOLDER, TRACE_PLACEHOLDER, None
            )
            return engine.command(preview)

        extra = "" if system or not options.depth else budget_line
        composed = self._service.compose(
            request,
            signatures,
            command,
            options.simple,
            options.route.clarity,
            extra,
            shape,
            graph_available,
        )
        if run_id:
            self._service.link_decisions(key, run_id)
            if options.intake_scope:
                self._service.link_decisions(options.intake_scope, run_id)
        spec = replace(base, prompt=composed.prompt, scope=composed.hint.option)
        return Prepared(composed, engine_name, launcher, spec, applied)

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
        if not engine.available():
            raise NotAvailable(f"{name} not found on PATH", "install it or pick another engine")
        missing = engine.missing_flags()
        if missing:
            raise NotAvailable(
                f"{name} lacks flags cuanta needs: {', '.join(missing)}", f"upgrade {name}"
            )
        self._active = engine
        try:
            report = self._service.run(
                prepared.composed,
                prepared.launcher,
                prepared.spec,
                progress,
                self._summarize,
                observer,
            )
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
        return report

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


def preview_of(prepared: Prepared) -> MandatePreview:
    composed = prepared.composed
    return MandatePreview(
        prompt=composed.prompt,
        command=display_command(composed.command, composed.prompt),
        engine=prepared.engine_name,
        scope=composed.hint.option,
        confidence=composed.hint.probability,
    )


ENGINE_MODEL_PREFIXES = {"claude": ("claude-",), "codex": ("gpt-", "o")}


def models_for(engine: str, models: tuple[str, ...]) -> tuple[str, ...]:
    prefixes = ENGINE_MODEL_PREFIXES.get(engine)
    if prefixes is None:
        return models
    return tuple(name for name in models if name.startswith(prefixes))
