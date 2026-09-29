from __future__ import annotations

from datetime import datetime

from textual.content import Content

from cuanta.domain.cache import (
    CacheState,
    FirstRequestCache,
    PrefixState,
    PrefixWindow,
)
from cuanta.domain.queue import warm_known, warm_message
from cuanta.tui.i18n import Catalog


def clock_time(moment: datetime) -> str:
    return moment.astimezone().strftime("%H:%M")


def first_request_text(t: Catalog, cache: FirstRequestCache | None) -> str:
    if cache is None:
        return t("cache.first_unknown")
    if cache.state is CacheState.WARM:
        return t("cache.first_warm", read=f"{cache.read:,}", share=f"{cache.share:.0%}")
    return t("cache.first_cold", share=f"{cache.share:.0%}")


def first_request_content(t: Catalog, cache: FirstRequestCache | None) -> Content:
    style = "$text-muted"
    if cache is not None:
        style = "$success" if cache.state is CacheState.WARM else "$warning"
    return Content.styled(first_request_text(t, cache), style)


def prefix_content(t: Catalog, window: PrefixWindow) -> Content:
    if window.until is None or window.state is PrefixState.UNKNOWN:
        return Content.styled(t("cache.prefix_unknown"), "$text-muted")
    key = "cache.prefix_warm" if window.state is PrefixState.WARM else "cache.prefix_cold"
    style = "$success" if window.state is PrefixState.WARM else "$warning"
    return Content.styled(t(key, time=clock_time(window.until)), style)


def queue_unreadable_content(t: Catalog) -> Content:
    return Content.assemble(
        (t("home.queue_unreadable"), "$warning"),
        "\n",
        (t("home.queue_list_command"), "$accent"),
    )


def queue_content(t: Catalog, count: int, window: PrefixWindow) -> Content:
    style = "$text-muted"
    if warm_known(window):
        style = "$success" if window.state is PrefixState.WARM else "$warning"
    time = clock_time(window.until) if window.until is not None else ""
    return Content.assemble(
        (t("home.queue", count=count), "bold"),
        " · ",
        (t.message(warm_message(window, time)), style),
        "\n",
        (t("home.queue_command"), "$accent"),
    )
