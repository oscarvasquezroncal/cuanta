from __future__ import annotations

import time

import pytest
from textual.geometry import Region
from textual.pilot import Pilot
from textual.widget import Widget
from textual.widgets import Button, TextArea

from cuanta.application.doctor import CheckResult, result
from cuanta.domain.fixes import FixAction
from cuanta.domain.forge_template import MANDATE_TEMPLATE, TEMPLATE_FIX
from cuanta.domain.messages import msg
from cuanta.domain.progress import Status, note
from cuanta.tui.app import CuantaApp
from tests.tui.fakes import FakeServices, snapshot
from tests.tui.test_app import SETTLE_BUDGET_S, current, drive, make_app, text_of
from tests.tui.test_t5_screens import wait_for
from tests.tui.test_wizard import open_wizard
from tests.tui.test_wizard_file import visible_rows

VERSION = "0.4.0 (8b8a490)"
HEALTHY = (
    result("python", Status.OK, msg("doctor.python.ok", version="3.12.9")),
    result("engine claude", Status.OK, msg("doctor.engine.found", version="2.1.280")),
    result("listener", Status.OK, msg("doctor.listener.on", port=4318, written="1,204")),
)
MISSING = result(
    "template",
    Status.WARN,
    msg("doctor.template.restorable", path=MANDATE_TEMPLATE, version=VERSION),
    TEMPLATE_FIX,
)
READY = result("template", Status.OK, msg("doctor.template.ready", path=MANDATE_TEMPLATE))
UNUSABLE = result(
    "template",
    Status.WARN,
    msg(
        "doctor.template.unusable",
        problem=msg("template.broken", path=MANDATE_TEMPLATE),
        hint=msg("template.unusable_hint"),
    ),
)
PLANNED = note(Status.INFO, msg("plan.template", path=MANDATE_TEMPLATE, version=VERSION))
BUG = "Fix the cart total: AssertionError: [total] expected 10 got 12. Don't touch payments."


def services_with(*checks: CheckResult) -> FakeServices:
    return FakeServices(home_snapshot=snapshot(checks=(*HEALTHY, *checks)))


def notes(app: CuantaApp) -> list[str]:
    return [str(note.message) for note in app._notifications]


def words(text: str) -> str:
    return " ".join(text.split())


def rendered(widget: Widget) -> str:
    strips = widget.render_lines(Region(0, 0, *widget.outer_size))
    return words(" ".join(strip.text for strip in strips))


def in_view(app: CuantaApp, widget: Widget) -> bool:
    return visible_rows(app, widget) == widget.outer_size.height > 0


def shown_whole(app: CuantaApp, selector: str) -> bool:
    widget = app.query_one(selector)
    return in_view(app, widget) and rendered(widget) == words(text_of(app, selector))


def test_home_reports_a_missing_template_as_part_of_forge_and_fixes_it_in_place() -> None:
    services = services_with(MISSING)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        assert "installed · template missing" in text_of(app, "#project-facts")
        assert "installed · template missing" in text_of(app, "#facts")
        assert "template" in text_of(app, "#next-text")
        assert "when a mandate needs it" in text_of(app, "#next-text")
        assert TEMPLATE_FIX in text_of(app, "#next-fix")
        assert str(app.query_one("#next-run", Button).label) == "Run fix"
        await pilot.click("#next-run")
        deadline = time.monotonic() + SETTLE_BUDGET_S
        while not services.applied and time.monotonic() < deadline:
            await pilot.pause(0.01)
        assert [fix.action for fix in services.applied] == [FixAction.TEMPLATE]
        assert current(app) == "home"

    drive(make_app(services), scenario)


def test_home_in_spanish_names_the_missing_template() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        assert "instalado · falta la plantilla" in text_of(app, "#project-facts")
        assert "plantilla" in text_of(app, "#next-text")
        assert "cuando un mandato la necesita" in text_of(app, "#next-text")
        assert str(app.query_one("#next-run", Button).label) == "Ejecutar corrección"

    drive(make_app(services_with(MISSING), language="es"), scenario)


def test_a_ready_template_keeps_forge_installed_and_out_of_the_next_step() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        facts = text_of(app, "#project-facts")
        assert "installed" in facts and "template missing" not in facts
        assert "template missing" not in text_of(app, "#facts")
        assert "Everything looks healthy" in text_of(app, "#next-text")

    drive(make_app(services_with(READY)), scenario)


def test_home_says_forge_is_installed_with_a_template_it_cannot_use() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        assert "installed · template unusable" in text_of(app, "#project-facts")
        assert "installed · template unusable" in text_of(app, "#facts")
        assert "template missing" not in text_of(app, "#project-facts")
        assert "has no fenced === REQUEST === block" in text_of(app, "#next-text")
        assert "cuanta init --template" in text_of(app, "#next-text")
        assert "Everything looks healthy" not in text_of(app, "#next-text")
        assert not app.query_one("#next-run", Button).display

    drive(make_app(services_with(UNUSABLE)), scenario)


@pytest.mark.parametrize("language", ["en", "es"])
@pytest.mark.parametrize("size", [(80, 24), (120, 36)], ids=["narrow", "wide"])
@pytest.mark.parametrize("check", [MISSING, UNUSABLE], ids=["missing", "unusable"])
def test_home_shows_the_whole_template_next_step(
    check: CheckResult, size: tuple[int, int], language: str
) -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        button = app.query_one("#next-run", Button)

        def whole() -> bool:
            texts = shown_whole(app, "#next-text") and shown_whole(app, "#next-fix")
            return texts and (in_view(app, button) if check.fix else not button.display)

        await wait_for(pilot, whole)
        assert TEMPLATE_FIX in rendered(app.query_one("#next-fix")) or not check.fix
        if check is UNUSABLE:
            assert "cuanta init --template" in rendered(app.query_one("#next-text"))

    drive(make_app(services_with(check), language=language), scenario, size=size)


def test_home_in_spanish_says_the_template_is_unusable() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        assert "instalado · plantilla inutilizable" in text_of(app, "#project-facts")
        assert "instalado · plantilla inutilizable" in text_of(app, "#facts")
        assert "no tiene un bloque === REQUEST === cercado" in text_of(app, "#next-text")
        assert not app.query_one("#next-run", Button).display

    drive(make_app(services_with(UNUSABLE), language="es"), scenario)


def test_the_preview_says_it_will_write_the_missing_template() -> None:
    services = FakeServices(layout="one_page", preview_note=PLANNED)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        wizard.query_one("#wiz-story", TextArea).text = BUG
        wizard.query_one("#wiz-understand", Button).press()
        await wait_for(pilot, lambda: wizard.kind == "bug")
        wizard.query_one("#wiz-preview", Button).press()
        await wait_for(pilot, lambda: wizard.query_one("#preview-card").display)
        expected = f"write: docs/MANDATE_TEMPLATE.md from the vendored Forge {VERSION}"
        await wait_for(pilot, lambda: expected in notes(app))

    drive(make_app(services), scenario, size=(120, 50))
