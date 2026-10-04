from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest
from textual.pilot import Pilot
from textual.widgets import Collapsible, Log, Static

from cuanta.tui.app import CuantaApp
from cuanta.tui.screens.result import ResultScreen
from tests.tui.fakes import sample_result
from tests.tui.test_app import drive, make_app, settle
from tests.tui.test_t5_screens import render, wait_for


def test_result_leads_with_cost_tokens_and_files_and_folds_details() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        app.push_screen(ResultScreen(app.services, app.catalog, sample_result()))
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        await settle(app, pilot)
        assert app.screen.query_one("#result-details", Collapsible).collapsed
        text = render(app.screen.query_one("#result-facts", Static))
        assert "Files changed" in text
        assert "Tokens" in text
        assert "$0.14" in text

    drive(make_app(), scenario)


def test_run_feed_translates_session_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    from cuanta.application.mandate_flow import MandateOptions
    from cuanta.domain.engine import SessionStarted
    from cuanta.domain.mandate import MandateRequest
    from cuanta.tui.screens.pipeline import PipelineScreen

    monkeypatch.setattr(PipelineScreen, "execute", lambda self: None)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        screen = PipelineScreen(
            app.services, app.catalog, MandateRequest("bug", "Fix", "Evidence"), 0, MandateOptions()
        )
        app.push_screen(screen)
        await settle(app, pilot)
        screen.apply(SessionStarted("fixture", "claude-sonnet-5"))
        assert "sesión iniciada" in "\n".join(screen.query_one("#pipeline-feed", Log).lines)

    drive(make_app(language="es"), scenario)


def test_partial_attempt_marks_tokens_as_partial() -> None:
    app = make_app(language="es")
    view = replace(sample_result(), actual_partial=True)
    text = str(ResultScreen(app.services, app.catalog, view)._primary())
    assert "Tokens: 53,107 (parcial)" in text


def test_failure_dialog_keeps_copyable_configuration_after_its_three_lines() -> None:
    from cuanta.domain.errors import CuantaError
    from cuanta.tui.screens.failure import FailureScreen

    error = CuantaError("Too many files", 'Exclude these folders\n[detect]\nexclude = [".venv"]')

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        app.push_screen(FailureScreen(app.catalog, error))
        await settle(app, pilot)
        assert len(render(app.screen.query_one("#confirm-body", Static)).splitlines()) == 3
        assert render(app.screen.query_one("#failure-hint", Static)).splitlines() == [
            "[detect]",
            'exclude = [".venv"]',
        ]

    drive(make_app(), scenario)


@pytest.mark.parametrize("language", ["es", "en"])
@pytest.mark.parametrize("size", [(120, 36), (80, 24)], ids=["wide", "narrow"])
@pytest.mark.parametrize("view", ["run", "result", "dialog"])
def test_readable_app_snapshots(
    snap_compare: Callable[..., bool],
    monkeypatch: pytest.MonkeyPatch,
    language: str,
    size: tuple[int, int],
    view: str,
) -> None:
    from cuanta.application.mandate_flow import MandateOptions
    from cuanta.domain.errors import CuantaError
    from cuanta.domain.mandate import MandateRequest
    from cuanta.tui.screens.failure import FailureScreen
    from cuanta.tui.screens.pipeline import PipelineScreen
    from tests.tui.fakes import pipeline_events

    monkeypatch.setattr(PipelineScreen, "execute", lambda self: None)

    async def shown(pilot: Pilot[None]) -> None:
        app = pilot.app
        assert isinstance(app, CuantaApp)
        await settle(app, pilot)
        if view == "result":
            app.push_screen(ResultScreen(app.services, app.catalog, sample_result()))
        elif view == "dialog":
            error = CuantaError(app.catalog("failure.unexpected"))
            error.log_path = ".cuanta/logs/2026-10-04.log"
            app.push_screen(FailureScreen(app.catalog, error))
        else:
            screen = PipelineScreen(
                app.services,
                app.catalog,
                MandateRequest("bug", "Fix totals", "AssertionError"),
                0,
                MandateOptions(),
                app.clock,
            )
            app.push_screen(screen)
            await settle(app, pilot)
            for event in pipeline_events()[:6]:
                screen.apply(event)
            screen._live.last = None
            screen._tick()
        await settle(app, pilot)

    assert snap_compare(
        CuantaApp(
            make_app().services,
            language,
            "calico-dark",
            motion=False,
            environ={"WT_SESSION": "1"},
            clock=lambda: 0.0,
        ),
        terminal_size=size,
        run_before=shown,
    )
