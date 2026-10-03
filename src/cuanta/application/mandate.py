from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from cuanta.application.engine_run import EngineLauncher, LaunchSpec
from cuanta.application.gateway import CAPSULE_DIR
from cuanta.application.instinct import DecisionMaker
from cuanta.application.run_reports import RunReports
from cuanta.domain.audit import AuditRow
from cuanta.domain.capsules import capsule_id
from cuanta.domain.change_plan import ChangePlan, plan_metrics
from cuanta.domain.detection import Stack
from cuanta.domain.engine import AssistantText, EngineEvent, RunResult, ToolCall
from cuanta.domain.errors import DomainFailure
from cuanta.domain.governor import ReactionTaken
from cuanta.domain.governor_report import governor_metrics
from cuanta.domain.graph_policy import graphless_prompt
from cuanta.domain.implementation import ImplementationReport
from cuanta.domain.instinct import SCOPES, Choice, scope_hint_line
from cuanta.domain.ledger import Capsule, Run, Snapshot
from cuanta.domain.mandate import (
    INLINE_EVIDENCE_LIMIT,
    MandateRequest,
    Shape,
    TemplateError,
    builtin_block,
    clip_evidence,
    evidence_from_failure,
    extract_block,
    fill_request,
    single_context,
)
from cuanta.domain.messages import english, msg
from cuanta.domain.progress import Status, note
from cuanta.domain.report import strip_preamble
from cuanta.domain.scout import DocsChoice
from cuanta.domain.scout_report import DOCS_KEY, SCOUT_KEY, docs_payload
from cuanta.domain.telemetry import unreadable_note
from cuanta.domain.testing import GatewayStatus
from cuanta.ports.capsules import CapsuleStore
from cuanta.ports.ledger import Ledger
from cuanta.ports.progress import ProgressSink
from cuanta.ports.workspace import Workspace

TEMPLATE_PATH = "docs/MANDATE_TEMPLATE.md"
BASE_TOOLS = (
    "Read",
    "Grep",
    "Glob",
    "Write",
    "Edit",
    "Agent",
    "Task",
    "Skill",
    "Bash(cuanta test)",
    "Bash(cuanta test *)",
    "Bash(cuanta cat *)",
    "Bash(graphify *)",
)


def allowed_tools(stack: Stack, graph_available: bool = True) -> tuple[str, ...]:
    extra: list[str] = []
    for command in (stack.typecheck_command, stack.build_command):
        if command:
            extra.extend((f"Bash({command})", f"Bash({command} *)"))
    base = BASE_TOOLS if graph_available else BASE_TOOLS[:-1]
    return tuple(dict.fromkeys((*base, *extra)))


@dataclass(frozen=True, slots=True)
class Composed:
    prompt: str
    hint: Choice
    decision_id: int
    request: MandateRequest
    command: tuple[str, ...]
    simple: bool = False
    single: bool = False


@dataclass
class LiveView:
    progress: ProgressSink
    handoffs: list[str] = field(default_factory=list)
    tools: int = 0
    result: RunResult | None = None
    observer: Callable[[EngineEvent], None] | None = None

    def __call__(self, event: EngineEvent) -> None:
        if self.observer is not None:
            self.observer(event)
        if isinstance(event, ToolCall):
            self.tools += 1
            agent = event.spawned_agent
            if agent:
                self.handoffs.append(agent)
                arrow = " → ".join(self.handoffs[-4:])
                self.progress.publish(note(Status.INFO, msg("mandate.handoff", agents=arrow)))
        elif isinstance(event, RunResult):
            self.result = event
        elif isinstance(event, AssistantText) and "halt" in event.text.lower()[:200]:
            self.progress.publish(note(Status.WARN, msg("mandate.halt")))


@dataclass(frozen=True, slots=True)
class MandateReport:
    run: Run
    ok: bool
    changed_files: tuple[str, ...]
    tests: str
    tokens_by_agent: dict[str, int]
    utilization: float | None
    handoffs: tuple[str, ...]
    tool_calls: int
    hint: Choice
    run_file: str
    audit: tuple[AuditRow, ...] = ()
    text: str = ""
    report_path: str = ""
    task_type: str = ""
    simple: bool = False
    single: bool = False
    prompt_chars: int = 0
    change_plan: ChangePlan | None = None
    governor: tuple[ReactionTaken, ...] = ()
    scout: Mapping[str, object] | None = None
    docs: DocsChoice | None = None
    governed: bool = False
    read_hooks: bool = False
    implementation: ImplementationReport | None = None
    unreadable_telemetry: int = 0


class MandateService:
    def __init__(
        self,
        workspace: Workspace,
        ledger: Ledger,
        decisions: DecisionMaker,
        clock_iso: Callable[[], str],
        exclusions: frozenset[str] = frozenset(),
        capsules: CapsuleStore | None = None,
        scanned: Workspace | None = None,
        meta: Mapping[str, object] | None = None,
    ) -> None:
        self._capsules = capsules
        self._meta = dict(meta or {})
        self._workspace = workspace
        self._scanned = scanned or workspace
        self._reports = RunReports(workspace)
        self._ledger = ledger
        self._decisions = decisions
        self._clock_iso = clock_iso
        self._exclusions = exclusions
        self._snapshot_modes: dict[tuple[str, str], dict[str, int]] = {}

    def template_block(self) -> str:
        text = self._workspace.read_text(TEMPLATE_PATH)
        if text is None:
            raise DomainFailure(f"{TEMPLATE_PATH} not found", "run cuanta init first")
        try:
            return extract_block(text)
        except TemplateError as error:
            raise DomainFailure(str(error), "regenerate it with cuanta refresh") from error

    def from_failure(self) -> tuple[str, int]:
        latest = self._ledger.test_runs(limit=1, project_only=True)
        if not latest:
            raise DomainFailure("no gateway result yet", "run cuanta test first")
        record = latest[0]
        if record.status == GatewayStatus.GREEN.value:
            raise DomainFailure("last cuanta test was green · nap", "nothing to fix")
        signatures = self._ledger.signatures(record.id)
        rows = [
            (item.signature_id, item.verbatim, item.tests, item.first_test, item.location)
            for item in signatures
        ]
        return evidence_from_failure(rows, record.command, record.capsule_id), len(rows)

    def bounded(self, text: str) -> str:
        if len(text) <= INLINE_EVIDENCE_LIMIT or self._capsules is None:
            return text
        digest, _, size = self._capsules.put(text)
        reference = capsule_id(digest)
        self._ledger.add_capsule(
            Capsule(
                id=reference,
                sha256=digest,
                path=f"{CAPSULE_DIR}/{digest}.log",
                kind="evidence",
                size_bytes=size,
                lines=text.count("\n") + 1,
                summary=text[:200],
                created_at=self._clock_iso(),
            )
        )
        return clip_evidence(text, reference)

    def compose(
        self,
        request: MandateRequest,
        signatures: int,
        command: Callable[[str], list[str]],
        simple: bool = False,
        clarity: float | None = None,
        extra: str = "",
        shape: Shape = Shape.PIPELINE,
        graph_available: bool = True,
        context: str = "",
        implementation: bool = False,
    ) -> Composed:
        block = (
            "# MANDATE — implementation\n\n"
            "Implement the request in this native session.\n\n=== REQUEST ===\n"
            if implementation
            else builtin_block(request.type, simple, shape, graph_available)
            or self.template_block()
        )
        if not graph_available:
            block = graphless_prompt(block)
        request = replace(request, why=self.bounded(request.why))
        decision_context = {
            "kind": "scope",
            "type": request.type,
            "what": request.what,
            "where": request.where,
            "signatures": signatures,
            "clarity": clarity,
        }
        choice, decided = self._decisions.choose(
            english(msg("question.mandate_scope")), SCOPES, decision_context
        )
        hint = scope_hint_line(choice, decided.receipt.backend)
        if extra:
            hint = f"{hint}\n{extra}"
        prompt = fill_request(block, request, hint, context)
        return Composed(
            prompt,
            choice,
            decided.decision_id,
            request,
            tuple(command(prompt)),
            simple,
            implementation or simple or single_context(request.type, simple, shape),
        )

    def link_decisions(self, request_hash: str, run_id: str) -> int:
        return self._ledger.link_decisions(request_hash, run_id)

    def close_decisions(self, run_id: str, outcome: str) -> None:
        self._ledger.close_run_decisions(run_id, outcome)

    def snapshot(self, run_id: str, phase: str) -> dict[str, str]:
        scan = self._scanned.scan(self._exclusions, collect_files=True, all_files=True)
        self._snapshot_modes[(run_id, phase)] = dict(scan.modes)
        hashes: dict[str, str] = {}
        for path in scan.files:
            digest = self._scanned.sha256(path)
            if digest is not None:
                hashes[path] = digest
                if phase == "start":
                    self._reports.keep_blob(path, digest, self._scanned)
        self._ledger.add_snapshots(
            [Snapshot(run_id, phase, path, digest) for path, digest in hashes.items()]
        )
        return hashes

    def run(
        self,
        composed: Composed,
        launcher: EngineLauncher,
        spec: LaunchSpec,
        progress: ProgressSink,
        summarize: Callable[[str], tuple[dict[str, int], float | None]],
        observer: Callable[[EngineEvent], None] | None = None,
        before: Callable[[str], None] | None = None,
    ) -> MandateReport:
        view = LiveView(progress, observer=observer)
        holder: dict[str, str] = {}

        def before_launch(run_id: str) -> None:
            holder["run_id"] = run_id
            if before is not None:
                before(run_id)
            with launcher.timing.measure("snapshots_guards", run_id):
                self.snapshot(run_id, "start")

        launch = launcher.launch(spec, view, before_launch)
        if launch.unreadable > 0:
            progress.publish(note(Status.WARN, unreadable_note(launch.unreadable)))
        run = launch.run
        with launcher.timing.measure("snapshots_guards", run.id):
            end = self.snapshot(run.id, "end")
        start = {item.path: item.sha256 for item in self._ledger.snapshots(run.id, "start")}
        start_modes = self._snapshot_modes.pop((run.id, "start"), {})
        end_modes = self._snapshot_modes.pop((run.id, "end"), {})
        changed = tuple(
            sorted(
                path
                for path in set(start) | set(end)
                if start.get(path) != end.get(path) or start_modes.get(path) != end_modes.get(path)
            )
        )
        tests = self._ledger.test_runs(run_id=run.id, limit=1)
        tests_text = tests[0].status if tests else "not run"
        by_agent, utilization = summarize(run.id)
        result = view.result or launch.outcome.result
        raw_text = result.text if result is not None else ""
        with launcher.timing.measure("handoff", run.id):
            report_path = self._reports.save_report(run.id, raw_text) if raw_text else ""
        text = strip_preamble(raw_text)
        protected = spec.change_plan is not None and bool(
            plan_metrics(spec.change_plan, changed, run.engine)["guard_violations"]
        )
        outcome = "ok" if launch.outcome.ok and not protected else "failed"
        self._decisions.record_outcome(composed.decision_id, outcome)
        report = MandateReport(
            run=run,
            ok=launch.outcome.ok and not protected,
            changed_files=changed,
            tests=tests_text,
            tokens_by_agent=by_agent,
            utilization=utilization,
            handoffs=tuple(view.handoffs),
            tool_calls=view.tools,
            hint=composed.hint,
            run_file=self._reports.meta_path(run.id),
            text=text,
            report_path=report_path,
            task_type=composed.request.type,
            simple=composed.simple,
            single=composed.single,
            prompt_chars=len(composed.prompt),
            change_plan=spec.change_plan,
            implementation=launch.implementation,
            unreadable_telemetry=launch.unreadable,
        )
        if launch.implementation is not None:
            report = replace(report, tests="green" if launch.implementation.passed else "red")
        self.save_meta(report)
        return report

    def save_meta(self, report: MandateReport) -> None:
        existing = self._reports.meta(report.run.id) or {}
        self._reports.save_meta(report.run.id, {**existing, **report_payload(report), **self._meta})


def report_payload(report: MandateReport) -> dict[str, object]:
    run = report.run
    payload: dict[str, object] = {
        "run_id": run.id,
        "status": run.status,
        "engine": run.engine,
        "model": run.model,
        "hu": run.hu_ref,
        "scope_hint": report.hint.option,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "cost_usd": run.cost_usd,
        "partial": run.partial,
        "cost_source": run.cost_source,
        "changed_files": list(report.changed_files),
        "tests": report.tests,
        "tokens_by_agent": report.tokens_by_agent,
        "utilization": report.utilization,
        "handoffs": list(report.handoffs),
        "tool_calls": report.tool_calls,
        "spectrum": f"cuanta spectrum {run.id}",
        "task_type": report.task_type,
        "simple": report.simple,
        "shape": (Shape.SINGLE if report.single else Shape.PIPELINE).value,
        "max_turns": run.max_turns,
        "turns": run.turns,
        "end_reason": run.end_reason,
        "prompt_chars": report.prompt_chars,
        "report": report.report_path,
        "audit": [
            {
                "agent": row.agent,
                "role": row.role,
                "planned": row.planned,
                "actual": list(row.actual),
                "status": row.status.value,
                "cause": row.cause_text,
            }
            for row in report.audit
        ],
    }
    governor = governor_metrics(report.governor, recorded=report.governed, hooks=report.read_hooks)
    if governor:
        payload["governor"] = governor
    if report.scout is not None:
        payload[SCOUT_KEY] = dict(report.scout)
    if report.docs is not None:
        payload[DOCS_KEY] = docs_payload(report.docs)
    if report.change_plan is not None:
        payload.update(plan_metrics(report.change_plan, report.changed_files, run.engine))
    if report.implementation is not None:
        payload["implementation"] = report.implementation.payload()
    if report.unreadable_telemetry > 0:
        payload["telemetry_unreadable"] = report.unreadable_telemetry
    return payload
