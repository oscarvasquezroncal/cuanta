from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path

from cuanta.application.engine_run import EngineLauncher, LaunchSpec
from cuanta.application.forge_suggestions import listed, relocate, siblings, suggest
from cuanta.application.init_project import InitContext, StageResult, stage
from cuanta.application.mandate_template import MandateTemplates
from cuanta.domain.detection import Detection, GraphMode
from cuanta.domain.engine import AssistantText, EngineEvent, ToolCall, phase_markers
from cuanta.domain.errors import CuantaError, NotAvailable
from cuanta.domain.forge_state import FORGE_PHASES
from cuanta.domain.forge_template import PendingTemplate, TemplateState
from cuanta.domain.forge_verify import (
    FORGE_DOCS,
    GATEWAY_FILES,
    Finding,
    ForgeTree,
    checked,
    estimated_tokens,
    line_count,
    passed,
    suggestion_findings,
    verify_tree,
)
from cuanta.domain.ledger import Baseline
from cuanta.domain.messages import Message, english, msg
from cuanta.domain.new_files import (
    is_suggested,
    original_of,
    same_text,
    sibling_original,
    suggested_path,
)
from cuanta.domain.pricing import dollars
from cuanta.domain.progress import Status, file_count, note
from cuanta.domain.telemetry import unreadable_note
from cuanta.ports.forge import ForgeKit
from cuanta.ports.ledger import Ledger
from cuanta.ports.progress import ProgressSink
from cuanta.ports.system import Clock
from cuanta.ports.workspace import Workspace

FORGE_ALLOWED_TOOLS = ("Read", "Grep", "Glob", "Write", "Edit", "Skill", "Bash(graphify *)")
FORGE_DISALLOWED_TOOLS = ("Bash(git *)",)


def forge_allowed_tools(mode: str) -> tuple[str, ...]:
    return FORGE_ALLOWED_TOOLS if mode == GraphMode.CLI.value else FORGE_ALLOWED_TOOLS[:-1]


def forge_prompt(prompt: str, detection: Detection) -> str:
    if detection.graph_mode is GraphMode.BROKEN:
        mode = "REFRESH" if "REFRESH mode" in prompt else "INIT"
        marker = "This headless run cannot write"
        tail = prompt.partition(marker)
        staging = f"{marker}{tail[2]}" if tail[1] else ""
        return "\n\n".join(
            part
            for part in (
                f"Use the agent-system-init skill in {mode} mode. Finish the remaining phases.",
                "Cuanta completed detection and Phase 0.5. GRAPH_MODE=none because the graph "
                "launcher failed its health check. Do not repeat detection, install or index a "
                "graph, or run graph queries. Use Grep and Glob for structural discovery. "
                "Keep the existing state and resume from .claude/forge-state.json.",
                staging,
            )
            if part
        )
    return prompt


STAGING_DIR = ".cuanta/forge-out"
STATE_TARGET = ".claude/forge-state.json"
RULEBOOK = "CLAUDE.md"
RULEBOOK_BACKUP = ".cuanta/backups/CLAUDE.md.bak"


def promote_staged(workspace: Workspace) -> tuple[list[str], list[str]]:
    promoted: list[str] = []
    kept: list[str] = []
    prefix = f"{STAGING_DIR}/claude/"
    for staged in workspace.files_under(STAGING_DIR):
        if not staged.startswith(prefix):
            continue
        content = workspace.read_text(staged)
        if content is None:
            continue
        mirrored = ".claude/" + staged.removeprefix(prefix)
        target = sibling_original(mirrored) or mirrored
        existing = workspace.read_text(target)
        if target == STATE_TARGET or existing is None:
            workspace.write_text(target, content)
            promoted.append(target)
        elif not same_text(existing, content):
            kept.append(suggest(workspace, target, content))
        workspace.remove(staged)
    return promoted, kept


def report_rulebook(workspace: Workspace, progress: ProgressSink, before: str | None) -> None:
    after = workspace.read_text(RULEBOOK)
    if before is None or after is None or same_text(before, after):
        return
    workspace.write_text(RULEBOOK_BACKUP, before)
    message = msg(
        "forge.rulebook_rewritten",
        before=line_count(before),
        after=line_count(after),
        backup=RULEBOOK_BACKUP,
    )
    progress.publish(note(Status.INFO, message))


def registration_message(denials: tuple[str, ...], skill_calls: int) -> Message:
    if "Skill" in denials:
        return msg("registration.deferred")
    if skill_calls:
        return msg("registration.used", calls=skill_calls)
    return msg("registration.ok")


def registration(denials: tuple[str, ...], skill_calls: int) -> str:
    return english(registration_message(denials, skill_calls))


class ForgeProgress:
    def __init__(self, progress: ProgressSink) -> None:
        self._progress = progress
        self.phases: list[str] = []
        self.tools: dict[str, int] = {}
        self._last = -1

    def __call__(self, event: EngineEvent) -> None:
        if isinstance(event, ToolCall):
            self.tools[event.name] = self.tools.get(event.name, 0) + 1
            return
        if isinstance(event, AssistantText):
            for phase in phase_markers(event.text):
                if phase not in FORGE_PHASES:
                    continue
                order = FORGE_PHASES.index(phase)
                if order <= self._last:
                    continue
                self._last = order
                self.phases.append(phase)
                self._progress.publish(note(Status.INFO, msg("forge.phase", phase=phase)))

    @property
    def tool_calls(self) -> int:
        return sum(self.tools.values())


class ForgeStage:
    def __init__(
        self,
        kit: ForgeKit,
        launcher_factory: Callable[[], EngineLauncher | None],
        progress: ProgressSink,
        root: str,
        workspace: Workspace,
        max_budget_usd: float = 0.0,
        templates: MandateTemplates | None = None,
    ) -> None:
        self._workspace = workspace
        self._kit = kit
        self._launcher_factory = launcher_factory
        self._progress = progress
        self._root = root
        self._budget = max_budget_usd
        self._templates = templates

    def _keep(self, context: InitContext) -> StageResult:
        summary = self._kit.install(dry_run=context.dry_run, missing_only=True)
        pending = self._missing_template()
        if context.dry_run:
            context.planned.append(msg("plan.forge_kept"))
            context.planned.extend(msg("plan.install", path=path) for path in summary.written)
            if pending is not None:
                context.planned.append(pending.planned)
            return stage(Status.INFO, "stage.planned")
        written = len(summary.written)
        if pending is not None and self._write_template(pending):
            written += 1
        context.forge_line = english(msg("stage.forge_kept"))
        if not written:
            return stage(Status.SKIP, "stage.forge_kept")
        return stage(Status.OK, "stage.forge_restored", files=file_count(written))

    def _missing_template(self) -> PendingTemplate | None:
        templates = self._templates
        if templates is None:
            return None
        try:
            if templates.status().state is not TemplateState.RESTORABLE:
                return None
            return templates.load()[1]
        except (CuantaError, OSError):
            return None

    def _write_template(self, pending: PendingTemplate) -> bool:
        templates = self._templates
        if templates is None:
            return False
        outcome = templates.keep(pending)
        if outcome is None:
            return False
        self._progress.publish(outcome)
        return outcome.status is Status.INFO

    def __call__(self, context: InitContext) -> StageResult:
        if context.keeps_forge:
            return self._keep(context)
        if context.dry_run:
            summary = self._kit.install(dry_run=True)
            context.planned.extend(msg("plan.install", path=path) for path in summary.written)
            context.planned.extend(msg("plan.suggest", path=path) for path in summary.new_files)
            context.planned.append(msg("plan.forge_run"))
            return stage(Status.INFO, "stage.planned")
        launcher = self._launcher_factory()
        if launcher is None:
            context.forge_line = "skipped: claude not found"
            return stage(Status.SKIP, "stage.claude_missing")
        missing = launcher.engine.missing_flags()
        if missing:
            raise NotAvailable(
                f"claude lacks flags cuanta needs: {', '.join(missing)}",
                "upgrade Claude Code, then cuanta init resumes here",
            )
        summary = self._kit.install(dry_run=False)
        context.new_files.extend(self._outside(summary.new_files))
        rulebook = self._workspace.read_text(RULEBOOK)
        prompt = forge_prompt(self._kit.init_prompt(), context.detection)
        watcher = ForgeProgress(self._progress)
        spec = LaunchSpec(
            kind="init",
            prompt=prompt,
            cwd=self._root,
            allowed_tools=forge_allowed_tools(context.graph_mode),
            disallowed_tools=FORGE_DISALLOWED_TOOLS,
            scope="refresh" if context.refresh else "init",
            max_budget_usd=self._budget,
        )
        launch = launcher.launch(spec, watcher)
        if launch.unreadable > 0:
            self._progress.publish(note(Status.WARN, unreadable_note(launch.unreadable)))
        outcome = launch.outcome
        context.run_id = launch.run.id
        context.cost_usd = launch.run.cost_usd
        context.forge_ran = True
        _, kept = promote_staged(self._workspace)
        context.new_files.extend(kept)
        report_rulebook(self._workspace, self._progress, rulebook)
        denials = outcome.result.denials if outcome.result is not None else ()
        context.denials = list(denials)
        skill_calls = watcher.tools.get("Skill", 0)
        context.registration = registration(denials, skill_calls)
        context.registration_message = registration_message(denials, skill_calls)
        if "Skill" in denials:
            self._progress.publish(note(Status.WARN, msg("registration.deferred")))
        turns = outcome.result.num_turns if outcome.result is not None else 0
        context.forge_line = (
            f"{launch.run.status} · {dollars(launch.run.cost_usd)} · {turns} turns · "
            f"{watcher.tool_calls} tool calls · phases {', '.join(watcher.phases) or '-'}"
        )
        if not outcome.ok:
            detail = outcome.stderr_tail or (
                outcome.result.subtype if outcome.result else "no result"
            )
            return stage(Status.FAIL, "stage.claude_failed", code=outcome.exit_code, detail=detail)
        return stage(Status.OK, "stage.run", run=launch.run.id)

    def _outside(self, paths: Sequence[str]) -> list[str]:
        root = self._workspace.root
        return [path for path in paths if not Path(path).is_relative_to(root)]


def _gather(workspace: Workspace) -> tuple[ForgeTree, dict[str, str]]:
    texts: dict[str, str] = {}
    for path in ("CLAUDE.md", "AGENTS_GUIDE.md", "HISTORIAS.md", *FORGE_DOCS):
        text = workspace.read_text(path)
        if text is not None:
            texts[path] = text
    docs_names = workspace.list_names("docs")
    for name in docs_names:
        if name.startswith("GROUND_TRUTH") and name.endswith(".md"):
            text = workspace.read_text(f"docs/{name}")
            if text is not None:
                texts[f"docs/{name}"] = text
    agent_names = tuple(
        name
        for name in workspace.list_names(".claude/agents")
        if name.endswith(".md") and not name.endswith(".new.md")
    )
    agent_texts = {
        f".claude/agents/{name}": workspace.read_text(f".claude/agents/{name}") or ""
        for name in agent_names
    }
    scan = workspace.scan(frozenset(), collect_files=False)
    per_directory = {path: workspace.read_text(path) or "" for path in scan.nested_claude_md}
    state_text = workspace.read_text(".claude/forge-state.json") or "{}"
    try:
        state = json.loads(state_text)
    except ValueError:
        state = {}
    phases = state.get("phases_completed") if isinstance(state, dict) else None
    graph_mode = state.get("graph_mode") if isinstance(state, dict) else ""
    tree = ForgeTree(
        texts=texts,
        agent_names=agent_names,
        docs_names=docs_names,
        per_directory=per_directory,
        graph_wired=graph_mode in {"cli", "mcp"},
        phases_completed=tuple(phases) if isinstance(phases, list) else (),
        gateway=workspace.is_dir(".cuanta"),
        gateway_texts={
            path: text for path in GATEWAY_FILES if (text := workspace.read_text(path)) is not None
        },
        suggested_texts={
            path: text
            for path in GATEWAY_FILES
            if (text := workspace.read_text(suggested_path(path))) is not None
        },
    )
    placeholder_texts = {path: text for path, text in texts.items() if path != "CLAUDE.md"}
    placeholder_texts.update(agent_texts)
    return tree, placeholder_texts


def verify_status(findings: Sequence[Finding]) -> Status:
    if not passed(findings):
        return Status.FAIL
    if any(finding.status is Status.WARN for finding in findings):
        return Status.WARN
    return Status.OK


class VerifyStage:
    def __init__(
        self, workspace: Workspace, ledger_factory: Callable[[], Ledger], clock: Clock
    ) -> None:
        self._workspace = workspace
        self._ledger_factory = ledger_factory
        self._clock = clock

    def baselines(self, run_id: str, created_at: str) -> list[Baseline]:
        items: list[Baseline] = []
        workspace = self._workspace
        candidates = [("rulebook", "CLAUDE.md")]
        candidates.extend(
            ("agent", f".claude/agents/{name}")
            for name in workspace.list_names(".claude/agents")
            if name.endswith(".md") and not name.endswith(".new.md")
        )
        skill = ".claude/skills/agent-system-init/SKILL.md"
        candidates.append(("skill", skill))
        for kind, path in candidates:
            if not workspace.exists(path):
                continue
            size = workspace.size_bytes(path)
            items.append(
                Baseline(
                    run_id,
                    kind,
                    path.rsplit("/", 1)[-1],
                    path,
                    size,
                    estimated_tokens(size),
                    created_at,
                )
            )
        return items

    def __call__(self, context: InitContext) -> StageResult:
        workspace = self._workspace
        if context.dry_run:
            context.planned.extend(
                msg("plan.move", path=path, target=suggested_path(original_of(path)))
                for path in siblings(workspace)
            )
            context.planned.append(msg("plan.verify"))
            return stage(Status.INFO, "stage.planned")
        notes = self._suggestions(context)
        if (
            not context.forge_ran
            and not context.refresh
            and not workspace.exists("AGENTS_GUIDE.md")
        ):
            context.verify_lines.extend(notes)
            context.verify_lines.append(Finding(Status.SKIP, msg("verify.nothing")))
            self._store(context)
            return stage(Status.SKIP, "stage.forge_skipped")
        tree, placeholder_texts = _gather(workspace)
        findings = [*verify_tree(tree, placeholder_texts), *notes]
        context.verify_lines.extend(findings)
        context.verify_ok = passed(findings)
        stored = self._store(context)
        checks = checked(findings)
        clean = sum(1 for finding in checks if finding.status is Status.OK)
        return stage(
            verify_status(findings),
            "stage.verified",
            clean=clean,
            total=len(checks),
            stored=stored,
        )

    def _suggestions(self, context: InitContext) -> list[Finding]:
        workspace = self._workspace
        moved = relocate(workspace)
        suggestions = listed(workspace, drop_adopted=True)
        elsewhere = [path for path in context.new_files if not is_suggested(path)]
        context.new_files[:] = [*elsewhere, *(item.path for item in suggestions)]
        context.suggestions[:] = suggestions
        return suggestion_findings(suggestions, moved, siblings(workspace), elsewhere)

    def _store(self, context: InitContext) -> int:
        items = self.baselines(context.run_id, self._clock.now_iso())
        if not items:
            return 0
        ledger = self._ledger_factory()
        try:
            ledger.add_baselines(items)
        finally:
            ledger.close()
        context.baseline_tokens = sum(item.tokens for item in items if item.kind == "rulebook")
        return len(items)
