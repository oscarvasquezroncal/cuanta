from __future__ import annotations

from textual.content import Content

from cuanta.domain.time_anatomy import PHASES, PhaseTime, TimeReport
from cuanta.tui.i18n import Catalog

LABEL_WIDTH = 34
VALUE_WIDTH = 12


def time_seconds(t: Catalog, seconds: float | None) -> str:
    return t("time.na") if seconds is None else t("time.seconds", value=f"{seconds:,.3f}")


def phase_label(t: Catalog, phase: str) -> str:
    if phase.startswith("tool:"):
        return t("time.tool", name=phase.removeprefix("tool:"))
    return t(f"time.{phase}") if phase in PHASES or phase == "wall" else phase


def _phase_lines(t: Catalog, phases: tuple[PhaseTime, ...]) -> list[Content]:
    return [
        Content.assemble(
            phase_label(t, phase.phase).ljust(LABEL_WIDTH),
            time_seconds(t, phase.seconds).rjust(VALUE_WIDTH),
        )
        for phase in phases
    ]


def time_content(t: Catalog, report: TimeReport) -> Content:
    lines = [
        Content.styled(t("time.title"), "bold"),
        Content.assemble(
            t("time.wall").ljust(LABEL_WIDTH),
            time_seconds(t, report.wall_seconds).rjust(VALUE_WIDTH),
        ),
        Content.styled(t("time.note"), "$text-muted"),
        *_phase_lines(t, report.phases or tuple(PhaseTime(phase, None, 0) for phase in PHASES)),
    ]
    for role in report.roles:
        lines.extend(
            (
                Content(""),
                Content.styled(t("time.role", name=role.role or t("time.na")), "bold"),
                *_phase_lines(t, role.phases),
            )
        )
    if report.requests:
        lines.extend((Content(""), Content.styled(t("time.requests"), "bold")))
        for request in report.requests:
            lines.append(
                Content(
                    t(
                        "time.request",
                        name=request.request_id or t("time.na"),
                        role=request.role or t("time.na"),
                        model=request.model or t("time.na"),
                        duration=time_seconds(t, request.duration_seconds),
                        ttft=time_seconds(t, request.ttft_seconds),
                    )
                )
            )
    return Content("\n").join(lines)
