from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.application.mandate import MandateReport, MandateService
    from cuanta.application.mandate_flow import MandateFlow, MandateOptions, Prepared
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Block, Document
    from cuanta.domain.mandate import MandateRequest


@dataclass(frozen=True, slots=True)
class MandateArgs:
    type: str = ""
    what: str = ""
    why: str = ""
    evidence: Path | None = None
    where: str = ""
    constraints: str = ""
    tests: str = ""
    out_of_scope: str = ""
    from_failure: bool = False
    engine: str = ""
    model: str = ""
    budget: float = 0.0
    hu: str = ""
    dry_run: bool = False
    parent: str = ""
    route: str = ""
    preset: str = ""
    role_models: tuple[str, ...] = ()
    keep_env_model: bool = True
    cross_engine: bool = False
    cross_budget: float = 1.0
    simple: bool = False
    session: str = ""
    depth: str = ""
    shape: str = ""


def mandate_command(
    ctx: typer.Context,
    type_: Annotated[
        str, typer.Option("--type", help="feature, bug, refactor, investigation.")
    ] = "",
    what: Annotated[str, typer.Option("--what", help="The change, concretely.")] = "",
    why: Annotated[str, typer.Option("--why", help="Evidence: the error, the log.")] = "",
    evidence: Annotated[
        Path | None, typer.Option("--evidence", help="File whose content becomes the evidence.")
    ] = None,
    where: Annotated[str, typer.Option("--where", help="File, module or area.")] = "",
    constraints: Annotated[str, typer.Option("--constraints", help="Invariants to keep.")] = "",
    tests: Annotated[str, typer.Option("--tests", help="The proof you expect.")] = "",
    out_of_scope: Annotated[
        str, typer.Option("--out-of-scope", help="What must not be touched.")
    ] = "",
    from_failure: Annotated[
        bool, typer.Option("--from-failure", help="Use the last red cuanta test as evidence.")
    ] = False,
    engine: Annotated[str, typer.Option("--engine", help="claude, codex or opencode.")] = "",
    model: Annotated[str, typer.Option("--model", help="Model for the run.")] = "",
    budget: Annotated[float, typer.Option("--max-budget-usd", help="Spend cap (claude).")] = 0.0,
    hu: Annotated[str, typer.Option("--hu", help="Tag the run with a story, e.g. HU-007.")] = "",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print prompt and command.")] = False,
    route: Annotated[str, typer.Option("--route", help="Model routing: auto, fixed or off.")] = "",
    preset: Annotated[str, typer.Option("--preset", help="save, balanced or best.")] = "",
    role_model: Annotated[
        list[str] | None, typer.Option("--role-model", help="Pin a role: senior=opus.")
    ] = None,
    override_env_model: Annotated[
        bool,
        typer.Option(
            "--override-env-model",
            help="Unset CLAUDE_CODE_SUBAGENT_MODEL(_FORCE) for this run so routes apply.",
        ),
    ] = False,
    cross_engine: Annotated[
        bool,
        typer.Option(
            "--cross-engine",
            help="Experimental: run each role separately on its own routed engine.",
        ),
    ] = False,
    cross_budget: Annotated[
        float,
        typer.Option("--cross-budget-usd", help="Spend cap for the whole cross-engine run."),
    ] = 1.0,
    simple: Annotated[
        bool,
        typer.Option(
            "--simple", help="Simple mode: one agent, no project knowledge (no Forge needed)."
        ),
    ] = False,
    session: Annotated[
        str,
        typer.Option("--session", help="lean (no user plugins, hooks or MCP servers) or full."),
    ] = "",
    depth: Annotated[
        str,
        typer.Option(
            "--depth", help="quick, normal or deep: tier cap, read budget and default spend cap."
        ),
    ] = "",
    shape: Annotated[
        str,
        typer.Option(
            "--shape",
            help="Investigations: single (one context, default) or pipeline (analyst subagent).",
        ),
    ] = "",
) -> None:
    args = MandateArgs(
        type=type_,
        what=what,
        why=why,
        evidence=evidence,
        where=where,
        constraints=constraints,
        tests=tests,
        out_of_scope=out_of_scope,
        from_failure=from_failure,
        engine=engine,
        model=model,
        budget=budget,
        hu=hu,
        dry_run=dry_run,
        route=route,
        preset=preset,
        role_models=tuple(role_model or ()),
        keep_env_model=not override_env_model,
        cross_engine=cross_engine,
        cross_budget=cross_budget,
        simple=simple,
        session=session,
        depth=depth,
        shape=shape,
    )
    execute(ctx, lambda cli: run_mandate(cli, args))


def _request(args: MandateArgs, why: str) -> "MandateRequest":
    from cuanta.domain.mandate import MandateRequest

    return MandateRequest(
        type=args.type,
        what=args.what,
        why=why,
        where=args.where,
        constraints=args.constraints,
        tests=args.tests,
        out_of_scope=args.out_of_scope,
    )


def _complete(session: Session, request: "MandateRequest") -> "MandateRequest":
    from cuanta.domain.errors import DomainFailure
    from cuanta.domain.mandate import LABELS, MandateRequest, MandateType, missing_fields

    missing = missing_fields(request)
    if missing and not session.interactive:
        names = ", ".join(f"--{name.replace('_', '-')}" for name in missing)
        raise DomainFailure(f"missing required fields: {names}", "pass them as flags")
    values = {
        "type": request.type,
        "what": request.what,
        "why": request.why,
        "where": request.where,
        "constraints": request.constraints,
        "tests": request.tests,
        "out_of_scope": request.out_of_scope,
    }
    for name in missing:
        values[name] = str(typer.prompt(LABELS[name].rstrip(":")))
    completed = MandateRequest(**values)
    allowed = {item.value for item in MandateType}
    if completed.type not in allowed:
        raise DomainFailure(
            f"unknown type {completed.type}", f"use one of {', '.join(sorted(allowed))}"
        )
    return completed


def _build_request(
    session: Session, service: "MandateService", args: MandateArgs
) -> "tuple[MandateRequest, int]":
    from dataclasses import replace

    why = args.why
    signatures = 0
    if args.evidence is not None:
        why = args.evidence.read_text(encoding="utf-8", errors="replace")
    effective = args
    if args.from_failure:
        evidence, signatures = service.from_failure()
        why = "\n".join((why, evidence)).strip() if why else evidence
        effective = replace(
            args,
            type=args.type or "bug",
            what=args.what or "make cuanta test green by fixing the failing signatures below",
        )
    return _complete(session, _request(effective, why)), signatures


def _options(args: MandateArgs) -> "MandateOptions":
    from cuanta.application.mandate_flow import MandateOptions
    from cuanta.application.route_apply import RouteOptions
    from cuanta.cli.commands.route import PRESETS, ROUTE_MODES, check_choice, parse_role_models
    from cuanta.domain.depth import DEPTHS
    from cuanta.domain.mandate import Shape
    from cuanta.domain.plugins import SESSIONS

    check_choice(args.route, ROUTE_MODES, "--route")
    check_choice(args.session, SESSIONS, "--session")
    check_choice(args.preset, PRESETS, "--preset")
    check_choice(args.depth, tuple(depth.value for depth in DEPTHS), "--depth")
    check_choice(args.shape, tuple(shape.value for shape in Shape), "--shape")
    return MandateOptions(
        engine=args.engine,
        model=args.model,
        budget_usd=args.budget,
        hu=args.hu,
        parent=args.parent,
        route=RouteOptions(
            mode=args.route,
            preset=args.preset,
            role_models=tuple(parse_role_models(list(args.role_models)).items()),
            keep_env_model=args.keep_env_model,
        ),
        simple=args.simple,
        session=args.session,
        depth=args.depth,
        shape=args.shape,
    )


def _prepare(
    session: Session, container: "Container", args: MandateArgs
) -> "tuple[MandateFlow, Prepared]":
    flow = container.mandate_flow(container.shared_ledger())
    request, signatures = _build_request(session, flow.service, args)
    return flow, flow.prepare(request, signatures, _options(args), preview=args.dry_run)


def run_mandate_core(
    session: Session, container: "Container", args: MandateArgs, verdict: bool = True
) -> "MandateReport":
    from cuanta.domain.progress import Status, StepFinished, StepStarted

    flow, prepared = _prepare(session, container, args)
    publish_team(session, prepared)
    label = f"pounce · {prepared.engine_name} · {prepared.composed.hint.option}"
    session.presenter.publish(StepStarted("mandate", label))
    report = flow.run(prepared, session.presenter, verdict=verdict)
    status = Status.OK if report.ok else Status.FAIL
    session.presenter.publish(StepFinished("mandate", status, f"{report.tool_calls} tool calls"))
    return report


def run_mandate(session: Session, args: MandateArgs) -> "Document":
    from cuanta.application.mandate_flow import display_command
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, Line, Verbatim
    from cuanta.domain.progress import Status

    container = Container.for_project(session.project)
    try:
        if args.dry_run:
            _, prepared = _prepare(session, container, args)
            composed = prepared.composed
            shown = display_command(composed.command, composed.prompt)
            team = tuple(Line(line, Status.INFO) for line in team_lines(prepared))
            return Document(
                blocks=(
                    Verbatim(composed.prompt),
                    *team,
                    Line(f"command: {shown}", Status.INFO),
                ),
                payload={
                    "dry_run": True,
                    "prompt": composed.prompt,
                    "command": list(composed.command),
                    "scope_hint": composed.hint.option,
                    "engine": prepared.engine_name,
                    "team": team_lines(prepared),
                    "agents_file": prepared.spec.agents_file or None,
                    "model": prepared.spec.model or None,
                },
            )
        if args.cross_engine:
            return run_cross_engine(session, container, args)
        report = run_mandate_core(session, container, args)
    finally:
        container.close()
    return _final(report)


def run_cross_engine(session: Session, container: "Container", args: MandateArgs) -> "Document":
    from cuanta.cli.commands.route import parse_role_models
    from cuanta.cli.document import Column, Document, Line, Table
    from cuanta.cli.fmt import usd
    from cuanta.domain.messages import english
    from cuanta.domain.progress import Note, Status

    flow = container.mandate_flow(container.shared_ledger())
    request, _ = _build_request(session, flow.service, args)
    roles = parse_role_models(list(args.role_models))
    plan, _ = container.plan_route(
        request.type, request.what, request.where, args.route, args.preset, roles
    )
    session.presenter.publish(Note(Status.WARN, "cross-engine pipeline (experimental)"))
    pipeline = container.cross_engine(container.shared_ledger(), args.cross_budget)
    report = pipeline.run(request, plan, session.presenter)
    rows = tuple(
        (
            step.role.value,
            step.engine,
            step.model,
            step.run_id,
            "ok" if step.ok else "failed",
            usd(step.cost_usd),
        )
        for step in report.steps
    )
    blocks: list[Block] = [
        Table(
            "cross-engine pipeline (experimental)",
            (
                Column("role"),
                Column("engine"),
                Column("model"),
                Column("run"),
                Column("status"),
                Column("cost", numeric=True),
            ),
            rows,
        ),
        Line(f"spent {usd(report.spent_usd)} of {usd(args.cross_budget)}"),
    ]
    if report.stopped is not None:
        blocks.append(Line(english(report.stopped), Status.WARN))
    payload: dict[str, object] = {
        "experimental": True,
        "ok": report.ok,
        "spent_usd": report.spent_usd,
        "stopped": english(report.stopped) if report.stopped else None,
        "steps": [
            {
                "role": step.role.value,
                "engine": step.engine,
                "model": step.model,
                "run_id": step.run_id,
                "ok": step.ok,
                "cost_usd": step.cost_usd,
            }
            for step in report.steps
        ],
    }
    return Document(blocks=tuple(blocks), payload=payload, exit_code=0 if report.ok else 1)


def team_lines(prepared: "Prepared") -> list[str]:
    from cuanta.domain.messages import english

    applied = prepared.applied
    if applied is None or not applied.active:
        return ["team: routing off, the engine picks its default models"]
    lines: list[str] = []
    for route in applied.plan.routes:
        model = route.model.id if route.model else "engine default"
        tier = route.tier.value if route.tier else "-"
        lines.append(f"team · {route.role.value} → {model} ({tier}) · {english(route.reason)}")
    if applied.single:
        lines.append(f"team · single model for {applied.engine}: {applied.single}")
    if applied.env_override:
        verb = "unset for this run" if applied.unset else "kept: it overrides the planned models"
        lines.append(f"team · {applied.env_override} is set ({verb})")
    return lines


def publish_team(session: Session, prepared: "Prepared") -> None:
    from cuanta.domain.progress import Note, Status

    applied = prepared.applied
    warn = applied is not None and applied.env_override and not applied.unset
    for line in team_lines(prepared):
        status = Status.WARN if warn and "is set" in line else Status.INFO
        session.presenter.publish(Note(status, line))


def audit_rows(report: "MandateReport") -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    for row in report.audit:
        if row.status.value == "not_run":
            rows.append((f"route {row.agent}", f"– planned {row.planned} · {row.cause_text}"))
            continue
        mark = "✓" if row.ok else "✗"
        actual = ", ".join(row.actual) or "-"
        detail = f"{mark} planned {row.planned}, ran {actual}"
        rows.append((f"route {row.agent}", detail if row.ok else f"{detail} · {row.cause_text}"))
    return tuple(rows)


def _final(report: "MandateReport") -> "Document":
    from cuanta.application.mandate import report_payload
    from cuanta.cli.document import Document, Hint, KeyValues, MarkdownText, MascotBlock, Panel
    from cuanta.cli.fmt import compact, percent, usd
    from cuanta.domain.voice import Mood

    run = report.run
    agents = ", ".join(
        f"{name} {compact(tokens)}" for name, tokens in report.tokens_by_agent.items()
    )
    index = "n/a" if report.utilization is None else percent(report.utilization)
    rows = (
        ("run", run.id),
        ("status", run.status),
        ("scope hint", report.hint.option),
        ("files changed", str(len(report.changed_files))),
        ("tests", report.tests),
        ("tokens by agent", agents or "-"),
        ("cost", usd(run.cost_usd)),
        ("utilization", f"{index} (heuristic v1)"),
        *audit_rows(report),
    )
    panel = Panel(
        "purr" if report.ok else "hiss",
        (
            MascotBlock(Mood.HAPPY if report.ok else Mood.ALARMED),
            KeyValues(rows),
            Hint(f"cuanta spectrum {run.id}"),
        ),
    )
    blocks: list[Block] = [panel]
    if report.text.strip():
        blocks.append(MarkdownText(report.text))
    if report.report_path:
        blocks.append(Hint(f"report saved: {report.report_path} · cuanta runs show {run.id}"))
    payload = {**report_payload(report), "report_text": report.text}
    return Document(blocks=tuple(blocks), payload=payload, exit_code=0 if report.ok else 1)
