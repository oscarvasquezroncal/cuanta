from __future__ import annotations

from textual.content import Content

from cuanta.domain.anatomy import AnatomyReport, Phase, PhaseTotals
from cuanta.tui.fmt import cost_money
from cuanta.tui.i18n import Catalog

STACK_WIDTH = 40
PHASE_WIDTH = 14
TOKEN_WIDTH = 12
COST_WIDTH = 10
REQUEST_WIDTH = 11
PHASE_STYLES = {
    Phase.START: "$primary",
    Phase.EXPLORATION: "$secondary",
    Phase.WRITING: "$success",
    Phase.HANDOFF: "$warning",
}


def phase_stack(report: AnatomyReport, width: int = STACK_WIDTH) -> tuple[tuple[Phase, str], ...]:
    totals = {row.phase: row.totals.total for row in report.phases}
    whole = sum(totals.values())
    if whole <= 0 or width <= 0:
        return ()
    shares = {phase: width * totals.get(phase, 0) / whole for phase in Phase}
    sizes = {phase: int(shares[phase]) for phase in Phase}
    remaining = width - sum(sizes.values())
    ranked = sorted(Phase, key=lambda phase: shares[phase] - sizes[phase], reverse=True)
    for phase in ranked[:remaining]:
        sizes[phase] += 1
    return tuple((phase, "█" * sizes[phase]) for phase in Phase if sizes[phase] > 0)


def anatomy_content(t: Catalog, report: AnatomyReport) -> Content:
    title = Content.styled(t("anatomy.title"), "bold")
    if report.totals.requests == 0:
        return Content("\n").join((title, Content.styled(t("anatomy.empty"), "$text-muted")))
    lines = [title]
    stack = phase_stack(report)
    if stack:
        lines.append(Content.assemble(*((text, PHASE_STYLES[phase]) for phase, text in stack)))
    else:
        lines.append(Content.styled(t("anatomy.no_tokens"), "$text-muted"))
    lines.append(
        Content("  ").join(
            Content.styled(t(f"anatomy.{phase.value}"), PHASE_STYLES[phase]) for phase in Phase
        )
    )
    lines.append(
        Content.styled(
            t("anatomy.col_phase").ljust(PHASE_WIDTH)
            + t("anatomy.col_tokens").rjust(TOKEN_WIDTH)
            + t("anatomy.col_cost").rjust(COST_WIDTH)
            + t("anatomy.col_requests").rjust(REQUEST_WIDTH),
            "$text-muted",
        )
    )
    totals = {row.phase: row.totals for row in report.phases}
    for phase in Phase:
        row = totals.get(phase, PhaseTotals())
        lines.append(
            Content.assemble(
                (t(f"anatomy.{phase.value}").ljust(PHASE_WIDTH), PHASE_STYLES[phase]),
                f"{row.total:,}".rjust(TOKEN_WIDTH),
                cost_money(row.cost_usd, t("spectrum.na")).rjust(COST_WIDTH),
                f"{row.requests:,}".rjust(REQUEST_WIDTH),
            )
        )
    lines.append(Content.styled(t("anatomy.heuristic"), "$text-muted"))
    return Content("\n").join(lines)
