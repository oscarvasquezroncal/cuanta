from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest
from textual.pilot import Pilot
from textual.widgets import Static, TabbedContent

from cuanta.domain.mandate import Shape
from cuanta.domain.scout import DocsChoice, DocsReason, ShapeChoice
from cuanta.domain.scout_report import ScoutSummary
from cuanta.tui.app import CuantaApp
from cuanta.tui.i18n import Catalog
from cuanta.tui.scout_text import scout_content
from cuanta.tui.screens.result import ResultScreen
from cuanta.tui.widgets.wizard import MandateWizard
from tests.tui.fakes import TEAM_CATALOG, FakeServices, sample_result
from tests.tui.test_app import drive, make_app, settle
from tests.tui.test_snapshots import SIZES, app_for, loaded, understood
from tests.tui.test_t5_screens import render, wait_for

SnapCompare = Callable[..., bool]

LAUNCH = ScoutSummary(
    mode="launch",
    run_id="RUN0",
    capsule="cap:1a2b3c4d5e6f7a8b",
    source="json",
    status="done",
    tokens=4_812,
    budget=6_000,
    raw_tokens=7_950,
    facts=9,
    snippets=3,
    risks=2,
    tests=("tests/cart.test.ts",),
    edit_set=("src/cart/total.ts", "src/cart/format.ts"),
    dropped_snippets=4,
    leaked=("src/app/layout.tsx",),
    leaks_known=True,
    outside_named=("src/cart/index.ts",),
    outside_unnamed=("src/app/page.tsx",),
    senior_checked=True,
    docs="off",
    docs_reason="not_requested",
)
NATIVE = replace(
    LAUNCH,
    mode="native",
    dropped_snippets=0,
    leaked=(),
    leaks_known=False,
    outside_named=(),
    outside_unnamed=(),
)


@pytest.mark.parametrize("language", ["en", "es"])
def test_the_scout_panel_shows_the_pack_the_edit_set_and_the_flags(language: str) -> None:
    t = Catalog(language)
    text = str(scout_content(t, LAUNCH))
    assert t("scout_panel.title") in text and t("scout_panel.mode_launch") in text
    assert t("scout_panel.pack", tokens="4,812", budget="6,000", facts=9, snippets=3) in text
    assert t("scout_panel.edit_set", paths="src/cart/total.ts, src/cart/format.ts") in text
    assert t("scout_panel.trimmed", snippets=4, facts=0) in text
    assert t("scout_panel.capsule", capsule="cap:1a2b3c4d5e6f7a8b") in text
    assert t("scout_panel.leaks", paths="src/app/layout.tsx") in text
    assert t("scout_panel.named", paths="src/cart/index.ts") in text
    assert t("scout_panel.unnamed", paths="src/app/page.tsx") in text
    reason = t("scout_panel.reason_not_requested")
    assert t("scout_panel.docs_off", reason=reason) in text
    native = str(scout_content(t, NATIVE))
    assert t("scout_panel.mode_native") in native
    assert t("scout_panel.leaks_unknown") in native
    assert t("scout_panel.trimmed", snippets=0, facts=0) not in native


def test_a_docs_only_summary_shows_just_the_docs_line() -> None:
    t = Catalog("en")
    docs = ScoutSummary(docs="on", docs_reason="requested")
    assert docs.shown
    assert str(scout_content(t, docs)) == "Docs on: the request asks for docs"
    assert not ScoutSummary().shown


async def open_scout(app: CuantaApp, pilot: Pilot[None], summary: ScoutSummary) -> Static:
    view = replace(sample_result(), task_type="feature", scout=summary)
    app.push_screen(ResultScreen(app.services, app.catalog, view))
    await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
    await settle(app, pilot)
    app.screen.query_one("#result-tabs", TabbedContent).active = "tab-consumption"
    await settle(app, pilot)
    widget = app.screen.query_one("#result-scout", Static)
    widget.scroll_visible(animate=False)
    await settle(app, pilot)
    return widget


def test_the_result_shows_the_scout_panel_only_for_scouted_runs() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        widget = await open_scout(app, pilot, LAUNCH)
        assert app.catalog("scout_panel.title") in render(widget)
        app.pop_screen()
        app.push_screen(ResultScreen(app.services, app.catalog, sample_result()))
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        await settle(app, pilot)
        assert not app.screen.query("#result-scout")

    drive(make_app(FakeServices()), scenario, size=(120, 46))


@pytest.mark.parametrize("language", ["en", "es"])
@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
def test_scout_result_snapshot(
    snap_compare: SnapCompare, language: str, size: tuple[int, int]
) -> None:
    app = make_app(FakeServices(), language)

    async def shown(pilot: Pilot[None]) -> None:
        await open_scout(app, pilot, LAUNCH)

    assert snap_compare(app, terminal_size=size, run_before=shown)


async def scout_team(pilot: Pilot[None]) -> None:
    wizard = await understood(pilot, 1)
    wizard.kind = "feature"
    wizard.go(2)
    wizard.choose_provider("codex")
    await wait_for(
        pilot,
        lambda: bool(wizard.query("#override-scout")) and wizard.estimate is not None,
    )
    await loaded(pilot)
    await pilot.pause(0.2)
    wizard.query_one("#override-scout").scroll_visible(animate=False, top=True)
    await loaded(pilot)


def scout_services() -> FakeServices:
    return FakeServices(
        engines=(("claude", True), ("codex", True)),
        catalog=TEAM_CATALOG,
        team_shape=ShapeChoice(Shape.SCOUT, False, 0.42),
        team_docs=DocsChoice(False, DocsReason.NOT_REQUESTED),
    )


@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
def test_team_scout_snapshot(snap_compare: SnapCompare, size: tuple[int, int]) -> None:
    app = app_for("calico-dark", scout_services())
    assert snap_compare(app, terminal_size=size, run_before=scout_team)


def test_the_team_step_shows_the_scout_card_and_the_shape_line() -> None:
    services = scout_services()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await scout_team(pilot)
        wizard = app.query_one(MandateWizard)
        assert wizard.query("#override-scout") and not wizard.query("#override-analyst")
        assert not wizard.query("#override-docs")
        line = render(wizard.query_one("#wiz-estimate", Static))
        assert "Shape: scout and senior" in line and "42%" in line
        assert "Docs: off" in line

    drive(make_app(services), scenario, size=(120, 46))


def test_the_run_keeps_the_shape_the_team_step_decided() -> None:
    services = scout_services()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await scout_team(pilot)
        wizard = app.query_one(MandateWizard)
        options = wizard.options()
        assert options.shape == "" and wizard.estimate is not None
        decided = wizard.decided(options)
        assert (decided.shape, decided.scout_mode) == ("scout", "native")
        pins = (("senior", "gpt-6-sol"),)
        pinned = replace(options, route=replace(options.route, role_models=pins))
        assert wizard.decided(pinned) == pinned
        wizard.estimate = replace(wizard.estimate, shape=ShapeChoice(Shape.PIPELINE))
        assert wizard.decided(options).shape == "pipeline"
        simple = replace(options, simple=True)
        assert wizard.decided(simple) == simple
        wizard.refresh_team()
        assert wizard.decided(wizard.options()).shape == ""

    drive(make_app(services), scenario, size=(120, 46))
