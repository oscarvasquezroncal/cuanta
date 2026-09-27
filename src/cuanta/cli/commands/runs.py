from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute, global_options

if TYPE_CHECKING:
    from cuanta.application.results import ResultView
    from cuanta.application.trials import Trial, TrialSummary
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Block, Document, Table
    from cuanta.domain.handoff import Handoff, Workflow
    from cuanta.domain.ledger import Run
    from cuanta.domain.shells import Shell

runs_app = typer.Typer(
    help="Runs cuanta launched, with their stored reports.", no_args_is_help=True
)

LIST_LIMIT = 30


@runs_app.command("list", help="Recent runs, newest first.")
def list_command(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option("--limit", min=1, help="How many runs.")] = LIST_LIMIT,
) -> None:
    execute(ctx, lambda session: _list(session, limit))


@runs_app.command("show", help="A run's report and consumption.")
def show_command(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Run id or a unique prefix.")],
    markdown: Annotated[
        bool, typer.Option("--markdown", help="Print the human run report as Markdown.")
    ] = False,
) -> None:
    execute(ctx, lambda session: _show(session, run_id, markdown))


@runs_app.command("open", help="Open the app on a run's Result screen.")
def open_command(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Run id or a unique prefix.")],
) -> None:
    from cuanta.cli.commands.ui import launch
    from cuanta.domain.errors import CuantaError

    options = global_options(ctx)
    try:
        resolved = _resolve(options.project, run_id)
    except CuantaError as error:
        typer.echo(f"cuanta: {error}", err=True)
        if error.hint:
            typer.echo(f"  {error.hint}", err=True)
        raise typer.Exit(int(error.exit_code)) from error
    launch(options, open_run=resolved)


@runs_app.command("apply", help="Apply an isolated-copy run to the project after a drift check.")
def apply_command(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Run id or a unique prefix.")],
) -> None:
    execute(ctx, lambda session: _apply(session, run_id))


@runs_app.command("discard", help="Reject an isolated-copy run; the project is not touched.")
def discard_command(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Run id or a unique prefix.")],
) -> None:
    execute(ctx, lambda session: _discard(session, run_id))


@runs_app.command("accept", help="Mark a run as accepted: its change or report was useful.")
def accept_command(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Run id or a unique prefix.")],
) -> None:
    execute(ctx, lambda session: _decide(session, run_id, True, ""))


@runs_app.command("reject", help="Mark a run as rejected; an isolated-copy run is discarded.")
def reject_command(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Run id or a unique prefix.")],
    reason: Annotated[str, typer.Option("--reason", help="Why, kept with the run.")] = "",
) -> None:
    execute(ctx, lambda session: _decide(session, run_id, False, reason))


@runs_app.command("branch", help="Suggested branch or commit and the git commands to run.")
def branch_command(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Run id or a unique prefix.")],
    shell: Annotated[str, typer.Option("--shell", help="cmd, pwsh, bash, zsh or fish.")] = "",
    workflow: Annotated[
        str, typer.Option("--workflow", help="branches or trunk; defaults to git.workflow.")
    ] = "",
) -> None:
    execute(ctx, lambda session: _branch(session, run_id, shell, workflow))


def _resolve(project: object, run_id: str) -> str:
    from pathlib import Path

    from cuanta.bootstrap import Container

    root = project if isinstance(project, Path) else Path.cwd()
    container = Container.for_project(root.resolve())
    try:
        return container.resolve_run(run_id)
    finally:
        container.close()


def _list(session: Session, limit: int) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Column, Document, Hint, Table
    from cuanta.cli.fmt import usd

    container = Container.for_project(session.project)
    runs = container.runs_query().run(limit)
    stored = container.stored_reports()
    rows = tuple(
        (
            run.id,
            run.kind,
            run.engine or "-",
            run.status,
            _outcome(run),
            usd(run.cost_usd, run.cost_source),
            run.started_at[5:16].replace("T", " ") or "-",
            "yes" if run.id in stored else "-",
        )
        for run in runs
    )

    def fitted(index: int, header: str) -> int:
        return max(len(header), *(len(row[index]) for row in rows)) if rows else 0

    table = Table(
        "runs",
        (
            Column("id", min_width=fitted(0, "id")),
            Column("kind", min_width=fitted(1, "kind")),
            Column("engine"),
            Column("status", min_width=fitted(3, "status")),
            Column("outcome", min_width=fitted(4, "outcome")),
            Column("cost", numeric=True),
            Column("started"),
            Column("report"),
        ),
        rows,
    )
    blocks: list[Block] = [table]
    if not runs:
        blocks.append(Hint("no runs yet · run cuanta mandate or cuanta test"))
    else:
        blocks.append(Hint("cuanta runs show <id> · cuanta runs open <id>"))
    payload: dict[str, object] = {
        "runs": [
            {
                "id": run.id,
                "kind": run.kind,
                "engine": run.engine,
                "status": run.status,
                "outcome": _outcome_value(run),
                "cost_usd": run.cost_usd,
                "cost_source": run.cost_source,
                "started_at": run.started_at,
                "report": run.id in stored,
            }
            for run in runs
        ]
    }
    return Document(blocks=tuple(blocks), payload=payload)


def _outcome(run: "Run") -> str:
    from cuanta.domain.outcomes import is_attempt

    if not is_attempt(run):
        return "-"
    return run.outcome or "pending"


def _outcome_value(run: "Run") -> str | None:
    from cuanta.domain.outcomes import is_attempt

    if run.outcome:
        return run.outcome
    return "pending" if is_attempt(run) else None


def _money(value: float | None) -> str:
    from cuanta.cli.fmt import usd

    return usd(value)


def estimate_text(run: "Run") -> str:
    if not run.estimate_source:
        return "not recorded"
    if run.estimate_low is None:
        return "none shown"
    low, high = _money(run.estimate_low), _money(run.estimate_high)
    span = low if run.estimate_high in (None, run.estimate_low) else f"{low}–{high}"
    if run.estimate_source == "history":
        return f"{span} · from history (n={run.estimate_samples})"
    return f"{span} · {run.estimate_source}"


def error_text(error: float | None) -> str:
    if error is None:
        return "n/a"
    return "in range" if error == 0 else f"{error:+.0%}"


def _load(container: "Container", run_id: str) -> "ResultView":
    from cuanta.domain.errors import DomainFailure

    resolved = container.resolve_run(run_id)
    view = container.result_query(container.shared_ledger()).load(resolved)
    if view is None:
        raise DomainFailure(f"run {run_id} is not in the ledger", "list them: cuanta runs list")
    return view


def _show(session: Session, run_id: str, markdown: bool) -> "Document":
    from dataclasses import asdict

    from cuanta.application.results import run_markdown
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, Hint, KeyValues, Line, MarkdownText, Verbatim
    from cuanta.cli.fmt import usd
    from cuanta.domain.engine import TURN_LIMIT_SUBTYPE
    from cuanta.domain.messages import english, msg
    from cuanta.domain.overhead import overhead_messages, overhead_payload

    container = Container.for_project(session.project)
    try:
        view = _load(container, run_id)
    finally:
        container.close()
    run = view.run
    payload: dict[str, object] = {
        "run_id": run.id,
        "kind": run.kind,
        "type": view.task_type,
        "simple": view.simple,
        "shape": "unknown" if not view.shape_known else "single" if view.single else "pipeline",
        "max_turns": run.max_turns,
        "turns": run.turns,
        "end_reason": run.end_reason,
        "status": run.status,
        "completion": view.completion or None,
        "verification": [
            {
                "role": item.role,
                "attempt": item.attempt,
                "passed": item.passed,
                "total": item.total,
                "seconds": item.seconds,
                "model_cost_usd": 0.0,
            }
            for item in view.verification
        ],
        "engine": run.engine,
        "model": run.model,
        "cost_usd": run.cost_usd,
        "cost_source": run.cost_source,
        "actual_usd": view.actual_usd,
        "estimate": {
            "source": run.estimate_source or None,
            "low_usd": run.estimate_low,
            "high_usd": run.estimate_high,
            "samples": run.estimate_samples,
            "factor": view.estimate_factor,
            "error": view.estimate_error,
        },
        "cap_usd": run.cap_usd,
        "outcome": _outcome_value(run),
        "outcome_at": run.outcome_at or None,
        "outcome_reason": run.outcome_reason or None,
        "duration_s": view.duration_s,
        "estimate_factor": view.estimate_factor,
        "turn_count_includes_terminal": view.terminal_turn,
        "changed_files": list(view.changed_files),
        "report": view.text,
        "report_path": view.report_path or None,
        "sections": [section.key for section in view.sections],
        "overhead": overhead_payload(view.overhead) if view.overhead is not None else None,
        "trial": _trial_payload(view.trial),
        "read_efficiency": {
            **asdict(view.read_efficiency),
            "read_count": view.read_efficiency.read_count,
            "useful_count": view.read_efficiency.useful_count,
        },
    }
    if markdown:
        text = run_markdown(view)
        return Document(
            blocks=(Verbatim(text.rstrip("\n")),), payload={**payload, "markdown": text}
        )
    duration = "n/a" if view.duration_s is None else f"{view.duration_s:,.0f} s"
    turn_rows: tuple[tuple[str, str], ...] = ()
    if run.max_turns > 0 or run.end_reason == TURN_LIMIT_SUBTYPE:
        count = f"{run.turns}/{run.max_turns}" if run.max_turns > 0 else str(run.turns)
        cut = " · cut by turn limit" if run.end_reason == TURN_LIMIT_SUBTYPE else ""
        turn_rows = (("turns", f"{count}{cut}"),)
        if view.terminal_turn:
            turn_rows += (
                ("turn counting", "raw engine count includes terminal turn-limit result"),
            )
    rows = (
        ("run", run.id),
        ("type", view.task_type or run.kind),
        (
            "mode",
            "unknown shape"
            if not view.shape_known
            else "simple (one agent, no project knowledge)"
            if view.simple
            else "single context"
            if view.single
            else "pipeline",
        ),
        ("status", run.status),
        *(
            (("completion", english(msg(f"completion.{view.completion}"))),)
            if view.completion
            else ()
        ),
        ("engine", f"{run.engine} · {run.model or 'default model'}"),
        ("duration", duration),
        ("cost", usd(run.cost_usd, run.cost_source)),
        *_decision_rows(view),
        *turn_rows,
        ("files changed", str(len(view.changed_files))),
        *(
            (
                f"verify {item.role} #{item.attempt}",
                f"{item.passed}/{item.total} passed in {item.seconds:.1f} s, $0 model spend",
            )
            for item in view.verification
        ),
        *_trial_rows(view.trial),
    )
    blocks: list[Block] = [KeyValues(rows)]
    if view.trial is not None and view.trial.trial.guard_tripped:
        blocks.append(_no_handoff(view.trial))
    if view.overhead is not None:
        blocks.extend(Line(english(line)) for line in overhead_messages(view.overhead))
    if view.text.strip():
        blocks.append(MarkdownText(view.text))
    else:
        blocks.append(Hint("no stored report for this run"))
    if view.report_path:
        blocks.append(Hint(f"report: {view.report_path} · cuanta runs open {run.id}"))
    return Document(blocks=tuple(blocks), payload=payload)


def _decision_rows(view: "ResultView") -> tuple[tuple[str, str], ...]:
    from cuanta.cli.fmt import usd
    from cuanta.domain.outcomes import is_attempt

    run = view.run
    if not is_attempt(run):
        return ()
    rows: list[tuple[str, str]] = []
    if run.kind == "cross" and not run.parent_id and view.actual_usd != run.cost_usd:
        source = "estimated" if view.actual_estimated else ""
        rows.append(("pipeline cost", usd(view.actual_usd, source)))
    rows.append(("estimate", estimate_text(run)))
    if run.estimate_low is not None:
        rows.append(("estimate error", error_text(view.estimate_error)))
    if run.cap_usd is not None:
        rows.append(("cap", "none" if run.cap_usd <= 0 else usd(run.cap_usd)))
    outcome = run.outcome or "pending"
    when = f" · {run.outcome_at[:16].replace('T', ' ')}" if run.outcome_at else ""
    why = f" · {run.outcome_reason}" if run.outcome_reason else ""
    rows.append(("outcome", f"{outcome}{when}{why}"))
    return tuple(rows)


COMMAND_LABELS = (
    ("cd ", "project"),
    ("Set-Location ", "project"),
    ("git switch ", "branch"),
    ("cuanta runs apply ", "apply"),
    ("git --literal-pathspecs add ", "stage"),
    ("git --literal-pathspecs commit ", "commit"),
)


def _label(command: str) -> str:
    return next((label for prefix, label in COMMAND_LABELS if command.startswith(prefix)), "")


def _changes(trial: "Trial") -> "Table":
    from cuanta.cli.document import Column, Table

    return Table(
        "changes",
        (Column("change"), Column("file"), Column("+", numeric=True), Column("−", numeric=True)),
        tuple(
            (
                change.kind.value,
                change.path,
                "bin" if change.binary else str(change.added),
                "bin" if change.binary else str(change.removed),
            )
            for change in trial.changes
        ),
    )


def _handoff_blocks(handoff: "Handoff | None") -> "list[Block]":
    from cuanta.cli.document import Commands, KeyValues, Line
    from cuanta.domain.progress import Status

    if handoff is None:
        return []
    rows = [("commit", handoff.subject)]
    if handoff.branch:
        rows.append(("branch", handoff.branch))
    elif handoff.current_branch:
        rows.append(("branch", f"{handoff.current_branch} (current)"))
    items = tuple((_label(command), command) for command in handoff.commands)
    blocks: list[Block] = [
        KeyValues(tuple(rows)),
        Commands(f"git hand-off ({handoff.workflow.value})", items),
    ]
    if handoff.uncommitted:
        shown = ", ".join(handoff.uncommitted[:5])
        blocks.append(
            Line(
                "these files already had your own uncommitted changes, and the commit will "
                f"include them: {shown}",
                Status.WARN,
            )
        )
    if handoff.untracked:
        shown = ", ".join(handoff.untracked[:5])
        blocks.append(
            Line(
                "these files were not tracked by git before the run, and the commit adds them "
                f"whole: {shown}",
                Status.WARN,
            )
        )
    return blocks


def _handoff_payload(handoff: "Handoff | None", shell: str) -> dict[str, object] | None:
    if handoff is None:
        return None
    return {
        "workflow": handoff.workflow.value,
        "kind": handoff.kind,
        "subject": handoff.subject,
        "branch": handoff.branch or None,
        "current_branch": handoff.current_branch or None,
        "shell": shell,
        "commands": list(handoff.commands),
        "one_line": handoff.chained,
        "uncommitted": list(handoff.uncommitted),
        "untracked": list(handoff.untracked),
    }


def _shell(container: "Container", name: str) -> "Shell":
    from cuanta.domain.errors import DomainFailure
    from cuanta.domain.shells import Shell, shell_from_name

    if not name:
        return container.shell()
    chosen = shell_from_name(name)
    if chosen is Shell.UNKNOWN:
        raise DomainFailure(f"unknown --shell {name}", "use cmd, pwsh, bash, zsh or fish")
    return chosen


def _workflow(container: "Container", name: str) -> "Workflow":
    from cuanta.cli.commands.route import check_choice
    from cuanta.domain.handoff import WORKFLOWS, parse_workflow

    check_choice(name, WORKFLOWS, "--workflow")
    return parse_workflow(name or container.config.git_workflow)


def _apply(session: Session, run_id: str) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, Hint, Line
    from cuanta.domain.errors import DomainFailure
    from cuanta.domain.progress import Status

    container = Container.for_project(session.project)
    try:
        resolved = container.resolve_run(run_id)
        store = container.trial_store(container.shared_ledger())
        summary = store.summary(resolved)
        if summary is None:
            raise DomainFailure(
                f"run {resolved} has no isolated-copy changes",
                "only runs launched with --sandbox can be applied",
            )
        table = _changes(summary.trial)
        if not session.options.yes:
            if not session.interactive:
                return Document(
                    blocks=(table, Hint("nothing written · add --yes to apply")),
                    payload={"applied": False, "run_id": resolved},
                )
            session.presenter.render(Document(blocks=(table,)))
            if not typer.confirm(f"Apply these changes to {session.project}?", default=False):
                return Document(
                    blocks=(Hint("nothing written"),),
                    payload={"applied": False, "run_id": resolved},
                )
        trial = store.apply(resolved)
        shell = container.shell()
        handoff = store.handoff(resolved, _workflow(container, ""), shell)
    finally:
        container.close()
    blocks: list[Block] = [
        Line(f"applied {len(trial.changes)} files from run {resolved}", Status.OK),
        *_handoff_blocks(handoff),
    ]
    return Document(
        blocks=tuple(blocks),
        payload={
            "applied": True,
            "run_id": resolved,
            "files": list(trial.paths),
            "handoff": _handoff_payload(handoff, shell.value),
        },
    )


def _decide(session: Session, run_id: str, accept: bool, reason: str) -> "Document":
    from pathlib import Path

    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, Hint, Line
    from cuanta.domain.progress import Status

    container = Container.for_project(session.project)
    try:
        ledger = container.shared_ledger()
        resolved = container.resolve_run(run_id)
        outcomes = container.run_outcomes(ledger)
        change = outcomes.accept(resolved) if accept else outcomes.reject(resolved, reason)
        summary = container.trial_store(ledger).summary(change.run_id)
    finally:
        container.close()
    blocks: list[Block] = [Line(f"run {change.run_id} {change.outcome}", Status.OK)]
    if change.run_id != resolved:
        blocks.append(Hint(f"{resolved} is a role of the cross-engine run {change.run_id}"))
    trial = summary.trial if summary is not None else None
    if accept and summary is not None and trial is not None and trial.applicable:
        if summary.applied_at:
            blocks.append(Hint("the change was already applied to the project"))
        elif summary.can_apply:
            blocks.append(
                Hint(f"the change is not in the project yet: cuanta runs apply {change.run_id}")
            )
        else:
            moved = ", ".join(summary.drift[:5]) or "files changed since the copy"
            blocks.append(Hint(f"nothing was applied; the project changed since the copy: {moved}"))
    kept = trial.copy_root if not accept and trial is not None and trial.kept else ""
    if kept and Path(kept).exists():
        blocks.append(Hint(f"the isolated copy is still at {kept}; delete it when done"))
    return Document(
        blocks=tuple(blocks),
        payload={
            "run_id": change.run_id,
            "outcome": change.outcome,
            "outcome_at": change.at,
            "reason": change.reason or None,
            "kept_copy": kept or None,
        },
    )


def _discard(session: Session, run_id: str) -> "Document":
    from pathlib import Path

    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, Hint, Line
    from cuanta.domain.progress import Status

    container = Container.for_project(session.project)
    try:
        ledger = container.shared_ledger()
        resolved = container.run_outcomes(ledger).pending(container.resolve_run(run_id)).id
        trial = container.trial_store(ledger).discard(resolved)
    finally:
        container.close()
    blocks: list[Block] = [
        Line(f"run {resolved} rejected · the project was not touched", Status.OK)
    ]
    kept = trial.copy_root if trial is not None and trial.kept else ""
    if kept and Path(kept).exists():
        blocks.append(Hint(f"the isolated copy is still at {kept}; delete it when done"))
    return Document(
        blocks=tuple(blocks),
        payload={"rejected": True, "run_id": resolved, "kept_copy": kept or None},
    )


def _branch(session: Session, run_id: str, shell_name: str, workflow_name: str) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document

    container = Container.for_project(session.project)
    try:
        resolved = container.resolve_run(run_id)
        shell = _shell(container, shell_name)
        workflow = _workflow(container, workflow_name)
        store = container.trial_store(container.shared_ledger())
        handoff = store.handoff(resolved, workflow, shell)
        summary = store.summary(resolved)
    finally:
        container.close()
    blocks = _handoff_blocks(handoff)
    tripped = summary is not None and summary.trial.guard_tripped
    if handoff is None:
        blocks.append(_no_handoff(summary))
    return Document(
        blocks=tuple(blocks),
        payload={
            "run_id": resolved,
            "handoff": _handoff_payload(handoff, shell.value),
            "trial": _trial_payload(summary),
        },
        exit_code=1 if tripped else 0,
    )


def _trial_rows(summary: "TrialSummary | None") -> tuple[tuple[str, str], ...]:
    if summary is None:
        return ()
    trial = summary.trial
    state = (
        f"applied {summary.applied_at[:16]}"
        if summary.applied_at
        else summary.outcome or "waiting for apply or discard"
    )
    rows = [
        ("isolated copy", f"{len(trial.changes)} files, +{trial.added} −{trial.removed}"),
        ("apply state", state),
    ]
    if summary.drift:
        rows.append(("changed since the copy", ", ".join(summary.drift[:5])))
    return tuple(rows)


def _no_handoff(summary: "TrialSummary | None") -> "Block":
    from cuanta.application.trials import NPM_HINT, STATE_HINT
    from cuanta.cli.document import Hint, Line
    from cuanta.domain.outcomes import REJECTED
    from cuanta.domain.progress import Status

    if summary is None:
        return Hint("this run did not work in an isolated copy")
    trial = summary.trial
    if trial.state_changed_count:
        return Line(
            "cuanta's own files in the project changed during the run: "
            f"{', '.join(trial.state_changed[:5])}; {STATE_HINT}",
            Status.FAIL,
        )
    if trial.guard_tripped:
        return Line(
            f"node_modules in the project changed during the run "
            f"({trial.dependencies_changed_count} files): {NPM_HINT}",
            Status.FAIL,
        )
    if summary.outcome == REJECTED:
        return Hint("this run was discarded; there is nothing to hand off")
    if trial.base_missing:
        return Line("files changed in the project while the run worked", Status.WARN)
    return Hint("this run has no changes to hand off")


def _trial_payload(summary: "TrialSummary | None") -> dict[str, object] | None:
    from cuanta.application.trials import trial_payload

    if summary is None:
        return None
    return {
        **trial_payload(summary.trial),
        "outcome": summary.outcome or None,
        "outcome_at": summary.outcome_at or None,
        "applied_at": summary.applied_at or None,
        "drift": list(summary.drift),
        "guard_tripped": summary.trial.guard_tripped,
    }
