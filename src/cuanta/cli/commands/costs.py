from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.cli.document import Block, Document, Table
    from cuanta.domain.real_costs import CostReport, CostRow, PhaseCostRow

TYPE_LABELS = {
    "investigation": "audit",
    "fix": "fix",
    "feature": "feature",
    "refactor": "refactor",
    "docs": "docs",
    "untyped": "untyped",
}
HEADERS = ("group", "runs", "acc.", "per acc.", "spend", "n/a", "median", "time", "err.")
CENT = 0.01
MIX_LABELS = {
    "claude": "Claude",
    "codex": "Codex",
    "opencode": "OpenCode",
    "cross-engine": "cross-engine",
}


def costs_command(
    ctx: typer.Context,
    since: Annotated[
        str,
        typer.Option("--since", help="First day to count, YYYY-MM-DD (UTC); default 30 days."),
    ] = "",
) -> None:
    execute(ctx, lambda session: _costs(session, since))


def _costs(session: Session, since: str) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, Hint
    from cuanta.domain.errors import DomainFailure
    from cuanta.domain.real_costs import since_date

    start = ""
    if since:
        parsed = since_date(since)
        if parsed is None:
            raise DomainFailure(f"--since {since} is not a date", "use YYYY-MM-DD")
        start = parsed
    container = Container.for_project(session.project)
    try:
        report = container.costs_query().report(start)
    finally:
        container.close()
    blocks: list[Block] = [
        _table(f"by type since {report.since[:10]} UTC", report.by_type, TYPE_LABELS),
        _table("by engine mix", report.by_mix, MIX_LABELS),
    ]
    if report.phase_medians and not report.empty:
        blocks.append(_phase_table(report.phase_medians))
        blocks.append(
            Hint(
                "Phase medians use observed priced requests, separate from billed attempt costs. "
                "Missing requests or costs exclude an attempt; covered absent phases count zero."
            )
        )
    if report.empty:
        blocks.append(Hint("no mandates in this window · run cuanta mandate"))
    else:
        blocks.append(
            Hint(
                "≥ lower bound: some runs have no cost · n/a: runs without a cost · "
                "* includes estimated costs · acc. accepted runs · "
                "per acc.: all attempts ÷ accepted runs · err.: median estimate error"
            )
        )
        blocks.append(Hint("record outcomes: cuanta runs accept <id> · cuanta runs reject <id>"))
    return Document(blocks=tuple(blocks), payload=costs_payload(report))


def money(value: float | None, lower_bound: bool = False, estimated: bool = False) -> str:
    if value is None:
        return "n/a"
    shown = f"${value:,.2f}" if value == 0 or value >= CENT else f"${value:.4f}"
    return f"{'≥' if lower_bound else ''}{shown}{'*' if estimated else ''}"


def error_cell(row: "CostRow") -> str:
    if row.median_error is None:
        return "-"
    return "in range" if row.error_in_range else f"{row.median_error:+.0%}"


def _cells(row: "CostRow", labels: dict[str, str]) -> tuple[str, ...]:
    from cuanta.cli.fmt import duration

    label = labels.get(row.key, row.key)
    if not row.runs:
        return (label, "0", "0", "-", "-", "-", "-", "-", "-")
    spend = row.spend
    bound = spend.lower_bound
    return (
        label,
        str(row.runs),
        str(row.accepted),
        money(row.per_accepted, bound and row.per_accepted is not None, row.estimated),
        money(spend.value, bound, row.estimated),
        str(spend.missing),
        money(row.median_cost, False, row.estimated),
        duration(row.median_seconds) if row.median_seconds is not None else "-",
        error_cell(row),
    )


def _table(title: str, rows: "tuple[CostRow, ...]", labels: dict[str, str]) -> "Table":
    from cuanta.cli.document import Column, Table

    cells = tuple(_cells(row, labels) for row in rows)
    widths = [
        max(len(header), *(len(row[index]) for row in cells))
        for index, header in enumerate(HEADERS)
    ]
    columns = tuple(
        Column(header, numeric=index > 0, min_width=width if index else 0)
        for index, (header, width) in enumerate(zip(HEADERS, widths, strict=True))
    )
    return Table(title, columns, cells)


def _phase_table(rows: "tuple[PhaseCostRow, ...]") -> "Table":
    from cuanta.cli.document import Column, Table
    from cuanta.domain.anatomy import Phase

    return Table(
        "observed phase cost medians by type",
        (
            Column("type"),
            Column("covered", numeric=True),
            Column("missing", numeric=True),
            *(Column(phase.value, numeric=True) for phase in Phase),
        ),
        tuple(
            (
                TYPE_LABELS.get(row.key, row.key),
                f"{row.covered}/{row.runs}",
                str(row.missing),
                *(money(item.median_cost_usd) for item in row.medians),
            )
            for row in rows
        ),
    )


def phase_payload(row: "PhaseCostRow") -> dict[str, object]:
    return {
        "key": row.key,
        "runs": row.runs,
        "covered_attempts": row.covered,
        "missing_attempts": row.missing,
        "phases": [
            {
                "phase": item.phase.value,
                "median_cost_usd": item.median_cost_usd,
                "samples": item.samples,
            }
            for item in row.medians
        ],
    }


def row_payload(row: "CostRow") -> dict[str, object]:
    spend = row.spend
    return {
        "key": row.key,
        "runs": row.runs,
        "accepted": row.accepted,
        "rejected": row.rejected,
        "pending": row.pending,
        "spend_usd": spend.value,
        "spend_lower_bound": spend.lower_bound,
        "runs_without_cost": spend.missing,
        "runs_with_estimated_cost": spend.estimated,
        "median_cost_usd": row.median_cost,
        "median_cost_includes_estimated": row.estimated and row.median_cost is not None,
        "median_seconds": row.median_seconds,
        "cost_per_accepted_usd": row.per_accepted,
        "cost_per_accepted_lower_bound": spend.lower_bound and row.per_accepted is not None,
        "cost_per_accepted_includes_estimated": row.estimated and row.per_accepted is not None,
        "median_estimate_error": row.median_error,
        "estimate_error_in_range": row.error_in_range,
        "estimate_samples": row.errors,
    }


def costs_payload(report: "CostReport") -> dict[str, object]:
    return {
        "since": report.since,
        "by_type": [row_payload(row) for row in report.by_type],
        "by_engine_mix": [row_payload(row) for row in report.by_mix],
        "total": row_payload(report.total),
        "phase_medians": [phase_payload(row) for row in report.phase_medians],
    }
