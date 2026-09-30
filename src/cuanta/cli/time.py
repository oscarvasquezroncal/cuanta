from __future__ import annotations

from cuanta.cli.document import Block, Column, Hint, KeyValues, Table
from cuanta.domain.time_anatomy import PHASES, PhaseTime, TimeReport


def seconds(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.3f}"


def phase_table(title: str, phases: tuple[PhaseTime, ...]) -> Table:
    return Table(
        title,
        (Column("phase"), Column("seconds", numeric=True), Column("samples", numeric=True)),
        tuple((phase.phase, seconds(phase.seconds), str(phase.samples)) for phase in phases),
    )


def time_blocks(report: TimeReport) -> list[Block]:
    phases = report.phases or tuple(PhaseTime(phase, None, 0) for phase in PHASES)
    blocks: list[Block] = [
        KeyValues((("wall time (s)", seconds(report.wall_seconds)),)),
        phase_table("time", phases),
        Hint("Measured durations can overlap; they do not add up to wall time. Unknown stays n/a."),
    ]
    blocks.extend(phase_table(f"time · {role.role or 'n/a'}", role.phases) for role in report.roles)
    if report.requests:
        blocks.append(
            Table(
                "request time",
                (
                    Column("request"),
                    Column("role"),
                    Column("model"),
                    Column("purpose"),
                    Column("duration (s)", numeric=True),
                    Column("first token (s)", numeric=True),
                ),
                tuple(
                    (
                        request.request_id or "n/a",
                        request.role or "n/a",
                        request.model or "n/a",
                        request.purpose or "n/a",
                        seconds(request.duration_seconds),
                        seconds(request.ttft_seconds),
                    )
                    for request in report.requests
                ),
            )
        )
    return blocks
