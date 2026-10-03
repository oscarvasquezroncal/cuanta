from __future__ import annotations

from collections.abc import Callable

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, Select, Static

from cuanta.application.implementer import ImplementationSession
from cuanta.domain.engine import RunResult
from cuanta.domain.ledger import Run
from cuanta.domain.stop_reason import stop_message
from cuanta.tui.app import CuantaApp
from cuanta.tui.i18n import Catalog
from cuanta.tui.implementation_text import implementation_content
from tests.tui.fakes import FakeServices
from tests.tui.test_app import drive, make_app
from tests.tui.test_snapshots import loaded
from tests.tui.test_t5_screens import wait_for
from tests.tui.test_wizard import current, open_wizard, tell


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
        assert "Opus 5.5 · Fast output · low (Opus)" in depth and "medium effort" not in depth

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
        ("time_limit", "se detuvo por el límite de tiempo de 900 s"),
        ("cost_limit", "se detuvo en el tope de gasto de $1.00"),
        ("planning_changes", "se detuvo porque se cambiaron archivos antes de autorizar un paso"),
    ],
)
def test_spanish_stop_reason_and_unused_repair_rounds(reason: str, expected: str) -> None:
    report: dict[str, object] = {
        "passed": False,
        "repairs": 1,
        "repair_limit": 3,
        "repair_rounds_remaining": 2,
        "reason": reason,
        "time_limit_s": 900.0,
        "cap_usd": 1.0,
        "steps": [
            {"title": "Cambio", "state": "failed"},
            {"title": "Prueba", "state": "blocked"},
            {"title": "Docs", "state": "running"},
        ],
    }
    spanish = Catalog("es")
    content = str(implementation_content(spanish, report))
    assert "1/3 · 2 restantes" in content
    assert "Cambio: fallida" in content
    assert "Prueba: bloqueada" in content
    assert "Docs: detenida" in content and "en curso" not in content
    assert "en curso" in str(implementation_content(spanish, report, finished=False))
    stop = stop_message(Run("R", "mandate", status="failed"), report)
    assert spanish.message(stop) == expected


def test_an_engine_budget_stop_is_shown_from_the_catalog() -> None:
    session = ImplementationSession(
        ("npm run lint",), (), lambda commands, stopped: (), lambda: 0.0, cap_usd=1.0
    )
    session.on_result(RunResult(False, "error_max_budget_usd", 1.0, 1, "S"), lambda _: True)
    payload = session.report.payload()
    spanish = Catalog("es")
    text = spanish.message(stop_message(Run("R", "mandate", status="failed"), payload))
    assert text == "se detuvo en el tope de gasto de $1.00"
    assert "error_max_budget_usd" not in str(implementation_content(spanish, payload))
    uncapped = spanish.message(
        stop_message(Run("R", "mandate", status="failed"), {**payload, "cap_usd": 0.0})
    )
    assert "tope de gasto" in uncapped


def test_auto_profile_shows_fast_for_a_claude_change_and_the_default_of_its_kind() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)

        def fast() -> bool:
            return wizard.fast_profile

        await tell(wizard, pilot, "Add a badge to the cart. Don't touch payments.")
        await pilot.pause()
        assert wizard.kind == "feature" and fast()
        assert wizard.query_one("#implementation-fast").has_class("-current")
        assert not wizard.query_one("#implementation-balanced").has_class("-current")
        assert not wizard.per_role
        options = wizard.options()
        assert (options.profile, options.model, options.variant) == ("auto", "", "")
        depth = str(wizard.query_one("#wiz-depth-note", Static).render())
        assert "Opus 5.5 · Low effort for features" in depth
        wizard.query_one("#wiz-implementation-variant", Select).value = "high"
        await pilot.pause()
        assert wizard.options().variant == "high"
        depth = str(wizard.query_one("#wiz-depth-note", Static).render())
        assert "Opus 5.5 · High effort for features" in depth
        wizard.kind = "refactor"
        assert not fast()
        wizard.kind = "bug"
        wizard.simple = True
        assert not fast()
        assert (wizard.options().model, wizard.options().variant) == ("", "")
        wizard.query_one("#implementation-balanced", Button).press()
        await pilot.pause()
        wizard.simple = False
        assert not fast() and wizard.options().profile == "balanced"

    drive(make_app(FakeServices(profile="auto")), scenario, size=(120, 50))


def test_config_role_pins_keep_the_auto_profile_on_the_balanced_team() -> None:
    services = FakeServices(profile="auto", role_pins=True)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)

        def fast() -> bool:
            return wizard.fast_profile

        await wait_for(pilot, lambda: wizard.role_pins)
        await tell(wizard, pilot, "Add a badge to the cart. Don't touch payments.")
        assert wizard.kind == "feature" and not fast()
        assert wizard.query_one("#implementation-balanced").has_class("-current")
        assert not wizard.query_one("#implementation-fast").has_class("-current")
        assert wizard.query_one("#implementation-auto").has_class("-on")
        assert wizard.query_one("#implementation-options").display
        assert wizard.query_one("#wiz-pure-row").display
        assert not wizard.query_one("#wiz-implementation-pure-note").display
        wizard.go(2)
        assert current(wizard) == "team"
        await wait_for(
            pilot, lambda: wizard.plan is not None and bool(wizard.query("#override-senior"))
        )
        assert wizard.query_one("#team-cards").display
        note = str(wizard.query_one("#wiz-provider-note", Static).render())
        assert note.startswith("One Claude session: every role runs inside it")
        options = wizard.options()
        assert (options.profile, options.model, options.variant) == ("auto", "", "")
        assert services.team_options[-1].profile == "auto"
        wizard.query_one("#implementation-fast", Button).press()
        await wait_for(pilot, fast)
        assert wizard.options().profile == "fast"

    drive(make_app(services), scenario, size=(120, 50))
