from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Protocol

from cuanta.application.detect import FORGE_STATE_FILE, DetectProject, read_forge_state
from cuanta.domain.detection import Detection, ForgeState, GraphMode
from cuanta.domain.errors import CuantaError
from cuanta.domain.forge_state import forge_will_run, handoff_state, state_to_dict
from cuanta.domain.forge_verify import Finding
from cuanta.domain.graph_policy import (
    GraphBranch,
    GraphPlan,
    graph_in_background,
    graph_outcome,
    plan_graph,
)
from cuanta.domain.ignore import CUANTA_GITIGNORE, with_entries
from cuanta.domain.messages import Message, english, msg
from cuanta.domain.new_files import Suggestion
from cuanta.domain.progress import Status, finished, note, started, took
from cuanta.ports.graph import GraphScheduler, GraphTool
from cuanta.ports.progress import ProgressSink
from cuanta.ports.system import Clock
from cuanta.ports.workspace import Workspace

INIT_STATE_FILE = ".cuanta/state.json"
STAGES = ("detect", "graph", "telemetry", "forge", "verify")
GRAPH_COMMAND = "graphify update ."
REFRESHED_STAGES = frozenset({"forge", "verify"})


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
    suggestions: list[Suggestion] = field(default_factory=list)
    run_id: str = ""
    cost_usd: float | None = 0.0
    refresh: bool = False
    forge_ran: bool = False
    baseline_tokens: int = 0
    registration: str = ""
    registration_message: Message | None = None
    denials: list[str] = field(default_factory=list)
    forge_runs: bool = True
    skip_graph: bool = False

    @property
    def keeps_forge(self) -> bool:
        return self.refresh and not self.forge_runs


@dataclass(frozen=True, slots=True)
class StageResult:
    status: Status
    detail: str = ""
    message: Message | None = None
    seconds: float | None = None


def timed(message: Message | None, seconds: float) -> Message:
    if message is None or not english(message):
        return took(seconds)
    return took(seconds, message)


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
    refresh_forge: bool = False
    skip_graph: bool = False


@dataclass(frozen=True, slots=True)
class InitReport:
    context: InitContext
    stages: tuple[tuple[str, StageResult], ...]
    resumed_from: str | None
    seconds: float | None = None

    @property
    def ok(self) -> bool:
        return all(result.status is not Status.FAIL for _, result in self.stages) and (
            self.context.verify_ok
        )


def _progress_document(workspace: Workspace) -> dict[str, object]:
    text = workspace.read_text(INIT_STATE_FILE)
    if text is None:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _read_progress(workspace: Workspace) -> list[str]:
    completed = _progress_document(workspace).get("completed")
    if not isinstance(completed, list):
        return []
    done = [stage for stage in completed if stage in STAGES]
    return [] if len(done) == len(STAGES) else done


def _forge_was_running(workspace: Workspace) -> bool:
    recorded = _progress_document(workspace).get("forge_runs")
    return recorded if isinstance(recorded, bool) else True


def can_resume(workspace: Workspace) -> bool:
    return bool(_read_progress(workspace))


def _write_progress(
    workspace: Workspace, completed: list[str], run_id: str, forge_runs: bool
) -> None:
    document = {"completed": completed, "run_id": run_id, "forge_runs": forge_runs}
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
    def __init__(
        self, workspace: Workspace, graph: GraphTool, scheduler: GraphScheduler | None = None
    ) -> None:
        self._workspace = workspace
        self._graph = graph
        self._scheduler = scheduler

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
        if context.skip_graph:
            skipped = msg("stage.skip_graph")
            context.graph_line = english(skipped)
            return stage_of(Status.SKIP, skipped)
        background = graph_in_background(context.forge_runs, plan.branch)
        scheduler = self._scheduler if background else None
        if context.dry_run:
            if plan.branch is GraphBranch.INSTALL:
                context.planned.append(msg("plan.install_graph"))
            if plan.branch in {GraphBranch.UPDATE, GraphBranch.INSTALL}:
                key = "plan.run" if scheduler is None else "plan.run_background"
                context.planned.append(msg(key, command=GRAPH_COMMAND))
            context.graph_line = plan.reason
            return stage_of(Status.INFO, plan.message)
        succeeded, failure = True, ""
        if plan.branch is GraphBranch.INSTALL:
            installed = self._graph.install()
            succeeded, failure = installed.ok, installed.detail
        if succeeded and scheduler is not None:
            return self._schedule(context, plan, scheduler)
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

    def _schedule(
        self, context: InitContext, plan: GraphPlan, scheduler: GraphScheduler
    ) -> StageResult:
        scheduled = scheduler.schedule_update()
        if not scheduled.ok:
            context.graph_mode = "none"
            failed = graph_outcome(plan, False, scheduled.detail)
            context.graph_line = english(failed)
            return stage_of(Status.WARN, failed)
        if plan.branch is GraphBranch.INSTALL:
            context.graph_mode = "cli"
        outcome = msg("graph.scheduled", log=scheduled.detail)
        context.graph_line = english(outcome)
        return stage_of(Status.OK, outcome)


class HandoffWriter:
    def __init__(self, workspace: Workspace, clock: Clock) -> None:
        self._workspace = workspace
        self._clock = clock

    def _keeps_state(self, kept: bool) -> bool:
        return kept and read_forge_state(self._workspace).state is not None

    def planned(self, detection: Detection, kept: bool = False) -> list[Message]:
        changes: list[Message] = []
        if not self._keeps_state(kept):
            changes.append(msg("plan.forge_state", path=FORGE_STATE_FILE))
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
        if not (context.keeps_forge and existing is not None):
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
        monotonic: Callable[[], float] = time.monotonic,
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
        self._monotonic = monotonic

    def detect(self) -> Detection:
        return self._detector.run()

    def run(self, options: InitOptions) -> InitReport:
        progress = self._progress
        begun = self._monotonic()
        progress.publish(started("detect", msg("stage.detect")))
        detection = self._detector.run()
        context = InitContext(
            detection=detection, dry_run=options.dry_run, skip_graph=options.skip_graph
        )
        context.refresh = detection.forge_state is ForgeState.INITIALIZED
        detected = self._monotonic() - begun
        found = msg(
            "stage.detect.done", files=f"{detection.file_count:,}", size=detection.size_tier.value
        )
        progress.publish(finished("detect", Status.OK, took(detected, found), detected))
        results: list[tuple[str, StageResult]] = [
            ("detect", replace(stage(Status.OK, "stage.detect.ok"), seconds=detected))
        ]
        completed = [] if options.dry_run else _read_progress(self._workspace)
        if options.refresh_forge:
            completed = [key for key in completed if key not in REFRESHED_STAGES]
        resumed_from = next((stage for stage in STAGES if stage not in completed), None)
        if completed and resumed_from is not None:
            progress.publish(note(Status.RESUME, msg("stage.resume", stage=resumed_from)))
        else:
            completed = ["detect"]
            resumed_from = None
        context.forge_runs = forge_will_run(
            detection.forge_state,
            skip=options.skip_forge,
            refresh=options.refresh_forge,
            resuming=resumed_from == "forge" and _forge_was_running(self._workspace),
        )
        if context.refresh and context.forge_runs:
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
            opened = self._monotonic()
            try:
                if skip_reason or runner is None:
                    result = stage(Status.SKIP, f"stage.{skip_reason or 'unavailable'}")
                else:
                    result = runner(context)
            except CuantaError as error:
                result = stage(Status.FAIL, "stage.error", error=error.message)
            seconds = self._monotonic() - opened
            result = replace(result, seconds=seconds)
            progress.publish(finished(key, result.status, timed(result.message, seconds), seconds))
            results.append((key, result))
            if result.status is Status.FAIL:
                if not options.dry_run:
                    _write_progress(self._workspace, completed, context.run_id, context.forge_runs)
                break
            completed.append(key)
            if not options.dry_run:
                _write_progress(self._workspace, completed, context.run_id, context.forge_runs)
        if options.dry_run:
            context.planned[0:0] = self._handoff.planned(detection, context.keeps_forge)
        return InitReport(
            context=context,
            stages=tuple(results),
            resumed_from=resumed_from,
            seconds=self._monotonic() - begun,
        )

    def _graph_with_handoff(self, context: InitContext) -> StageResult:
        result = self._graph(context)
        if not context.dry_run:
            self._handoff.write(context)
        return result
