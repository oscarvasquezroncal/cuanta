from __future__ import annotations

from textual.content import Content

from cuanta.domain.governor_report import GovernorEntry, GovernorSummary
from cuanta.tui.i18n import Catalog

KINDS = frozenset({"finish_now", "codex_stop", "rotate"})
OUTCOMES = frozenset({"resumed", "salvaged", "rotated", "skipped"})


def action_text(t: Catalog, entry: GovernorEntry) -> str:
    if entry.kind not in KINDS:
        return entry.kind
    if not entry.sent:
        return t(f"governor_panel.{entry.kind}_unsent")
    if entry.outcome in OUTCOMES:
        return t(f"governor_panel.{entry.kind}_{entry.outcome}")
    return t(f"governor_panel.{entry.kind}")


def spent_text(t: Catalog, entry: GovernorEntry) -> str:
    if entry.spent_usd is None:
        return t("spectrum.na")
    value = f"${entry.spent_usd:.4f}"
    return f"{value} ({t('governor_panel.estimate')})" if entry.estimated else value


def reaction_line(t: Catalog, entry: GovernorEntry) -> Content:
    line = t(
        "governor_panel.reaction",
        at=f"{entry.at_s:.1f}",
        role=entry.role,
        action=action_text(t, entry),
        spent=spent_text(t, entry),
        limit=f"${entry.limit_usd:.4f}",
        trigger=t.keyed("governor.trigger", entry.trigger),
    )
    if entry.saved_usd is None:
        return Content(line)
    saved = t("governor_panel.saves", value=f"${entry.saved_usd:.4f}")
    return Content.assemble(line, "  ", (saved, "$success"))


def governor_content(t: Catalog, summary: GovernorSummary) -> Content:
    saved = summary.saved_usd
    value = f"${saved:.4f}" if saved is not None else t("spectrum.na")
    lines = [
        Content.assemble(
            (t("governor_panel.title") + "  ", "bold"),
            t("governor_panel.saved") + " ",
            (value, "$accent"),
        )
    ]
    if summary.reactions:
        lines.extend(reaction_line(t, entry) for entry in summary.reactions)
    else:
        lines.append(Content.styled(t("governor_panel.no_reactions"), "$text-muted"))
    blocked = summary.blocked
    if blocked is not None and blocked.total:
        lines.append(
            Content(
                t(
                    "governor_panel.blocked",
                    reads=blocked.reads,
                    searches=blocked.searches,
                    tests=blocked.tests,
                )
            )
        )
        if blocked.tokens:
            lines.append(
                Content.styled(
                    t("governor_panel.blocked_tokens", tokens=f"{blocked.tokens:,}"),
                    "$text-muted",
                )
            )
    if summary.enforced:
        lines.append(
            Content.styled(
                t("governor_panel.enforced", roles=", ".join(summary.enforced)), "$text-muted"
            )
        )
    if summary.best_effort:
        lines.append(
            Content.styled(
                t("governor_panel.best_effort", roles=", ".join(summary.best_effort)), "$warning"
            )
        )
    return Content("\n").join(lines)
