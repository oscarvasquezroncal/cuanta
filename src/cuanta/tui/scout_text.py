from __future__ import annotations

from textual.content import Content

from cuanta.domain.scout_report import ScoutSummary
from cuanta.tui.i18n import Catalog

SHOWN_PATHS = 5
MODES = frozenset({"native", "launch"})
REASONS = frozenset({"requested", "not_requested", "trial", "forced_on", "forced_off", "pinned"})


def paths_text(paths: tuple[str, ...]) -> str:
    shown = ", ".join(paths[:SHOWN_PATHS])
    more = len(paths) - SHOWN_PATHS
    return f"{shown} (+{more})" if more > 0 else shown


def _title(t: Catalog, summary: ScoutSummary) -> Content:
    head = Content.styled(t("scout_panel.title"), "bold")
    if summary.mode not in MODES:
        return head
    return Content.assemble(head, "  ", (t(f"scout_panel.mode_{summary.mode}"), "$accent"))


def _pack_lines(t: Catalog, summary: ScoutSummary) -> list[Content]:
    lines = [
        Content(
            t(
                "scout_panel.pack",
                tokens=f"{summary.tokens:,}",
                budget=f"{summary.budget:,}",
                facts=summary.facts,
                snippets=summary.snippets,
            )
        )
    ]
    if not summary.dispatched:
        lines.append(Content.styled(t("scout_panel.not_dispatched"), "$warning"))
    elif summary.source == "fallback":
        lines.append(Content.styled(t("scout_panel.fallback"), "$warning"))
    if summary.trimmed:
        lines.append(
            Content.styled(
                t(
                    "scout_panel.trimmed",
                    snippets=summary.dropped_snippets,
                    facts=summary.dropped_facts,
                ),
                "$text-muted",
            )
        )
    if summary.over_budget:
        lines.append(Content.styled(t("scout_panel.over_budget"), "$warning"))
    if summary.edit_from_plan:
        lines.append(Content.styled(t("scout_panel.edit_from_plan"), "$warning"))
    lines.append(Content(t("scout_panel.edit_set", paths=paths_text(summary.edit_set) or "-")))
    if summary.capsule:
        lines.append(
            Content.styled(t("scout_panel.capsule", capsule=summary.capsule), "$text-muted")
        )
    return lines


def _senior_lines(t: Catalog, summary: ScoutSummary) -> list[Content]:
    if not summary.senior_checked:
        return []
    if not summary.leaks_known:
        lines = [Content.styled(t("scout_panel.leaks_unknown"), "$text-muted")]
    elif summary.leaked:
        lines = [
            Content.styled(t("scout_panel.leaks", paths=paths_text(summary.leaked)), "$warning")
        ]
    else:
        lines = [Content.styled(t("scout_panel.no_leaks"), "$text-muted")]
    if summary.outside_named:
        lines.append(Content(t("scout_panel.named", paths=paths_text(summary.outside_named))))
    if summary.outside_unnamed:
        lines.append(
            Content.styled(
                t("scout_panel.unnamed", paths=paths_text(summary.outside_unnamed)), "$warning"
            )
        )
    return lines


def _docs_line(t: Catalog, summary: ScoutSummary) -> list[Content]:
    if summary.docs not in {"on", "off"} or summary.docs_reason not in REASONS:
        return []
    reason = t(f"scout_panel.reason_{summary.docs_reason}")
    return [Content.styled(t(f"scout_panel.docs_{summary.docs}", reason=reason), "$text-muted")]


def scout_content(t: Catalog, summary: ScoutSummary) -> Content:
    lines: list[Content] = []
    if summary.mode:
        lines.append(_title(t, summary))
        lines.extend(_pack_lines(t, summary))
        lines.extend(_senior_lines(t, summary))
    lines.extend(_docs_line(t, summary))
    return Content("\n").join(lines)
