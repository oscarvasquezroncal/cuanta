from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.application.cross_engine import CrossReport
    from cuanta.application.mandate import MandateReport, MandateService
    from cuanta.application.mandate_flow import MandateFlow, MandateOptions, Prepared
    from cuanta.application.routing import RoutePlan
    from cuanta.application.sandbox import SandboxResult
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Block, Document
    from cuanta.domain.estimates import RunEstimate
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
    mix: str = ""
    simple: bool = False
    session: str = ""
    depth: str = ""
    max_turns: int = 0
    shape: str = ""
    sandbox: bool = False
    keep: bool = False


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
    budget: Annotated[
        float,
        typer.Option("--max-budget-usd", help="Spend cap; enforcement depends on the engine."),
    ] = 0.0,
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
    mix: Annotated[
        str,
        typer.Option(
            "--mix",
            help=(
                "Cross-engine team preset: claude-only, claude-plans-codex-writes "
                "or codex-plans-claude-writes."
            ),
        ),
    ] = "",
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
    max_turns: Annotated[
        int,
        typer.Option("--max-turns", help="Turn limit for claude runs; 0 uses the depth default."),
    ] = 0,
    shape: Annotated[
        str,
        typer.Option(
            "--shape",
            help="Investigations: single (one context, default) or pipeline (analyst subagent).",
        ),
    ] = "",
    sandbox: Annotated[
        bool,
        typer.Option(
            "--sandbox",
            help="Work in an isolated copy of the project; apply later with cuanta runs apply.",
        ),
    ] = False,
    keep: Annotated[
        bool, typer.Option("--keep", help="Keep the isolated copy after the run (--sandbox).")
    ] = False,
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
        mix=mix,
        simple=simple,
        session=session,
        depth=depth,
        max_turns=max_turns,
        shape=shape,
        sandbox=sandbox,
        keep=keep,
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
    from cuanta.domain.errors import DomainFailure
    from cuanta.domain.mandate import Shape
    from cuanta.domain.plugins import SESSIONS
    from cuanta.domain.routing import MIXES

    check_choice(args.route, ROUTE_MODES, "--route")
    check_choice(args.session, SESSIONS, "--session")
    check_choice(args.preset, PRESETS, "--preset")
    check_choice(args.depth, tuple(depth.value for depth in DEPTHS), "--depth")
    check_choice(args.shape, tuple(shape.value for shape in Shape), "--shape")
    if args.keep and not args.sandbox:
        raise DomainFailure("--keep only applies to --sandbox runs", "add --sandbox")
    check_choice(args.mix, MIXES, "--mix")
    if args.mix and not args.cross_engine:
        raise DomainFailure(
            "--mix sets engines per role in a cross-engine run", "add --cross-engine"
        )
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
        max_turns=args.max_turns,
        shape=args.shape,
        sandbox=args.sandbox,
        keep_copy=args.keep,
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
            copy_note = (
                (Line("isolated copy: not created in a dry run", Status.INFO),)
                if args.sandbox
                else ()
            )
            return Document(
                blocks=(
                    Verbatim(composed.prompt),
                    *team,
                    *copy_note,
                    Line(f"command: {shown}", Status.INFO),
                ),
                payload={
                    "dry_run": True,
                    "sandbox": args.sandbox,
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
        if args.sandbox:
            return run_sandbox(session, container, args)
        report = run_mandate_core(session, container, args)
    finally:
        container.close()
    return _final(report)


def run_sandbox(session: Session, container: "Container", args: MandateArgs) -> "Document":
    from cuanta.domain.progress import Status, StepFinished, StepStarted

    ledger = container.shared_ledger()
    flow = container.mandate_flow(ledger)
    request, signatures = _build_request(session, flow.service, args)

    def started(_: "MandateFlow", prepared: "Prepared") -> None:
        publish_team(session, prepared)
        label = f"pounce · {prepared.engine_name} · {prepared.composed.hint.option} · isolated copy"
        session.presenter.publish(StepStarted("mandate", label))

    result = container.run_sandboxed(
        ledger, request, signatures, _options(args), session.presenter, on_start=started
    )
    report = result.report
    if report is None:
        raise RuntimeError("sandbox run ended without a report")
    status = Status.OK if report.ok else Status.FAIL
    session.presenter.publish(StepFinished("mandate", status, f"{report.tool_calls} tool calls"))
    return _final(report, result)


def run_cross_engine(session: Session, container: "Container", args: MandateArgs) -> "Document":
    from cuanta.application.mandate_flow import resolve_max_turns
    from cuanta.cli.document import Document, Line
    from cuanta.domain.depth import parse_depth, profile
    from cuanta.domain.errors import DomainFailure
    from cuanta.domain.messages import english, msg
    from cuanta.domain.progress import Note, Status
    from cuanta.domain.routing import ENGINE_ORDER, parse_mix
    from cuanta.domain.team import mix_title

    flow = container.mandate_flow(container.shared_ledger())
    request, _ = _build_request(session, flow.service, args)
    options = _options(args)
    roles = dict(options.route.role_models)
    mix = parse_mix(args.mix) if args.mix else None
    plan, _ = container.plan_route(
        request.type,
        request.what,
        request.where,
        args.route,
        args.preset,
        roles,
        depth=options.depth,
        mix=mix,
    )
    issues = container.route_advisor(container.shared_ledger()).pin_issues(
        plan, roles, ENGINE_ORDER, cross=True
    )
    if issues:
        raise DomainFailure(
            english(msg("route.pins_rejected")), "; ".join(english(item) for item in issues)
        )
    session.presenter.publish(Note(Status.WARN, "cross-engine pipeline (experimental)"))
    if mix is not None:
        session.presenter.publish(Note(Status.INFO, english(mix_title(mix))))
    publish_cross_team(session, container, request, plan, options.depth, args.cross_budget)
    guess = container.run_estimate(plan, request.type, options.depth)
    session.presenter.publish(Note(Status.INFO, estimate_line(guess)))
    max_turns = resolve_max_turns(
        options, profile(parse_depth(options.depth), request.type), container.config.max_turns
    )
    isolated: SandboxResult | None = None
    if args.sandbox:
        isolated = container.run_sandboxed_cross(
            container.shared_ledger(),
            request,
            plan,
            session.presenter,
            args.cross_budget,
            max_turns,
            args.keep,
            options.depth,
        )
        if isolated.cross is None:
            raise RuntimeError("sandbox cross-engine run ended without a report")
        report = isolated.cross
    else:
        pipeline = container.cross_engine(
            container.shared_ledger(), args.cross_budget, max_turns, depth=options.depth
        )
        report = pipeline.run(request, plan, session.presenter)
    blocks = cross_blocks(report, args.cross_budget)
    if report.stopped is not None:
        blocks.append(Line(english(report.stopped), Status.WARN))
    if isolated is not None:
        blocks.extend(sandbox_blocks(isolated))
    payload = cross_payload(report)
    if isolated is not None:
        payload["sandbox"] = sandbox_payload(isolated)
    ok = report.ok and not guard_tripped(isolated)
    return Document(blocks=tuple(blocks), payload=payload, exit_code=0 if ok else 1)


def publish_cross_team(
    session: Session,
    container: "Container",
    request: "MandateRequest",
    plan: "RoutePlan",
    depth: str,
    budget: float,
) -> None:
    from cuanta.domain.messages import english, msg
    from cuanta.domain.progress import Note, Status
    from cuanta.domain.team import advice_message, mix_attempts, recommend_mix, team_cards

    shares = container.role_budget(plan, request.type, depth, budget)
    for card in team_cards(
        plan.routes,
        shares,
        budget,
        container.pipeline_index_tools,
        container.build_blocked(),
    ):
        session.presenter.publish(Note(Status.INFO, english(card.title)))
        for guarantee in card.guarantees:
            session.presenter.publish(Note(Status.INFO, f"  {english(guarantee.message)}"))
        session.presenter.publish(Note(Status.INFO, f"  {english(card.context)}"))
        for warning in card.warnings:
            text = english(msg("team.warning", warning=warning))
            session.presenter.publish(Note(Status.WARN, f"  {text}"))
    advice = recommend_mix(mix_attempts(container.shared_ledger().runs()), request.type)
    if advice is not None:
        session.presenter.publish(Note(Status.INFO, english(advice_message(advice, request.type))))
    verify = container.change_plan(request).verify
    if verify:
        commands = ", ".join(verify)
        session.presenter.publish(
            Note(Status.INFO, english(msg("cross.verify_commands", commands=commands)))
        )


def cross_blocks(report: "CrossReport", budget: float) -> "list[Block]":
    from cuanta.cli.document import Column, Line, Table
    from cuanta.cli.fmt import usd
    from cuanta.domain.messages import english, msg
    from cuanta.domain.progress import Status

    rows: list[tuple[str, ...]] = []
    for step in report.steps:
        state = "repair" if step.repair else "salvaged" if step.salvaged else "ok"
        rows.append(
            (
                step.role.value,
                step.engine,
                step.model,
                step.run_id,
                state if step.ok or step.salvaged else "failed",
                usd(step.cost_usd, step.cost_source),
            )
        )
        attempt = 2 if step.repair else 1
        for item in report.verifications:
            if item.role is step.role and item.attempt == attempt:
                passed = sum(result.passed for result in item.results)
                rows.append(
                    (
                        f"verify {item.role.value}",
                        "cuanta",
                        "-",
                        f"{item.seconds:.1f}s",
                        f"{passed}/{len(item.results)} passed",
                        "$0.00",
                    )
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
            tuple(rows),
        ),
        Line(f"spent {usd(report.spent_usd)} of {usd(budget)}"),
        Line(
            english(msg(f"completion.{report.state.value}")),
            Status.OK if report.ok else Status.WARN,
        ),
    ]
    blocks.extend(Line(english(item.reason), Status.INFO) for item in report.skipped)
    for step in report.steps:
        if step.overrun_usd <= 0:
            continue
        overrun = msg(
            "cross.overrun",
            role=step.role.value,
            engine=step.engine,
            cost=f"{step.cost_usd or 0.0:.4f}",
            cap=f"{step.budget_usd:.4f}",
            over=f"{step.overrun_usd:.4f}",
        )
        blocks.append(Line(english(overrun), Status.WARN))
    return blocks


def cross_payload(report: "CrossReport") -> dict[str, object]:
    from cuanta.domain.messages import english

    return {
        "experimental": True,
        "ok": report.ok,
        "completion": report.state.value,
        "spent_usd": report.spent_usd,
        "stopped": english(report.stopped) if report.stopped else None,
        "skipped": [
            {"role": item.role.value, "reason": english(item.reason)} for item in report.skipped
        ],
        "verifications": [
            {
                "role": item.role.value,
                "attempt": item.attempt,
                "passed": item.passed,
                "seconds": item.seconds,
                "results": [
                    {
                        "command": result.command,
                        "exit_code": result.exit_code,
                        "seconds": result.seconds,
                        "timed_out": result.timed_out,
                        "errors": list(result.errors),
                    }
                    for result in item.results
                ],
            }
            for item in report.verifications
        ],
        "steps": [
            {
                "role": step.role.value,
                "engine": step.engine,
                "model": step.model,
                "run_id": step.run_id,
                "ok": step.ok,
                "cost_usd": step.cost_usd,
                "cost_source": step.cost_source,
                "budget_usd": step.budget_usd,
                "native_cap_usd": step.native_cap_usd,
                "overrun_usd": step.overrun_usd,
                "salvaged": step.salvaged,
                "repair": step.repair,
                "handoff_tokens": step.handoff_tokens,
                "covered_files": list(step.covered_files),
                "read_files": list(step.read_files),
                "reread_files": list(step.reread_files),
                "changed_files": list(step.changed_files),
                "unreadable_files": list(step.unreadable_files),
                "index_tools": step.index_tools,
            }
            for step in report.steps
        ],
    }


def team_lines(prepared: "Prepared") -> list[str]:
    import sys

    from cuanta.domain.guarantees import engine_guarantees
    from cuanta.domain.messages import english, msg

    applied = prepared.applied
    lines = [
        f"{prepared.engine_name} · {english(row.message)}"
        for row in engine_guarantees(prepared.engine_name)
    ]
    if prepared.engine_name == "codex" and sys.platform == "win32":
        lines.append(english(msg("guarantee.codex_builds")))
    if applied is None or not applied.active:
        return [*lines, "team: routing off, the engine picks its default models"]
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
    from cuanta.domain.guarantees import cap_warning
    from cuanta.domain.messages import english
    from cuanta.domain.progress import Note, Status

    applied = prepared.applied
    warn = applied is not None and applied.env_override and not applied.unset
    for line in team_lines(prepared):
        status = Status.WARN if warn and "is set" in line else Status.INFO
        session.presenter.publish(Note(status, line))
    warning = cap_warning(prepared.engine_name, prepared.spec.max_budget_usd)
    if warning is not None:
        session.presenter.publish(Note(Status.WARN, english(warning)))
    if prepared.spec.estimate is not None:
        session.presenter.publish(Note(Status.INFO, estimate_line(prepared.spec.estimate)))


def estimate_line(guess: "RunEstimate") -> str:
    from cuanta.cli.fmt import usd

    if guess.low is None:
        return "estimate: none (no similar runs and no priced plan yet)"
    single = guess.high in (None, guess.low)
    span = usd(guess.low) if single else f"{usd(guess.low)}–{usd(guess.high)}"
    if guess.source == "history":
        return f"estimate: {span} from history (n={guess.samples})"
    if guess.factor is not None:
        return f"estimate: {span} from the model plan × {guess.factor:.2f} (n={guess.samples})"
    return f"estimate: {span} from the model plan"


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


def sandbox_payload(result: "SandboxResult") -> dict[str, object]:
    from cuanta.application.trials import trial_payload

    return {
        "copy_root": result.copy_root,
        "removed": result.removed,
        "record_error": result.record_error or None,
        "trial": trial_payload(result.trial) if result.trial is not None else None,
    }


def sandbox_blocks(result: "SandboxResult") -> "list[Block]":
    from cuanta.application.trials import STATE_HINT
    from cuanta.cli.document import Hint, KeyValues, Line
    from cuanta.domain.progress import Status

    trial = result.trial
    where = "removed" if result.removed else f"kept at {result.copy_root}"
    rows = [("isolated copy", where)]
    blocks: list[Block] = []
    if result.record_error:
        blocks.append(
            Line(
                f"sandbox changes could not be collected: {result.record_error}; copy kept",
                Status.FAIL,
            )
        )
    if trial is not None:
        rows.append(("changes", f"{len(trial.changes)} files · +{trial.added} −{trial.removed}"))
        if trial.changes:
            rows.append(("patch", f".cuanta/trials/{trial.run_id}/change.patch"))
        if trial.dependencies_changed_count:
            blocks.append(
                Line(
                    "node_modules in the project changed during the run "
                    f"({trial.dependencies_changed_count} files): run npm ci in the project",
                    Status.FAIL,
                )
            )
        if trial.state_changed_count:
            blocks.append(
                Line(
                    "cuanta's own files in the project changed during the run: "
                    f"{', '.join(trial.state_changed[:5])}; {STATE_HINT}",
                    Status.FAIL,
                )
            )
        if trial.base_missing:
            blocks.append(
                Line(
                    f"{len(trial.base_missing)} files changed in the project while the run "
                    "worked; this run cannot be applied",
                    Status.WARN,
                )
            )
        if trial.read_only_breach:
            blocks.append(
                Line("the investigation changed files in the copy; nothing to apply", Status.WARN)
            )
        if trial.ignored_changes_count:
            shown = ", ".join(trial.ignored_changes[:5])
            blocks.append(
                Line(
                    f"{trial.ignored_changes_count} files ignored by .gitignore changed in the "
                    f"copy and were left out: {shown}",
                    Status.WARN,
                )
            )
        if trial.applicable:
            blocks.append(
                Hint(
                    f"cuanta runs apply {trial.run_id} · cuanta runs branch {trial.run_id} "
                    f"· cuanta runs discard {trial.run_id}"
                )
            )
    return [KeyValues(tuple(rows)), *blocks]


def guard_tripped(result: "SandboxResult | None") -> bool:
    return result is not None and (
        bool(result.record_error) or (result.trial is not None and result.trial.guard_tripped)
    )


def _final(report: "MandateReport", isolated: "SandboxResult | None" = None) -> "Document":
    from cuanta.application.mandate import report_payload
    from cuanta.cli.document import Document, Hint, KeyValues, MarkdownText, MascotBlock, Panel
    from cuanta.cli.fmt import compact, percent, usd
    from cuanta.domain.engine import TURN_LIMIT_SUBTYPE
    from cuanta.domain.guarantees import budget_stop_reason
    from cuanta.domain.messages import english
    from cuanta.domain.voice import Mood

    run = report.run
    stop = budget_stop_reason(run.end_reason)
    budget_rows = (("stop reason", english(stop)),) if stop is not None else ()
    agents = ", ".join(
        f"{name} {compact(tokens)}" for name, tokens in report.tokens_by_agent.items()
    )
    index = "n/a" if report.utilization is None else percent(report.utilization)
    turn_rows: tuple[tuple[str, str], ...] = ()
    if run.max_turns > 0 or run.end_reason == TURN_LIMIT_SUBTYPE:
        count = f"{run.turns}/{run.max_turns}" if run.max_turns > 0 else str(run.turns)
        cut = " · cut by turn limit" if run.end_reason == TURN_LIMIT_SUBTYPE else ""
        turn_rows = (("turns", f"{count}{cut}"),)
    trial = isolated.trial if isolated is not None else None
    changed = len(trial.changes) if trial is not None else len(report.changed_files)
    rows = (
        ("run", run.id),
        ("status", run.status),
        ("scope hint", report.hint.option),
        ("files changed", str(changed)),
        ("tests", report.tests),
        ("tokens by agent", agents or "-"),
        ("cost", usd(run.cost_usd, run.cost_source)),
        *turn_rows,
        *budget_rows,
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
    if isolated is not None:
        blocks.extend(sandbox_blocks(isolated))
    payload = {**report_payload(report), "report_text": report.text}
    if isolated is not None:
        payload["sandbox"] = sandbox_payload(isolated)
    ok = report.ok and not guard_tripped(isolated)
    return Document(blocks=tuple(blocks), payload=payload, exit_code=0 if ok else 1)
