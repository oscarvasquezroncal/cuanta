from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cuanta.application.detect import DetectProject, read_forge_state
from cuanta.application.engine_run import EngineLauncher, LaunchSpec
from cuanta.application.forge import (
    FORGE_DISALLOWED_TOOLS,
    ForgeProgress,
    VerifyStage,
    forge_allowed_tools,
    forge_prompt,
    promote_staged,
)
from cuanta.application.init_project import GraphStage, InitContext, StageResult, stage
from cuanta.domain.detection import Detection
from cuanta.domain.errors import NotAvailable
from cuanta.domain.messages import msg
from cuanta.domain.progress import Status, StepFinished, started
from cuanta.ports.forge import ForgeKit
from cuanta.ports.progress import ProgressSink
from cuanta.ports.workspace import Workspace


@dataclass(frozen=True, slots=True)
class RefreshReport:
    detection: Detection
    transition: str
    tier_changed: bool
    stages: tuple[tuple[str, StageResult], ...]
    context: InitContext

    @property
    def ok(self) -> bool:
        return all(result.status is not Status.FAIL for _, result in self.stages)


def tier_transition(previous: str, current: str, evidence: str) -> tuple[str, bool]:
    if not previous:
        return f"verify: unrecorded → {current} ({evidence})", True
    if previous == current:
        return f"verify: {current} (unchanged)", False
    return f"verify: {previous} → {current} ({evidence})", True


class RefreshProject:
    def __init__(
        self,
        workspace: Workspace,
        detector: DetectProject,
        graph_stage: GraphStage,
        kit: ForgeKit,
        launcher_factory: Callable[[], EngineLauncher | None],
        verify_stage: VerifyStage,
        progress: ProgressSink,
    ) -> None:
        self._workspace = workspace
        self._detector = detector
        self._graph = graph_stage
        self._kit = kit
        self._launcher_factory = launcher_factory
        self._verify = verify_stage
        self._progress = progress

    def _forge(self, context: InitContext) -> StageResult:
        launcher = self._launcher_factory()
        if launcher is None:
            return stage(Status.SKIP, "stage.claude_missing")
        missing = launcher.engine.missing_flags()
        if missing:
            raise NotAvailable(
                f"claude lacks flags cuanta needs: {', '.join(missing)}", "upgrade Claude Code"
            )
        summary = self._kit.install(dry_run=False)
        context.new_files.extend(summary.new_files)
        watcher = ForgeProgress(self._progress)
        spec = LaunchSpec(
            kind="refresh",
            prompt=forge_prompt(self._kit.refresh_prompt(), context.detection),
            cwd=str(self._workspace.root),
            allowed_tools=forge_allowed_tools(context.graph_mode),
            disallowed_tools=FORGE_DISALLOWED_TOOLS,
            scope="refresh",
        )
        launch = launcher.launch(spec, watcher)
        _, kept = promote_staged(self._workspace)
        context.new_files.extend(kept)
        context.run_id = launch.run.id
        context.forge_ran = True
        context.cost_usd = launch.run.cost_usd
        if not launch.outcome.ok:
            return stage(Status.FAIL, "stage.claude_exit", code=launch.outcome.exit_code)
        if launch.run.cost_usd is None:
            return stage(Status.OK, "stage.run_unpriced", run=launch.run.id)
        cost = f"{launch.run.cost_usd:.2f}"
        return stage(Status.OK, "stage.run_cost", run=launch.run.id, cost=cost)

    def run(self) -> RefreshReport:
        detection = self._detector.run()
        stored = read_forge_state(self._workspace).state
        previous = stored.verify_tier if stored is not None else ""
        transition, changed = tier_transition(
            previous, detection.verify_tier.value, detection.verify.evidence
        )
        context = InitContext(detection=detection, dry_run=False, refresh=True)
        stages: list[tuple[str, StageResult]] = []
        steps: list[tuple[str, str, Callable[[InitContext], StageResult]]] = [
            ("graph", "stage.graph_reindex", self._graph),
            ("forge", "stage.forge_refresh", self._forge),
            ("verify", "stage.verify", self._verify),
        ]
        for key, label, step in steps:
            self._progress.publish(started(key, msg(label)))
            try:
                result = step(context)
            except NotAvailable as error:
                result = stage(Status.FAIL, "stage.error", error=error.message)
            self._progress.publish(StepFinished(key, result.status, result.detail, result.message))
            stages.append((key, result))
            if result.status is Status.FAIL:
                break
        return RefreshReport(detection, transition, changed, tuple(stages), context)
