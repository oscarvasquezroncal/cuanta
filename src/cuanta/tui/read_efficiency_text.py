from __future__ import annotations

from textual.content import Content

from cuanta.domain.read_efficiency import ReadEfficiency
from cuanta.tui.i18n import Catalog

FORMULA_KEYS = {
    "cited files read / files read": "read_efficiency.formula_investigation",
    "(edited or cited) files read / files read": "read_efficiency.formula_code",
    "useful tool tokens / total API tokens": "read_efficiency.formula_v1",
}


def utilization_note(t: Catalog, label: str, formula: str) -> str:
    version = t(
        "read_efficiency.label_v2" if label == "file utilization v2" else "spectrum.heuristic_label"
    )
    expression = t(FORMULA_KEYS[formula]) if formula in FORMULA_KEYS else formula
    return t("read_efficiency.version_formula", version=version, formula=expression)


def read_efficiency_content(t: Catalog, report: ReadEfficiency) -> Content:
    value = f"{report.value:.0%}" if report.value is not None else t("spectrum.na")
    lines = [
        Content.assemble(
            (t("read_efficiency.title") + "  ", "bold"),
            (value, "$accent"),
            "  " + t("read_efficiency.counts", useful=report.useful_count, read=report.read_count),
        ),
        Content.styled(utilization_note(t, report.label, report.formula), "$text-muted"),
    ]
    if report.value is None:
        lines.append(Content.styled(t("read_efficiency.unknown"), "$text-muted"))
    return Content("\n").join(lines)
