from __future__ import annotations

from cuanta.domain.ledger import Run
from cuanta.domain.progress import Status
from cuanta.tui.i18n import Catalog

UNITS = ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k"))
GLYPHS = {
    Status.OK: "✓",
    Status.WARN: "!",
    Status.FAIL: "✗",
    Status.INFO: "i",
    Status.SKIP: "–",
    Status.RESUME: "↺",
}


CENT = 0.01
SUB_CENT = "<$0.01"
BOUND = "≥"
ESTIMATED_MARK = "*"


def compact(value: float) -> str:
    for size, unit in UNITS:
        if abs(value) >= size:
            scaled = value / size
            text = f"{scaled:.1f}".rstrip("0").rstrip(".")
            return f"{text}{unit}"
    return f"{int(value):,}"


def grouped(value: int) -> str:
    return f"{value:,}"


def money(value: float | None, unknown: str = "–") -> str:
    if value is None:
        return unknown
    return f"${value:,.2f}"


def cost_money(
    value: float | None, unknown: str, bound: bool = False, estimated: bool = False
) -> str:
    if value is None:
        return unknown
    shown = money(value) if not 0 < value < CENT else f"${value:.4f}" if bound else SUB_CENT
    return f"{BOUND if bound else ''}{shown}{ESTIMATED_MARK if estimated else ''}"


def run_money(run: Run, catalog: Catalog) -> str:
    value = money(run.cost_usd, catalog("spectrum.na"))
    return labeled_money(value, run.cost_usd, run.cost_source == "estimated", run.partial, catalog)


def labeled_money(
    value: str, cost: float | None, estimated: bool, partial: bool, catalog: Catalog
) -> str:
    if cost is None:
        return value
    if partial:
        from cuanta.domain.messages import msg

        key = "result.partial_estimated_cost" if estimated else "result.partial_cost"
        return catalog.message(msg(key, cost=value))
    return catalog("cost.estimated", cost=value) if estimated else value


def glyph(status: Status) -> str:
    return GLYPHS.get(status, "·")


def status_style(status: Status) -> str:
    styles = {
        Status.OK: "$success",
        Status.WARN: "$warning",
        Status.FAIL: "$error",
        Status.INFO: "$secondary",
        Status.RESUME: "$primary",
    }
    return styles.get(status, "$text-muted")
