from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress

import pytest
from textual.css.query import NoMatches
from textual.pilot import Pilot
from textual.widgets import Log

from cuanta.application.mandate import MandateReport
from cuanta.application.mandate_flow import MandateOptions
from cuanta.domain.engine import EngineEvent
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import msg
from cuanta.domain.progress import ProgressEvent, Status, file_count, finished, started, took
from cuanta.tui.app import CuantaApp
from cuanta.tui.screens.pipeline import PipelineScreen
from tests.tui.fakes import FakeServices, single_context_events
from tests.tui.test_app import drive, make_app
from tests.tui.test_t5_screens import wait_for


class PreparingServices(FakeServices):
    def run_mandate(
        self,
        request: MandateRequest,
        signatures: int,
        options: MandateOptions,
        observer: Callable[[EngineEvent], None],
        progress: Callable[[ProgressEvent], None],
    ) -> MandateReport:
        progress(started("index", msg("progress.index")))
        progress(finished("index", Status.OK, took(52.0, file_count(854)), 52.0))
        progress(started("plan", msg("progress.plan")))
        progress(finished("plan", Status.OK, took(21.0), 21.0))
        report = super().run_mandate(request, signatures, options, observer, progress)
        progress(started("verdict", msg("verify.running", runner="pytest")))
        progress(finished("verdict", Status.OK, msg("mandate.verdict_status", status="green")))
        return report


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("en", ["↺ index", "✓ index · 854 files · 52 s", "↺ plan", "✓ plan · 21 s"]),
        ("es", ["↺ índice", "✓ índice · 854 archivos · 52 s", "↺ plan", "✓ plan · 21 s"]),
    ],
)
def test_the_run_screen_feed_lists_the_slow_preparation_steps(
    language: str, expected: list[str]
) -> None:
    services = PreparingServices(events=single_context_events(), results={})

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        screen = PipelineScreen(
            services,
            app.catalog,
            MandateRequest(type="investigation", what="Check the cart", why="How does it work?"),
            0,
            MandateOptions(),
        )
        app.push_screen(screen)

        def feed() -> list[str]:
            with suppress(NoMatches):
                return list(screen.query_one("#pipeline-feed", Log).lines)
            return []

        await wait_for(pilot, lambda: any("pytest" in line for line in feed()))
        lines = feed()
        assert lines[: len(expected)] == expected
        assert lines[len(expected)] == "pounce started"
        assert any("pytest" in line for line in lines)

    drive(make_app(services, language), scenario)
