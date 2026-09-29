from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.cli.document import Block, Document, Table
    from cuanta.domain.real_costs import CostReport, CostRow, PhaseCostRow
    from cuanta.domain.run_metrics import Reactions, RunMetrics

TYPE_LABELS = {
    "investigation": "audit",
    "fix": "fix",
    "feature": "feature",
    "refactor": "refactor",
    "docs": "docs",
    "untyped": "untyped",
}
HEADERS = ("group", "runs", "acc.", "per acc.", "spend", "n/a", "median", "time", "err.")
COST_METRIC_HEADERS = (
    "run",
    "type",
    "engine",
    "P50",
    "P90",
    "actual",
    "cap used",
    "P90-actual",
    "per acc.",
    "per line",
)
EXPLORATION_METRIC_HEADERS = (
    "run",
    "blocked reads",
    "tokens avoided",
    "finishes",
    "rotations",
    "warm 1st",
    "warm all",
    "pack",
    "senior in",
)
CENT = 0.01
COMPLETION_LABELS = {
    "complete": "complete",
    "complete_skipped": "complete, optional roles skipped",
    "partial": "partial",
    "failed": "failed",
}
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
    metrics: Annotated[
        bool,
        typer.Option(
            "--metrics",
            help="Add per-run metrics: forecast against actual, margin, cost per accepted "
            "change, blocked reads, finishes, rotations, warm cache and scout pack.",
        ),
    ] = False,
) -> None:
    execute(ctx, lambda session: _costs(session, since, metrics))


def _costs(session: Session, since: str, metrics: bool = False) -> "Document":
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
        query = container.costs_query()
        report = query.report(start)
        per_run = query.metrics(start) if metrics else ()
    finally:
        container.close()
    blocks: list[Block] = [
        _table(f"by type since {report.since[:10]} UTC", report.by_type, TYPE_LABELS),
        _table("by engine mix", report.by_mix, MIX_LABELS),
    ]
    if report.by_completion:
        blocks.append(
            _table("cross-engine runs by completion", report.by_completion, COMPLETION_LABELS)
        )
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
    payload = costs_payload(report)
    if metrics:
        from cuanta.domain.run_metrics import metrics_payload

        blocks.extend(metric_blocks(report.since, per_run))
        payload["metrics"] = [metrics_payload(item) for item in per_run]
    return Document(blocks=tuple(blocks), payload=payload)


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
        "by_completion": [row_payload(row) for row in report.by_completion],
        "total": row_payload(report.total),
        "phase_medians": [phase_payload(row) for row in report.phase_medians],
    }


def share(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def count(value: int | None) -> str:
    return "n/a" if value is None else f"{value:,}"


def signed(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"-{money(-value)}" if value < 0 else money(value)


def reaction_cell(reactions: "Reactions | None") -> str:
    if reactions is None:
        return "n/a"
    if reactions.saved_usd is None:
        return str(reactions.count)
    return f"{reactions.count} ({money(reactions.saved_usd)})"


def cost_cells(item: "RunMetrics") -> tuple[str, ...]:
    per_line = item.tokens_per_line
    return (
        item.run_id,
        TYPE_LABELS.get(item.task_type, item.task_type),
        MIX_LABELS.get(item.provider, item.provider),
        money(item.p50_usd),
        money(item.p90_usd),
        money(item.actual_usd, estimated=item.estimated),
        share(item.cap_used),
        signed(item.p90_left_usd),
        money(item.per_accepted_usd, estimated=item.estimated),
        "n/a" if per_line is None else f"{per_line:,.0f}",
    )


def exploration_cells(item: "RunMetrics") -> tuple[str, ...]:
    blocked = item.blocked
    return (
        item.run_id,
        count(blocked.reads if blocked is not None else None),
        count(blocked.tokens if blocked is not None else None),
        reaction_cell(item.finishes),
        reaction_cell(item.rotations),
        share(item.first_warm_share),
        share(item.warm_share),
        count(item.pack_tokens),
        count(item.senior_input_tokens),
    )


def metric_table(
    title: str, headers: tuple[str, ...], numeric_from: int, rows: tuple[tuple[str, ...], ...]
) -> "Table":
    from cuanta.cli.document import Column, Table

    return Table(
        title,
        tuple(
            Column(header, numeric=index >= numeric_from) for index, header in enumerate(headers)
        ),
        rows,
    )


def metric_blocks(since: str, items: "tuple[RunMetrics, ...]") -> "list[Block]":
    from cuanta.cli.document import Hint

    if not items:
        return [Hint("no runs with metrics in this window")]
    return [
        metric_table(
            f"per-run cost since {since[:10]} UTC",
            COST_METRIC_HEADERS,
            3,
            tuple(cost_cells(item) for item in items),
        ),
        metric_table(
            "per-run exploration and cache",
            EXPLORATION_METRIC_HEADERS,
            1,
            tuple(exploration_cells(item) for item in items),
        ),
        Hint(
            "n/a: unknown, never zero · cap used: actual ÷ cap · per line: tokens ÷ changed "
            "lines of an accepted isolated-copy patch · finishes and rotations: count (saving) · "
            "warm: cache read ÷ input of first requests and of all requests · "
            "--json adds forecast and actual tokens by bucket"
        ),
    ]
