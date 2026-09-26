from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.application.cache_probe import CacheProbeReport
    from cuanta.cli.document import Document

probe_app = typer.Typer(
    help="Small, capped live measurements of engine behaviour.", no_args_is_help=True
)


@probe_app.command(
    "cache-ttl", help="Measure the prompt-cache TTL with tiny identical runs; --yes spends."
)
def cache_ttl_command(
    ctx: typer.Context,
    gaps: Annotated[
        str, typer.Option("--gaps", help="Start-to-start gaps, such as 1m,6m.")
    ] = "60,360",
    long: Annotated[bool, typer.Option("--long", help="Also test after 61 minutes.")] = False,
    budget_usd: Annotated[float, typer.Option("--budget-usd", min=0.01)] = 0.10,
    per_run_usd: Annotated[float, typer.Option("--per-run-usd", min=0.01)] = 0.05,
    tools: Annotated[
        str, typer.Option("--tools", help="Built-in tools for the prefix.")
    ] = "Read,Glob",
    model: Annotated[str, typer.Option("--model", help="Claude model; default economy tier.")] = "",
    keep: Annotated[bool, typer.Option("--keep", help="Keep the temporary project.")] = False,
    no_save: Annotated[bool, typer.Option("--no-save", help="Do not update user config.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Spend within the cap.")] = False,
) -> None:
    execute(
        ctx,
        lambda session: _cache_ttl(
            session,
            f"{gaps},3660" if long else gaps,
            budget_usd,
            per_run_usd,
            tuple(part.strip() for part in tools.split(",") if part.strip()),
            model,
            keep,
            not no_save,
            yes or session.options.yes,
        ),
    )


def _plan(report: CacheProbeReport) -> Document:
    from cuanta.cli.document import Document, Hint, KeyValues

    plan = report.plan
    return Document(
        blocks=(
            KeyValues(
                (
                    ("model", report.model),
                    ("auth", report.expected_auth),
                    ("gaps", ", ".join(str(gap) for gap in plan.gaps_s) + " s"),
                    ("worst run", _money(plan.worst_run_usd)),
                    ("worst case", _money(plan.worst_case_usd)),
                    ("total cap", _money(plan.budget_usd)),
                )
            ),
            Hint("nothing spent · add --yes to run it"),
        ),
        payload={
            "ran": False,
            "model": report.model,
            "gaps_s": list(plan.gaps_s),
            "ceiling_usd": plan.budget_usd,
            "worst_case_usd": plan.worst_case_usd,
            "affordable_gaps_s": list(plan.affordable_gaps_s),
        },
    )


def _money(value: float | None) -> str:
    return "unknown" if value is None else f"${value:.4f}"


def _cache_ttl(
    session: Session,
    gaps: str,
    budget_usd: float,
    per_run_usd: float,
    tools: tuple[str, ...],
    model: str,
    keep: bool,
    save: bool,
    spend: bool,
) -> Document:
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Column, Document, Hint, Line, Table
    from cuanta.domain.messages import english
    from cuanta.domain.progress import Status

    report = Container.for_project(session.project).probe_cache_ttl(
        gaps,
        budget_usd,
        per_run_usd,
        tools,
        model,
        spend,
        keep,
        save,
        session.presenter,
    )
    if report.result is None:
        return _plan(report)
    result = report.result
    items = (result.seed, *result.readings)
    states = ("seed", *(state.value for state in result.warmth))
    rows = tuple(
        (
            str(round(item.gap_s)),
            str(item.cache_read),
            str(item.cache_write),
            str(item.write_5m),
            str(item.write_1h),
            state,
            _money(item.cost_usd),
        )
        for item, state in zip(items, states, strict=True)
    )
    status = Status.OK if result.saveable else Status.WARN
    hint = "saved to the user config" if report.saved else "not saved"
    blocks = (
        Table(
            "cache TTL probe",
            (
                Column("gap s", numeric=True),
                Column("read", numeric=True),
                Column("wrote", numeric=True),
                Column("5m", numeric=True),
                Column("1h", numeric=True),
                Column("cache"),
                Column("cost", numeric=True),
            ),
            rows,
        ),
        Line(english(result.message), status),
        Hint(hint),
    )
    return Document(
        blocks=blocks,
        payload={
            "ran": True,
            "verdict": result.verdict.value,
            "ttl_s": result.ttl_s,
            "ttl_lower_s": result.lower_s,
            "ttl_upper_s": result.upper_s,
            "declared_ttl_s": result.declared_s,
            "auth": result.auth.value,
            "api_key_source": result.api_key_source,
            "engine_version": result.engine_version,
            "model": result.model,
            "tools": list(result.tools),
            "measured_on": result.measured_on,
            "spent_usd": result.spent_usd,
            "saved": report.saved,
            "scratch_path": report.scratch_path,
            "skipped_gaps_s": list(result.skipped_gaps_s),
            "runs": [
                {
                    "gap_s": item.gap_s,
                    "run_id": item.run_id,
                    "cache_read": item.cache_read,
                    "cache_write": item.cache_write,
                    "write_5m": item.write_5m,
                    "write_1h": item.write_1h,
                    "warmth": state,
                    "cost_usd": item.cost_usd,
                }
                for item, state in zip(items, states, strict=True)
            ],
        },
        exit_code=0 if result.saveable else 1,
    )
