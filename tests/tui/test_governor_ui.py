from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, Static, TabbedContent

from cuanta.domain.governor_report import (
    BEST_EFFORT,
    HOOKS,
    BlockedCalls,
    GovernorEntry,
    GovernorSummary,
)
from cuanta.tui.app import CuantaApp
from cuanta.tui.governor_text import governor_content
from cuanta.tui.i18n import Catalog
from cuanta.tui.screens.result import ResultScreen
from tests.tui.fakes import FakeServices, sample_result
from tests.tui.test_app import drive, make_app, settle
from tests.tui.test_t5_screens import render, wait_for

SnapCompare = Callable[..., bool]

CLAUDE_TEAM = GovernorSummary(
    (
        GovernorEntry(
            "rotate",
            "analyst",
            "fresh_session",
            31.2,
            "RUN1",
            True,
            0.12,
            0.3,
            False,
            0.224,
            "rotated",
        ),
        GovernorEntry(
            "finish_now", "senior", "headroom", 142.8, "RUN2", True, 0.825, 0.92, False, None
        ),
    ),
    BlockedCalls(reads=3, searches=1, tests=2, tokens=12_500),
    (("analyst", HOOKS), ("senior", HOOKS), ("tester", HOOKS)),
)
GPT_TEAM = GovernorSummary(
    (
        GovernorEntry(
            "codex_stop",
            "senior",
            "share",
            456.0,
            "RUN3",
            True,
            0.414,
            0.4785,
            True,
            1.3404,
            "resumed",
        ),
    ),
    None,
    (("senior", BEST_EFFORT),),
)


@pytest.mark.parametrize("language", ["en", "es"])
def test_the_panel_lists_reactions_savings_and_blocked_calls(language: str) -> None:
    t = Catalog(language)
    text = str(governor_content(t, CLAUDE_TEAM))
    assert t("governor_panel.title") in text
    assert "$0.2240" in text and t("governor_panel.rotate_rotated") in text
    assert "+142.8 s" in text and t("governor_panel.finish_now") in text
    assert t.keyed("governor.trigger", "headroom") in text
    assert t("governor_panel.blocked", reads=3, searches=1, tests=2) in text
    assert "12,500" in text
    assert t("governor_panel.enforced", roles="analyst, senior, tester") in text
    assert t("governor_panel.best_effort", roles="senior") not in text


@pytest.mark.parametrize("language", ["en", "es"])
def test_a_codex_stop_is_labelled_an_estimate_and_its_discipline_best_effort(
    language: str,
) -> None:
    t = Catalog(language)
    text = str(governor_content(t, GPT_TEAM))
    assert t("governor_panel.codex_stop_resumed") in text
    assert f"$0.4140 ({t('governor_panel.estimate')})" in text
    assert t("governor_panel.saves", value="$1.3404") in text
    assert t("governor_panel.best_effort", roles="senior") in text
    assert t("governor_panel.blocked", reads=0, searches=0, tests=0) not in text


@pytest.mark.parametrize(
    ("language", "plurals"),
    [("en", ("1 large reads", "1 whole-tree searches")), ("es", ("1 lecturas", "1 búsquedas"))],
)
def test_single_blocked_calls_read_without_a_wrong_plural(
    language: str, plurals: tuple[str, ...]
) -> None:
    t = Catalog(language)
    line = t("governor_panel.blocked", reads=1, searches=1, tests=1)
    assert line.count("1") == 3
    assert not any(plural in line for plural in plurals)


def test_a_quiet_governor_says_so_and_an_unknown_saving_stays_na() -> None:
    t = Catalog("en")
    quiet = GovernorSummary((), BlockedCalls(), (("senior", HOOKS),))
    text = str(governor_content(t, quiet))
    assert t("governor_panel.no_reactions") in text
    assert f"{t('governor_panel.saved')} {t('spectrum.na')}" in text
    unsent = replace(CLAUDE_TEAM, reactions=(replace(CLAUDE_TEAM.reactions[1], sent=False),))
    assert t("governor_panel.finish_now_unsent") in str(governor_content(t, unsent))


async def open_governor(app: CuantaApp, pilot: Pilot[None], summary: GovernorSummary) -> Static:
    view = replace(sample_result(), task_type="feature", governor=summary)
    app.push_screen(ResultScreen(app.services, app.catalog, view))
    await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
    await settle(app, pilot)
    app.screen.query_one("#result-tabs", TabbedContent).active = "tab-consumption"
    await settle(app, pilot)
    widget = app.screen.query_one("#result-governor", Static)
    widget.scroll_visible(animate=False)
    await settle(app, pilot)
    return widget


@pytest.mark.parametrize("size", [(80, 24), (80, 46), (120, 46)])
def test_the_panel_fits_the_consumption_tab(size: tuple[int, int]) -> None:
    width, height = size

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        widget = await open_governor(app, pilot, CLAUDE_TEAM)
        text = render(widget)
        assert app.catalog("governor_panel.title") in text
        assert widget.region.x >= 0 and widget.region.right <= width
        assert widget.region.height > 0 and widget.region.y >= 0
        assert widget.region.bottom <= height - 1
        assert app.screen.query_one("#result-save", Button).display

    drive(make_app(FakeServices()), scenario, size=size)


def test_a_result_without_a_governor_has_no_panel() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        app.push_screen(ResultScreen(app.services, app.catalog, sample_result()))
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        await settle(app, pilot)
        assert not app.screen.query("#result-governor")

    drive(make_app(FakeServices()), scenario)


@pytest.mark.parametrize("language", ["en", "es"])
@pytest.mark.parametrize("size", [(120, 36), (80, 24)])
def test_governor_snapshot(snap_compare: SnapCompare, language: str, size: tuple[int, int]) -> None:
    app = make_app(FakeServices(), language)

    async def shown(pilot: Pilot[None]) -> None:
        await open_governor(app, pilot, CLAUDE_TEAM)

    assert snap_compare(app, terminal_size=size, run_before=shown)
