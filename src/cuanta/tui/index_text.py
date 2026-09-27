from __future__ import annotations

from textual.content import Content

from cuanta.domain.index_metrics import IndexMetrics
from cuanta.tui.i18n import Catalog


def index_lines(t: Catalog, metrics: IndexMetrics) -> tuple[str, ...]:
    rate = (
        f"{metrics.index_hit_rate:.0%}" if metrics.index_hit_rate is not None else t("spectrum.na")
    )
    return (
        t("index_metrics.hit_rate", value=rate),
        t(
            "index_metrics.exploration",
            index=metrics.index_calls,
            raw=metrics.raw_reads,
            total=metrics.exploration_calls,
        ),
        t("index_metrics.tokens_estimate", count=f"{metrics.exploration_tokens_estimate:,}"),
        t("index_metrics.stale", count=metrics.stale_facts),
        t("index_metrics.guard", count=len(metrics.guard_violations)),
        t("index_metrics.out_of_plan", count=len(metrics.out_of_plan_edits)),
    )


def index_content(t: Catalog, metrics: IndexMetrics) -> Content:
    return Content("\n").join(
        (
            Content.styled(t("index_metrics.title"), "bold"),
            *(Content(line) for line in index_lines(t, metrics)),
        )
    )
