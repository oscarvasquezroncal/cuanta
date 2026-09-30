from __future__ import annotations

from collections.abc import Callable

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, Select, Static

from cuanta.application.implementer import ImplementationSession
from cuanta.domain.engine import RunResult
from cuanta.tui.app import CuantaApp
from cuanta.tui.i18n import Catalog
from cuanta.tui.implementation_text import implementation_content
from tests.tui.fakes import FakeServices
from tests.tui.test_app import drive, make_app
from tests.tui.test_snapshots import loaded
from tests.tui.test_wizard import open_wizard, tell


@pytest.mark.parametrize("language", ["en", "es"])
@pytest.mark.parametrize("size", [(120, 36), (80, 24)], ids=("wide", "narrow"))
def test_fast_team_snapshot(
    snap_compare: Callable[..., bool], language: str, size: tuple[int, int]
) -> None:
    async def fast_team(pilot: Pilot[None]) -> None:
        app = pilot.app
        assert isinstance(app, CuantaApp)
        await loaded(pilot)
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, "Add a badge to the cart. Don't touch payments.")
        wizard.go(2)
        assert not wizard.query_one("#implementation-fast", Button).disabled
        wizard.query_one("#implementation-fast", Button).press()
        await pilot.pause(0.2)
        await loaded(pilot)
        assert wizard.options().profile == "fast"
        assert not wizard.query_one("#team-cards").display
        assert wizard.query_one("#implementation-options").region.height >= 9
        assert wizard.query_one("#implementation-fast").has_class("-current")
        wizard.query_one("#implementation-row").scroll_visible(animate=False, top=True)
        await loaded(pilot)

    app = CuantaApp(
        FakeServices(),
        language,
        "calico-dark",
        motion=False,
        environ={"WT_SESSION": "1"},
        clock=lambda: 0.0,
    )
    assert snap_compare(app, terminal_size=size, run_before=fast_team)


def test_fast_chip_preserves_the_selected_model_and_variant() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, "Add a badge to the cart. Don't touch payments.")
        wizard.query_one("#implementation-fast", Button).press()
        await pilot.pause()
        wizard.query_one("#wiz-implementation-model", Select).value = "claude-opus-5-5"
        wizard.query_one("#wiz-implementation-variant", Select).value = "fast-low"
        await pilot.pause()
        options = wizard.options()
        assert options.profile == "fast"
        assert options.model == "claude-opus-5-5"
        assert options.variant == "fast-low"
        assert not wizard.per_role
        depth = str(wizard.query_one("#wiz-depth-note", Static).render())
        assert "claude-opus-5-5" in depth and "medium effort" not in depth

    drive(make_app(FakeServices()), scenario, size=(120, 50))


def test_result_lists_steps_and_preexisting_errors() -> None:
    report: dict[str, object] = {
        "passed": False,
        "repairs": 3,
        "reason": "round_limit",
        "steps": [{"title": "Add badge", "state": "red"}],
        "checks": [{"introduced": ["badge.ts:4 new"], "preexisting": ["old.ts:2 existing"]}],
    }
    content = str(implementation_content(Catalog("en"), report))
    assert "Add badge: red" in content
    assert "New error: badge.ts:4 new" in content
    assert "Pre-existing error: old.ts:2 existing" in content


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("time_limit", "límite de tiempo"),
        ("cost_limit", "límite de presupuesto"),
        ("planning_changes", "se cambiaron archivos antes de autorizar un paso"),
    ],
)
def test_spanish_stop_reason_and_unused_repair_rounds(reason: str, expected: str) -> None:
    report: dict[str, object] = {
        "passed": False,
        "repairs": 1,
        "repair_limit": 3,
        "repair_rounds_remaining": 2,
        "reason": reason,
        "steps": [{"title": "Cambio", "state": "failed"}, {"title": "Prueba", "state": "blocked"}],
    }
    content = str(implementation_content(Catalog("es"), report))
    assert "1/3 · 2 restantes" in content
    assert "Cambio: fallida" in content
    assert "Prueba: bloqueada" in content
    assert expected in content


def test_an_engine_budget_stop_is_shown_from_the_catalog() -> None:
    session = ImplementationSession(
        ("npm run lint",), (), lambda commands, stopped: (), lambda: 0.0
    )
    session.on_result(RunResult(False, "error_max_budget_usd", 1.0, 1, "S"), lambda _: True)
    content = str(implementation_content(Catalog("es"), session.report.payload()))
    assert "Detenido: límite de presupuesto alcanzado" in content
    assert "error_max_budget_usd" not in content
