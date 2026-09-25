from typing import TYPE_CHECKING, Annotated, Any

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.cli.document import Block, Document, TreeNode
    from cuanta.domain.spectrum import View


def spectrum_command(
    ctx: typer.Context,
    run: Annotated[str | None, typer.Argument(help="Run id, id prefix or HU-NNN.")] = None,
    session: Annotated[str | None, typer.Option("--session", help="One session id.")] = None,
    since: Annotated[str | None, typer.Option("--since", help="ISO date or timestamp.")] = None,
    by: Annotated[str | None, typer.Option("--by", help="agent, model, tool or file.")] = None,
    plan: Annotated[bool, typer.Option("--plan", help="Five-hour windows and weeks.")] = False,
    import_: Annotated[
        bool, typer.Option("--import", help="Import local Claude Code and Codex transcripts first.")
    ] = False,
    rebuild: Annotated[
        bool,
        typer.Option("--rebuild", help="With --import: drop imported events and re-read them."),
    ] = False,
) -> None:
    execute(
        ctx, lambda current: _spectrum(current, run, session, since, by, plan, import_, rebuild)
    )


def _tree(branch: Any, unicode: bool) -> "TreeNode":
    from cuanta.cli.document import TreeNode
    from cuanta.cli.fmt import compact, percent

    label = f"{branch.label}  {compact(branch.tokens)} · {percent(branch.share)}"
    return TreeNode(label, tuple(_tree(child, unicode) for child in branch.children))


def _cost_text(cost: Any) -> str:
    from cuanta.cli.fmt import usd

    if cost.value is None:
        unpriced = f": no price for {', '.join(cost.unpriced)}" if cost.unpriced else ""
        return f"n/a ({cost.source}{unpriced})"
    return f"{usd(cost.value)} API-equivalent ({cost.source})"


def _branch_payload(branch: Any) -> dict[str, Any]:
    return {
        "label": branch.label,
        "tokens": branch.tokens,
        "share": round(branch.share, 4),
        "children": [_branch_payload(child) for child in branch.children],
    }


def _import(session: Session, container: Any, ledger: Any, rebuild: bool) -> dict[str, Any]:
    from cuanta.domain.progress import Note, Status

    imported: dict[str, Any] = {}
    importer = container.transcript_import(ledger)
    for source, counts in importer.run(rebuild=rebuild).items():
        if rebuild:
            removed = importer.removed.get(source, 0)
            session.presenter.publish(
                Note(Status.INFO, f"rebuild: removed {removed:,} old events from {source}")
            )
        imported[source] = {
            "removed": importer.removed.get(source, 0),
            "files": getattr(counts, "files", 0),
            "records": getattr(counts, "records", 0),
            "events": getattr(counts, "events", 0),
            "skipped": getattr(counts, "skipped", 0),
        }
        session.presenter.publish(
            Note(Status.INFO, f"imported {imported[source]['events']:,} events from {source}")
        )
    return imported


def _view(by: str | None) -> "View | None":
    from cuanta.domain.errors import DomainFailure
    from cuanta.domain.spectrum import View

    if by is None:
        return None
    try:
        return View(by)
    except ValueError as error:
        raise DomainFailure(f"unknown view {by}", "use agent, model, tool or file") from error


def _spectrum(
    session: Session,
    run: str | None,
    session_id: str | None,
    since: str | None,
    by: str | None,
    plan: bool,
    do_import: bool,
    rebuild: bool = False,
) -> "Document":
    from cuanta.application.spectrum import Selection
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Column, Document, Hint, KeyValues, Line, Table
    from cuanta.cli.fmt import compact, percent, thousands, usd
    from cuanta.domain.errors import DomainFailure
    from cuanta.domain.progress import Status
    from cuanta.domain.spectrum import QUOTA_NOTE

    view = _view(by)
    if rebuild and not do_import:
        raise DomainFailure("--rebuild needs --import", "run cuanta spectrum --import --rebuild")
    container = Container.for_project(session.project)
    ledger = container.ledger()
    imported: dict[str, Any] = {}
    try:
        if do_import:
            imported = _import(session, container, ledger, rebuild)
        selection = Selection(run=run or "", session=session_id or "", since=since or "")
        if do_import and not (run or session_id or since):
            selection = Selection(since="0")
        result = container.spectrum_query(ledger).run(selection)
    finally:
        ledger.close()
    report = result.report
    totals = report.totals
    dot = "·" if session.settings.unicode else "-"
    utilization = report.utilization
    index = "n/a" if utilization.value is None else percent(utilization.value)
    header = KeyValues(
        (
            ("selection", report.label),
            ("total", f"{thousands(totals.total)} tokens {dot} {totals.requests:,} requests"),
            (
                "split",
                f"fresh {compact(totals.fresh_input)} {dot} "
                f"cache read {compact(totals.cache_read)} {dot} "
                f"cache write {compact(totals.cache_write)} {dot} "
                f"output {compact(totals.output)} {dot} "
                f"reasoning {compact(totals.reasoning)}",
            ),
            ("cost", _cost_text(report.cost)),
            ("cache share", percent(totals.cache_share)),
            (
                "utilization",
                f"{index} ({utilization.label})"
                if utilization.value is not None
                else f"n/a ({utilization.reason})",
            ),
            ("source", report.source),
        )
    )
    blocks: list[Block] = [header]
    if totals.total:
        blocks.extend(_overhead(result))
    if totals.total == 0:
        blocks.append(Line("no token events for this selection · nap", Status.INFO))
        blocks.append(Hint("run inside cuanta (init, pounce) or add --import"))
    elif view is None and not plan:
        blocks.append(_tree(report.tree, session.settings.unicode))
    if view is not None:
        rows = result.rows(view)
        blocks.append(
            Table(
                f"by {view.value}",
                (
                    Column(view.value),
                    Column("tokens", numeric=True),
                    Column("share", numeric=True),
                    Column("count", numeric=True),
                    Column("cost", numeric=True),
                ),
                tuple(
                    (
                        row.key,
                        compact(row.tokens),
                        percent(row.share),
                        f"{row.count:,}",
                        usd(row.cost_usd),
                    )
                    for row in rows[:20]
                ),
            )
        )
    if plan:
        windows, weeks = result.windows()
        for title, items in (("five-hour windows", windows), ("weeks", weeks)):
            blocks.append(
                Table(
                    title,
                    (
                        Column("window"),
                        Column("start"),
                        Column("total", numeric=True),
                        Column("cache read", numeric=True),
                        Column("requests", numeric=True),
                    ),
                    tuple(
                        (
                            item.label,
                            item.start[:16],
                            compact(item.totals.total),
                            compact(item.totals.cache_read),
                            f"{item.totals.requests:,}",
                        )
                        for item in items
                    ),
                )
            )
        blocks.append(
            Line(f"cached reads still count toward plan limits; {QUOTA_NOTE}", Status.INFO)
        )
    if result.audits:
        blocks.append(
            Table(
                "routing audit",
                (
                    Column("agent"),
                    Column("role"),
                    Column("planned"),
                    Column("ran on"),
                    Column(""),
                    Column("cause"),
                ),
                tuple(
                    (
                        audit.agent,
                        audit.role,
                        audit.planned,
                        audit.actual or "-",
                        {"match": "✓", "not_run": "–"}.get(audit.status, "✗"),
                        "" if audit.status == "match" else audit.cause,
                    )
                    for audit in result.audits
                ),
            )
        )
    if report.leaks:
        blocks.append(
            Table(
                "leaks",
                (
                    Column("kind"),
                    Column("subject"),
                    Column("agent"),
                    Column("tokens", numeric=True),
                ),
                tuple(
                    (leak.kind.value, leak.subject[:48], leak.agent, compact(leak.tokens))
                    for leak in report.leaks
                ),
            )
        )
        blocks.extend(Hint(suggestion.action) for suggestion in report.suggestions)
    payload = _payload(result, view, plan, imported)
    return Document(blocks=tuple(blocks), payload=payload)


def _overhead(result: Any) -> "list[Block]":
    from cuanta.cli.document import Line, Panel
    from cuanta.domain.messages import english
    from cuanta.domain.overhead import overhead_messages

    lines = overhead_messages(result.overhead)
    if not lines:
        return []
    return [Panel("session overhead", tuple(Line(english(line)) for line in lines))]


def _payload(result: Any, view: Any, plan: bool, imported: dict[str, Any]) -> dict[str, Any]:
    from cuanta.domain.overhead import overhead_payload
    from cuanta.domain.spectrum import QUOTA_NOTE

    report = result.report
    totals = report.totals
    utilization = report.utilization
    payload: dict[str, Any] = {
        "selection": report.label,
        "runs": [item.id for item in result.runs],
        "overhead": overhead_payload(result.overhead),
        "audit": [
            {
                "agent": audit.agent,
                "role": audit.role,
                "planned": audit.planned,
                "actual": audit.actual,
                "status": audit.status,
                "cause": audit.cause,
            }
            for audit in result.audits
        ],
        "totals": {
            "total": totals.total,
            "fresh_input": totals.fresh_input,
            "cache_read": totals.cache_read,
            "cache_write": totals.cache_write,
            "output": totals.output,
            "reasoning": totals.reasoning,
            "cost_usd": None if report.cost.value is None else round(report.cost.value, 6),
            "cache_share": round(totals.cache_share, 4),
            "requests": totals.requests,
        },
        "cost": {
            "value": None if report.cost.value is None else round(report.cost.value, 6),
            "source": report.cost.source,
            "unpriced": list(report.cost.unpriced),
        },
        "utilization": {
            "value": None if utilization.value is None else round(utilization.value, 4),
            "useful_tokens": utilization.useful_tokens,
            "label": utilization.label,
            "reason": utilization.reason,
        },
        "tree": _branch_payload(report.tree),
        "leaks": [
            {
                "kind": leak.kind.value,
                "subject": leak.subject,
                "agent": leak.agent,
                "tokens": leak.tokens,
                "detail": leak.detail,
            }
            for leak in report.leaks
        ],
        "suggestions": [
            {"leak": item.leak.value, "action": item.action} for item in report.suggestions
        ],
        "source": report.source,
        "calibration": dict(report.calibration),
        "imported": imported,
    }
    if view is not None:
        payload["by"] = {
            "view": view.value,
            "rows": [
                {
                    "key": row.key,
                    "tokens": row.tokens,
                    "share": round(row.share, 4),
                    "count": row.count,
                    "cost_usd": round(row.cost_usd, 6),
                }
                for row in result.rows(view)
            ],
        }
    if plan:
        windows, weeks = result.windows()
        payload["plan"] = {
            "note": QUOTA_NOTE,
            "windows": [
                {
                    "label": item.label,
                    "start": item.start,
                    "total": item.totals.total,
                    "cache_read": item.totals.cache_read,
                }
                for item in windows
            ],
            "weeks": [
                {
                    "label": item.label,
                    "start": item.start,
                    "total": item.totals.total,
                    "cache_read": item.totals.cache_read,
                }
                for item in weeks
            ],
        }
    return payload
