from __future__ import annotations

from textual.content import Content

from cuanta.domain.cost_trend import TrendRow, sparkline
from cuanta.domain.run_metrics import Reactions, RunMetrics
from cuanta.tui.chips import MIX_KEYS, TYPE_KEYS
from cuanta.tui.fmt import cost_money
from cuanta.tui.i18n import Catalog


def usd(t: Catalog, value: float | None, estimated: bool = False) -> str:
    if value is None:
        return t("spectrum.na")
    shown = f"{'-' if value < 0 else ''}${abs(value):,.4f}"
    return t("cost.estimated", cost=shown) if estimated else shown


def count(t: Catalog, value: int | None) -> str:
    return t("spectrum.na") if value is None else f"{value:,}"


def tokens(t: Catalog, value: float | None) -> str:
    if value is None:
        return t("spectrum.na")
    return t("metrics_panel.tokens", count=f"{value:,.0f}")


def share(t: Catalog, value: float | None) -> str:
    return t("spectrum.na") if value is None else f"{value:.0%}"


def _saved(t: Catalog, reactions: Reactions | None) -> tuple[str, str]:
    if reactions is None:
        return t("spectrum.na"), t("spectrum.na")
    return str(reactions.count), usd(t, reactions.saved_usd)


def bucket_line(t: Catalog, metrics: RunMetrics) -> str:
    parts = [
        t(
            "metrics_panel.bucket",
            name=t(f"metrics_panel.bucket_{item.name}"),
            forecast=count(t, item.forecast),
            actual=count(t, item.actual),
        )
        for item in metrics.buckets
    ]
    return t("metrics_panel.buckets", buckets=" · ".join(parts))


def metrics_content(t: Catalog, metrics: RunMetrics) -> Content:
    blocked = metrics.blocked
    finishes, finish_saved = _saved(t, metrics.finishes)
    rotations, rotation_saved = _saved(t, metrics.rotations)
    lines = [
        Content.styled(t("metrics_panel.title"), "bold"),
        Content(
            t(
                "metrics_panel.forecast",
                p50=usd(t, metrics.p50_usd),
                p90=usd(t, metrics.p90_usd),
                actual=usd(t, metrics.actual_usd, metrics.estimated),
                cap=usd(t, metrics.cap_usd),
            )
        ),
        Content(
            t(
                "metrics_panel.margin",
                used=share(t, metrics.cap_used),
                left=usd(t, metrics.p90_left_usd),
            )
        ),
        Content.styled(bucket_line(t, metrics), "$text-muted"),
        Content(
            t(
                "metrics_panel.accepted",
                cost=usd(t, metrics.per_accepted_usd, metrics.estimated),
                tokens=tokens(t, metrics.tokens_per_line),
            )
        ),
        Content(
            t(
                "metrics_panel.blocked",
                reads=count(t, blocked.reads if blocked is not None else None),
                tokens=tokens(t, blocked.tokens if blocked is not None else None),
            )
        ),
        Content(
            t(
                "metrics_panel.reactions",
                finishes=finishes,
                finish_saved=finish_saved,
                rotations=rotations,
                rotation_saved=rotation_saved,
            )
        ),
        Content(
            t(
                "metrics_panel.warm",
                first=share(t, metrics.first_warm_share),
                overall=share(t, metrics.warm_share),
            )
        ),
        Content(
            t(
                "metrics_panel.scout",
                pack=tokens(t, metrics.pack_tokens),
                senior=tokens(t, metrics.senior_input_tokens),
            )
        ),
    ]
    return Content("\n").join(lines)


def provider_label(t: Catalog, key: str) -> str:
    return t(MIX_KEYS.get(key, "costs.mix_other"))


def type_label(t: Catalog, key: str) -> str:
    return t(TYPE_KEYS.get(key, "costs.type_untyped"))


def trend_cells(t: Catalog, row: TrendRow) -> tuple[str, ...]:
    return (
        provider_label(t, row.provider),
        type_label(t, row.task_type),
        str(row.runs),
        str(row.accepted),
        cost_money(row.per_accepted_usd, t("spectrum.na"), row.lower_bound, row.estimated),
        sparkline(row.series),
    )
