from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, TextArea
from textual.widgets._toast import Toast

from cuanta.application.mandate_flow import MandateOptions
from cuanta.domain import index_limit
from cuanta.domain.errors import CuantaError
from cuanta.domain.index_limit import IndexTooLarge, oversized_index
from cuanta.domain.index_rebuild import IndexBusy
from cuanta.domain.mandate import MandateRequest
from cuanta.tui.app import CuantaApp
from cuanta.tui.i18n import Catalog
from cuanta.tui.screens.pipeline import PipelineScreen
from cuanta.tui.screens.result import ResultScreen
from cuanta.tui.services import ContainerServices
from cuanta.tui.views.init import InitView
from cuanta.tui.views.loop import LoopView
from cuanta.tui.views.mandate import MandateView
from cuanta.tui.views.map import MapView
from cuanta.tui.views.spectrum import SpectrumView
from cuanta.tui.views.tests import TestsView
from tests.tui.fakes import FakeServices, sample_result
from tests.tui.test_app import drive, make_app, settle
from tests.tui.test_t5_screens import wait_for
from tests.tui.test_wizard import current, open_wizard, tell

PASTE = ("[detect]", 'exclude = [".odoo_ref"]')
FAILURE_HANDLERS: dict[str, Callable[[CuantaApp], Callable[[str, str], None]]] = {
    "init": lambda app: app.query_one(InitView)._failed,
    "loop": lambda app: app.query_one(LoopView)._failed,
    "spectrum": lambda app: app.query_one(SpectrumView)._failed,
    "tests": lambda app: app.query_one(TestsView)._failed,
}
BUG = "Fix the cart total: AssertionError: [total] expected 10 got 12. Don't touch payments."


def too_large() -> IndexTooLarge:
    oversized = oversized_index(tuple(f".odoo_ref/m{number}.py" for number in range(8)), (), 5)
    assert oversized is not None
    return IndexTooLarge(oversized)


def shown(app: CuantaApp) -> list[str]:
    return [Toast(note).render().plain for note in app._notifications]


def pasted(app: CuantaApp) -> bool:
    return any(all(line in text.splitlines() for line in PASTE) for text in shown(app))


def test_a_preview_above_the_limit_shows_the_lines_to_paste() -> None:
    services = FakeServices(layout="one_page", service_error=too_large())

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        wizard.query_one("#wiz-story", TextArea).text = BUG
        wizard.query_one("#wiz-understand", Button).press()
        await wait_for(pilot, lambda: wizard.kind == "bug")
        wizard.query_one("#wiz-preview", Button).press()
        await wait_for(pilot, lambda: bool(app._notifications))
        await wait_for(pilot, lambda: pasted(app))
        assert any("Largest folders: .odoo_ref (8)" in text for text in shown(app))

    drive(make_app(services), scenario, size=(120, 50))


def test_a_run_above_the_limit_shows_the_lines_to_paste() -> None:
    services = FakeServices(service_error=too_large())

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        screen = PipelineScreen(
            services,
            app.catalog,
            MandateRequest(type="investigation", what="Check the cart", why="How does it work?"),
            0,
            MandateOptions(),
        )
        app.push_screen(screen)
        await wait_for(pilot, lambda: bool(app._notifications))
        await wait_for(pilot, lambda: pasted(app))

    drive(make_app(services), scenario)


@pytest.mark.parametrize("section", sorted(FAILURE_HANDLERS))
def test_every_failure_toast_keeps_bracketed_hints(section: str) -> None:
    failure = too_large()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await app.action_goto(section)
        await settle(app, pilot)
        failed = FAILURE_HANDLERS[section](app)
        failed(failure.message, failure.hint)
        await wait_for(pilot, lambda: pasted(app))

    drive(make_app(FakeServices()), scenario)


def test_a_failed_failure_load_keeps_bracketed_hints() -> None:
    services = FakeServices(service_error=too_large())

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await open_wizard(app, pilot)
        app.query_one(MandateView).load_failure()
        await wait_for(pilot, lambda: pasted(app))

    drive(make_app(services), scenario)


def test_a_failed_decision_keeps_bracketed_hints() -> None:
    services = FakeServices(service_error=too_large())

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        screen = ResultScreen(app.services, app.catalog, sample_result())
        app.push_screen(screen)
        await wait_for(pilot, lambda: app.screen is screen)
        await settle(app, pilot)
        screen.decide(True)
        await wait_for(pilot, lambda: pasted(app))

    drive(make_app(services), scenario, size=(120, 40))


def test_the_map_shows_the_lines_to_paste_when_the_index_stops() -> None:
    services = FakeServices(service_error=too_large())

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("g")
        await wait_for(pilot, lambda: bool(app.query(MapView)))
        await wait_for(pilot, lambda: pasted(app))
        assert any("Largest folders: .odoo_ref (8)" in text for text in shown(app))

    drive(make_app(services), scenario)


def test_the_map_rebuild_above_the_limit_leaves_the_stored_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for number in range(8):
        target = tmp_path / ".odoo_ref" / f"m{number}.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"value = {number}\n", encoding="utf-8")
    services = ContainerServices(tmp_path)
    assert services.map_status().status.files == 8
    monkeypatch.setattr(index_limit, "INDEX_FILE_LIMIT", 5)
    with pytest.raises(IndexTooLarge):
        services.map_status(True)
    with closing(sqlite3.connect(tmp_path / ".cuanta" / "index.db")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM files").fetchone() == (8,)


@pytest.mark.parametrize(
    ("failure", "spanish", "english"),
    [
        (
            IndexBusy(".cuanta/index.db"),
            (
                ".cuanta/index.db está abierto en otro proceso, así que no se reconstruyó",
                "vuelve a ejecutar cuanta index --rebuild",
            ),
            "open in another process",
        ),
        (
            too_large(),
            (
                "el índice se detuvo antes de leer ningún archivo: 8 archivos",
                "pon estas líneas en .cuanta/config.toml",
            ),
            "the index stopped",
        ),
    ],
)
def test_the_map_refusals_read_in_spanish(
    failure: CuantaError, spanish: tuple[str, str], english: str
) -> None:
    services = FakeServices(service_error=failure)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("g")
        await wait_for(pilot, lambda: bool(app.query(MapView)))
        await wait_for(pilot, lambda: bool(shown(app)))
        text = "\n".join(shown(app))
        assert text.startswith("No se pudo cargar el mapa: ")
        assert all(part in text for part in spanish), text
        assert english not in text

    drive(make_app(services, language="es"), scenario)


def test_the_change_plan_and_the_team_show_the_lines_to_paste() -> None:
    services = FakeServices(change_plan_error=too_large())

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, BUG)
        await wait_for(pilot, lambda: pasted(app))
        app.clear_notifications()
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: current(wizard) == "team")
        await wait_for(pilot, lambda: pasted(app))

    drive(make_app(services), scenario)


def test_the_ceiling_reads_in_spanish() -> None:
    failure = too_large()
    catalog = Catalog("es")
    reason = catalog.message(failure.reason)
    assert "el índice se detuvo antes de leer ningún archivo: 8 archivos" in reason
    assert "Carpetas más grandes: .odoo_ref (8)" in reason
    assert catalog.message(failure.advice).endswith('[detect]\nexclude = [".odoo_ref"]')
    flat = oversized_index(("a.py", "b.py"), (), 1)
    assert flat is not None
    assert "todos los archivos están en la raíz" in catalog.message(flat.reason)
