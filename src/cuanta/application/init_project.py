from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from cuanta.application.detect import FORGE_STATE_FILE, DetectProject, read_forge_state
from cuanta.domain.detection import Detection, ForgeState, GraphMode
from cuanta.domain.errors import CuantaError
from cuanta.domain.forge_state import handoff_state, state_to_dict
from cuanta.domain.forge_verify import Finding
from cuanta.domain.graph_policy import GraphBranch, GraphPlan, graph_outcome, plan_graph
from cuanta.domain.ignore import CUANTA_GITIGNORE, with_entries
from cuanta.domain.messages import Message, english, msg
from cuanta.domain.progress import Status, StepFinished, finished, note, started
from cuanta.ports.graph import GraphTool
from cuanta.ports.progress import ProgressSink
from cuanta.ports.system import Clock
from cuanta.ports.workspace import Workspace

INIT_STATE_FILE = ".cuanta/state.json"
STAGES = ("detect", "graph", "telemetry", "forge", "verify")


@dataclass(slots=True)
class InitContext:
    detection: Detection
    dry_run: bool
    graph_line: str = ""
    graph_mode: str = ""
    telemetry_line: str = "not wired"
    forge_line: str = ""
    verify_ok: bool = True
    verify_lines: list[Finding] = field(default_factory=list)
    planned: list[Message] = field(default_factory=list)
    new_files: list[str] = field(default_factory=list)
    run_id: str = ""
    cost_usd: float | None = 0.0
    refresh: bool = False
    forge_ran: bool = False
    baseline_tokens: int = 0
    registration: str = ""
    registration_message: Message | None = None
    denials: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class StageResult:
    status: Status
    detail: str = ""
    message: Message | None = None


def stage(status: Status, key: str, **params: object) -> StageResult:
    message = msg(key, **params)
    return StageResult(status, english(message), message)


def stage_of(status: Status, message: Message) -> StageResult:
    return StageResult(status, english(message), message)


class InitStage(Protocol):
    def __call__(self, context: InitContext) -> StageResult: ...


@dataclass(frozen=True, slots=True)
class InitOptions:
    dry_run: bool = False
    skip_forge: bool = False
    skip_telemetry: bool = False


@dataclass(frozen=True, slots=True)
class InitReport:
    context: InitContext
    stages: tuple[tuple[str, StageResult], ...]
    resumed_from: str | None

    @property
    def ok(self) -> bool:
        return all(result.status is not Status.FAIL for _, result in self.stages) and (
            self.context.verify_ok
        )


def _read_progress(workspace: Workspace) -> list[str]:
    text = workspace.read_text(INIT_STATE_FILE)
    if text is None:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    completed = data.get("completed") if isinstance(data, dict) else None
    if not isinstance(completed, list):
        return []
    done = [stage for stage in completed if stage in STAGES]
    return [] if len(done) == len(STAGES) else done


def _write_progress(workspace: Workspace, completed: list[str], run_id: str) -> None:
    document = {"completed": completed, "run_id": run_id}
    workspace.write_text(INIT_STATE_FILE, json.dumps(document, indent=2) + "\n")


def ensure_cuanta_dir(workspace: Workspace) -> None:
    if workspace.read_text(".cuanta/.gitignore") is None:
        workspace.write_text(".cuanta/.gitignore", CUANTA_GITIGNORE)


def ensure_gitignore(workspace: Workspace, entries: tuple[str, ...]) -> tuple[str, ...]:
    existing = workspace.read_text(".gitignore") or ""
    updated = with_entries(existing, entries)
    if updated is None:
        return ()
    workspace.write_text(".gitignore", updated)
    return tuple(entry for entry in entries if entry not in existing.splitlines())


class GraphStage:
    def __init__(self, workspace: Workspace, graph: GraphTool) -> None:
        self._workspace = workspace
        self._graph = graph

    def plan(self, detection: Detection) -> GraphPlan:
        return plan_graph(detection.size_tier, detection.graph_mode, detection.graph_evidence)

    def __call__(self, context: InitContext) -> StageResult:
        detection = context.detection
        plan = self.plan(detection)
        context.graph_mode = detection.graph_mode.value
        if detection.graph_mode is GraphMode.BROKEN:
            context.graph_mode = "none"
            context.graph_line = plan.reason
            return stage_of(Status.INFO if context.dry_run else Status.SKIP, plan.message)
        if context.dry_run:
            if plan.branch is GraphBranch.UPDATE:
                context.planned.append(msg("plan.run", command="graphify update ."))
            if plan.branch is GraphBranch.INSTALL:
                context.planned.append(msg("plan.install_graph"))
                context.planned.append(msg("plan.run", command="graphify update ."))
            context.graph_line = plan.reason
            return stage_of(Status.INFO, plan.message)
        succeeded, failure = True, ""
        if plan.branch is GraphBranch.INSTALL:
            installed = self._graph.install()
            succeeded, failure = installed.ok, installed.detail
        if succeeded and plan.branch in {GraphBranch.UPDATE, GraphBranch.INSTALL}:
            updated = self._graph.update(self._workspace.root)
            succeeded, failure = updated.ok, updated.detail
        if not succeeded:
            context.graph_mode = "none"
        elif plan.branch is GraphBranch.INSTALL:
            context.graph_mode = "cli"
        outcome = graph_outcome(plan, succeeded, failure)
        context.graph_line = english(outcome)
        status = Status.OK if succeeded else Status.WARN
        return stage_of(status, outcome)


class HandoffWriter:
    def __init__(self, workspace: Workspace, clock: Clock) -> None:
        self._workspace = workspace
        self._clock = clock

    def planned(self, detection: Detection) -> list[Message]:
        changes = [msg("plan.forge_state", path=FORGE_STATE_FILE)]
        changes.append(msg("plan.write", path=".cuanta/.gitignore"))
        if detection.vcs:
            entries = self._ignore_entries(detection, detection.graph_mode.value)
            missing = with_entries(self._workspace.read_text(".gitignore") or "", entries)
            if missing is not None:
                changes.append(msg("plan.gitignore", entries=", ".join(entries)))
        return changes

    def _ignore_entries(self, detection: Detection, graph_mode: str) -> tuple[str, ...]:
        entries = [FORGE_STATE_FILE]
        if graph_mode == "cli" and detection.size_tier.value != "small":
            entries.append("graphify-out/")
        return tuple(entries)

    def write(self, context: InitContext) -> None:
        detection = context.detection
        existing = read_forge_state(self._workspace).state
        in_flight = existing is not None and not existing.complete
        if in_flight and existing is not None and set(existing.phases_completed) - {"0", "0.5"}:
            return
        graph_mode = context.graph_mode or detection.graph_mode.value
        state = handoff_state(detection, self._clock.now_iso(), graph_mode)
        self._workspace.write_text(
            FORGE_STATE_FILE, json.dumps(state_to_dict(state), indent=2) + "\n"
        )
        ensure_cuanta_dir(self._workspace)
        if detection.vcs:
            ensure_gitignore(self._workspace, self._ignore_entries(detection, graph_mode))


class InitProject:
    def __init__(
        self,
        workspace: Workspace,
        detector: DetectProject,
        graph_stage: GraphStage,
        handoff: HandoffWriter,
        progress: ProgressSink,
        telemetry_stage: InitStage | None = None,
        forge_stage: InitStage | None = None,
        verify_stage: InitStage | None = None,
        run_id_factory: Callable[[], str] = lambda: "",
    ) -> None:
        self._workspace = workspace
        self._detector = detector
        self._graph = graph_stage
        self._handoff = handoff
        self._progress = progress
        self._telemetry = telemetry_stage
        self._forge = forge_stage
        self._verify = verify_stage
        self._run_id_factory = run_id_factory

    def detect(self) -> Detection:
        return self._detector.run()

    def run(self, options: InitOptions) -> InitReport:
        progress = self._progress
        progress.publish(started("detect", msg("stage.detect")))
        detection = self._detector.run()
        context = InitContext(detection=detection, dry_run=options.dry_run)
        context.refresh = detection.forge_state is ForgeState.INITIALIZED
        progress.publish(
            finished(
                "detect",
                Status.OK,
                msg(
                    "stage.detect.done",
                    files=f"{detection.file_count:,}",
                    size=detection.size_tier.value,
                ),
            )
        )
        results: list[tuple[str, StageResult]] = [("detect", stage(Status.OK, "stage.detect.ok"))]
        completed = [] if options.dry_run else _read_progress(self._workspace)
        resumed_from = next((stage for stage in STAGES if stage not in completed), None)
        if completed and resumed_from is not None:
            progress.publish(note(Status.RESUME, msg("stage.resume", stage=resumed_from)))
        else:
            completed = ["detect"]
            resumed_from = None
        if context.refresh:
            progress.publish(note(Status.INFO, msg("stage.refresh_route")))
        context.run_id = self._run_id_factory()
        stages: list[tuple[str, InitStage | None, str]] = [
            ("graph", self._graph_with_handoff, ""),
            (
                "telemetry",
                self._telemetry,
                "skip_telemetry" if options.skip_telemetry else "",
            ),
            ("forge", self._forge, "skip_forge" if options.skip_forge else ""),
            ("verify", self._verify, ""),
        ]
        for key, runner, skip_reason in stages:
            if key in completed:
                results.append((key, stage(Status.SKIP, "stage.previous_life")))
                continue
            progress.publish(started(key, msg(f"stage.{key}")))
            try:
                if skip_reason or runner is None:
                    result = stage(Status.SKIP, f"stage.{skip_reason or 'unavailable'}")
                else:
                    result = runner(context)
            except CuantaError as error:
                result = stage(Status.FAIL, "stage.error", error=error.message)
            progress.publish(StepFinished(key, result.status, result.detail, result.message))
            results.append((key, result))
            if result.status is Status.FAIL:
                if not options.dry_run:
                    _write_progress(self._workspace, completed, context.run_id)
                break
            completed.append(key)
            if not options.dry_run:
                _write_progress(self._workspace, completed, context.run_id)
        if options.dry_run:
            context.planned[0:0] = self._handoff.planned(detection)
        return InitReport(context=context, stages=tuple(results), resumed_from=resumed_from)

    def _graph_with_handoff(self, context: InitContext) -> StageResult:
        result = self._graph(context)
        if not context.dry_run:
            self._handoff.write(context)
        return result
