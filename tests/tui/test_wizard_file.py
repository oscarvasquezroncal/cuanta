from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from textual.errors import NoWidget
from textual.geometry import Region
from textual.pilot import Pilot
from textual.widget import Widget
from textual.widgets import Button, Input, Static, TextArea

from cuanta.domain.mandate import ESSENTIAL_FIELDS
from cuanta.domain.mandate_file import parse_mandate_file
from cuanta.tui.app import CuantaApp
from cuanta.tui.screens.attach import AttachScreen
from cuanta.tui.services import ContainerServices
from cuanta.tui.views.mandate import MandateView
from cuanta.tui.widgets.wizard import MandateWizard
from tests.real_run import phased_mandate
from tests.tui.fakes import FakeServices
from tests.tui.test_app import drive, make_app
from tests.tui.test_cross_services import UNRESOLVED_HOME, unresolvable_home
from tests.tui.test_snapshots import loaded
from tests.tui.test_t5_screens import render, wait_for
from tests.tui.test_wizard import STORY, current, launched, open_wizard, tell
from tests.tui.test_wizard_intents import choose

FILE = "docs/mandato.md"
WINDOWS_FILE = "D:\\mandatos\\mandato largo.md"
LABELLED_BUG = (
    "TIPO: error\n"
    "QUÉ: arreglar el total del carrito\n"
    "POR QUÉ: AssertionError: expected 10 got 12\n"
    "FUERA DE ALCANCE: los pagos\n"
)
SHORT_FILE = "Arregla el total del carrito\n\nAssertionError: expected 10 got 12\n"
CODE_COMMENT = (
    "Fix this function, it returns None:\n# compute the total\ndef total(items):\n    sum(items)"
)
NOTE_HEADING = (
    "Add a dark mode toggle to the settings page\n\n"
    "## Notes\nUse the existing theme tokens. Don't touch payments."
)
PLAIN_TITLE = (
    "Refactoriza el intérprete de consultas para que el endpoint responda todas las intenciones."
)
NOT_UTF8 = "No se pudo leer {path}: no es texto UTF-8; guárdalo como UTF-8"


def file_note(wizard: MandateWizard) -> str:
    return render(wizard.query_one("#wiz-file-note", Static))


async def understood(wizard: MandateWizard, pilot: Pilot[None]) -> None:
    await wait_for(pilot, lambda: wizard.understanding is not None and current(wizard) == "confirm")


async def told(wizard: MandateWizard, pilot: Pilot[None], story: str) -> None:
    wizard.query_one("#wiz-story", TextArea).text = story
    wizard.query_one("#wiz-next", Button).press()
    await understood(wizard, pilot)


def detected(wizard: MandateWizard) -> str:
    return render(wizard.query_one("#wiz-detected", Static))


def visible_rows(app: CuantaApp, widget: Widget) -> int:
    try:
        return app.screen.find_widget(widget).visible_region.height
    except NoWidget:
        return 0


def test_the_file_box_loads_the_whole_file_into_the_story() -> None:
    text = phased_mandate()
    services = FakeServices(evidence_files={FILE: text})

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        box = wizard.query_one("#wiz-file", Input)
        box.value = FILE
        wizard.query_one("#wiz-load", Button).press()
        await wait_for(pilot, lambda: wizard.story == text.strip())
        assert f"Loaded {FILE} · {len(text.strip()):,} characters" in file_note(wizard)
        assert current(wizard) == "tell" and wizard.understanding is None
        wizard.query_one("#wiz-story", TextArea).text = ""
        box.value = f'"{FILE}"'
        box.focus()
        await pilot.press("enter")
        await wait_for(pilot, lambda: wizard.story == text.strip())
        assert current(wizard) == "tell"

    drive(make_app(services), scenario, size=(120, 50))


def test_a_missing_or_empty_file_says_so_and_keeps_the_story() -> None:
    services = FakeServices(evidence_files={"docs/vacio.md": "  \n"})

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        wizard.query_one("#wiz-story", TextArea).text = "Add a badge to the cart"
        box = wizard.query_one("#wiz-file", Input)
        load = wizard.query_one("#wiz-load", Button)
        box.value = "docs/falta.md"
        load.press()
        await wait_for(pilot, lambda: "File not found: docs/falta.md" in file_note(wizard))
        assert wizard.story == "Add a badge to the cart"
        box.value = "docs/vacio.md"
        load.press()
        await wait_for(pilot, lambda: "The file is empty: docs/vacio.md" in file_note(wizard))
        box.value = "   "
        load.press()
        await wait_for(pilot, lambda: "Write the path of the file first." in file_note(wizard))
        assert wizard.story == "Add a badge to the cart"
        assert services.understood == []

    drive(make_app(services), scenario, size=(120, 50))


def test_a_dropped_macos_path_with_escaped_spaces_loads_from_the_file_box() -> None:
    dropped = "/Users/me/My Docs/mandato.md"
    services = FakeServices(evidence_files={dropped: LABELLED_BUG})

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        wizard.query_one("#wiz-file", Input).value = "/Users/me/My\\ Docs/mandato.md "
        wizard.query_one("#wiz-load", Button).press()
        await wait_for(pilot, lambda: wizard.story == LABELLED_BUG.strip())
        assert f"Loaded {dropped}" in file_note(wizard)
        assert wizard.query_one("#wiz-file", Input).value == dropped

    drive(make_app(services), scenario, size=(120, 50))


def test_a_windows_home_path_keeps_its_separators_in_the_file_box_and_the_story_box() -> None:
    home = "~\\Documents\\mandato.md"
    services = FakeServices(evidence_files={home: LABELLED_BUG})

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        wizard.query_one("#wiz-file", Input).value = home
        wizard.query_one("#wiz-load", Button).press()
        await wait_for(pilot, lambda: wizard.story == LABELLED_BUG.strip())
        assert f"Loaded {home}" in file_note(wizard)
        wizard.query_one("#wiz-story", TextArea).text = ""
        wizard.query_one("#wiz-file", Input).value = ""
        await told(wizard, pilot, home)
        assert services.understood == [LABELLED_BUG]
        assert wizard.kind == "bug"

    drive(make_app(services), scenario, size=(120, 50))


def test_a_path_pasted_alone_loads_that_file_instead_of_being_the_story() -> None:
    services = FakeServices(evidence_files={WINDOWS_FILE: LABELLED_BUG})

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        story = wizard.query_one("#wiz-story", TextArea)
        story.text = "D:\\mandatos\\otro.md"
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: "File not found: D:\\mandatos\\otro.md" in file_note(wizard))
        assert current(wizard) == "tell" and wizard.understanding is None
        assert services.understood == []
        story.text = f'"{WINDOWS_FILE}"'
        wizard.query_one("#wiz-next", Button).press()
        await understood(wizard, pilot)
        assert services.understood == [LABELLED_BUG]
        assert wizard.story == LABELLED_BUG.strip()
        assert wizard.query_one("#wiz-file", Input).value == WINDOWS_FILE
        assert wizard.kind == "bug"
        request = wizard.request()
        assert request.what == "arreglar el total del carrito"
        assert request.why == "AssertionError: expected 10 got 12"
        assert request.out_of_scope == "los pagos"

    drive(make_app(services), scenario, size=(120, 50))


def test_a_loaded_file_is_handed_to_the_services_until_the_story_is_replaced() -> None:
    services = FakeServices(evidence_files={FILE: LABELLED_BUG})

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        box = wizard.query_one("#wiz-file", Input)
        story = wizard.query_one("#wiz-story", TextArea)
        box.value = FILE
        wizard.query_one("#wiz-load", Button).press()
        await wait_for(pilot, lambda: services.request_files == [(FILE,)])
        story.text = "Add a badge to the cart"
        await wait_for(pilot, lambda: services.request_files == [(FILE,), ()])
        story.text = ""
        await pilot.pause()
        assert services.request_files == [(FILE,), ()]
        wizard.query_one("#wiz-next", Button).press()
        await understood(wizard, pilot)
        assert services.request_files == [(FILE,), (), (FILE,)]
        assert services.understood == [LABELLED_BUG]
        wizard.query_one("#wiz-back", Button).press()
        await wait_for(pilot, lambda: current(wizard) == "tell")
        box.value = ""
        await tell(wizard, pilot, "Add a badge to the cart. Don't touch payments.")
        await wait_for(pilot, lambda: services.request_files[-1] == ())

    drive(make_app(services), scenario, size=(120, 50))


def test_the_launch_excludes_the_file_pasted_alone_and_the_attached_log() -> None:
    log = "logs/run.txt"
    services = FakeServices(evidence_files={FILE: LABELLED_BUG, log: "Traceback: total"})

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await told(wizard, pilot, FILE)
        assert services.request_files == [(FILE,)]
        wizard.query_one("#wiz-attach", Button).press()
        await wait_for(pilot, lambda: isinstance(app.screen, AttachScreen))
        await pilot.press(*log, "enter")
        await wait_for(pilot, lambda: services.request_files[-1] == (FILE, log))
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: current(wizard) == "team")
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: services.launched_files == [(FILE, log)])
        assert services.request_files[-1] == (FILE, log)
        wizard.query_one("#wiz-story", TextArea).text = "Add a badge to the cart."
        wizard.understand_now()
        await wait_for(pilot, lambda: services.request_files[-1] == ())

    drive(make_app(services), scenario, size=(120, 50))


def test_a_twelve_kilobyte_paste_reaches_the_launch_whole() -> None:
    text = phased_mandate()
    parsed = parse_mandate_file(text).request
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        wizard.query_one("#wiz-story", TextArea).text = text
        wizard.query_one("#wiz-next", Button).press()
        await understood(wizard, pilot)
        assert wizard.kind == "refactor"
        assert wizard.query_one("#wiz-what", TextArea).text == parsed.what
        assert wizard.query_one("#wiz-body-field").display
        assert wizard.query_one("#wiz-body", TextArea).text == parsed.why
        detected = render(wizard.query_one("#wiz-detected", Static))
        assert "read-only" not in detected
        assert f"Whole mandate · {len(text.strip()):,} characters · nothing is cut" in detected
        assert wizard.request().why == parsed.why
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: current(wizard) == "team")
        await wait_for(pilot, lambda: services.team_options != [])
        assert services.change_plan_requests[-1].why == parsed.why
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: launched(app) is not None)
        screen = launched(app)
        assert screen is not None
        assert screen.request.type == "refactor"
        assert (screen.request.what, screen.request.why) == (parsed.what, parsed.why)
        assert len(screen.request.why) > 11_000
        assert screen.options.required == ESSENTIAL_FIELDS
        await wait_for(pilot, lambda: services.last == text.strip())
        assert services.last == text.strip()

    drive(make_app(services), scenario, size=(120, 50))


def test_a_whole_mandate_keeps_its_evidence_when_the_type_changes() -> None:
    text = phased_mandate()
    parsed = parse_mandate_file(text).request
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        story = wizard.query_one("#wiz-story", TextArea)
        story.text = text
        wizard.query_one("#wiz-next", Button).press()
        await understood(wizard, pilot)
        for kind in ("bug", "feature", "investigation", "refactor"):
            await choose(wizard, pilot, kind)
            assert wizard.query_one("#wiz-body-field").display
            assert wizard.request().why == parsed.why, kind
        wizard.query_one("#wiz-why", TextArea).text = "the public API stays the same"
        assert wizard.request().constraints == "the public API stays the same"
        await choose(wizard, pilot, "bug")
        assert wizard.request().why == f"the public API stays the same\n\n{parsed.why}"
        wizard.query_one("#wiz-back", Button).press()
        await wait_for(pilot, lambda: current(wizard) == "tell")
        story.text = "Add a badge to the cart. Don't touch payments."
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: services.understood[-1].startswith("Add a badge"))
        await understood(wizard, pilot)
        assert not wizard.query_one("#wiz-body-field").display
        assert wizard.options().required is None

    drive(make_app(services), scenario, size=(120, 50))


def test_going_back_and_telling_another_story_understands_it_again() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        story = wizard.query_one("#wiz-story", TextArea)
        story.text = "Add CSV export to the orders page"
        wizard.query_one("#wiz-next", Button).press()
        await understood(wizard, pilot)
        first = wizard.understanding
        assert wizard.query("#place-0")
        wizard.query_one("#wiz-back", Button).press()
        await wait_for(pilot, lambda: current(wizard) == "tell")
        story.text = "Add a badge to the cart. Don't touch payments."
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(
            pilot, lambda: wizard.understanding is not first and current(wizard) == "confirm"
        )
        second = wizard.understanding
        assert second is not None
        assert wizard.request().what.startswith("Add a badge to the cart")
        assert len(wizard.query("#where-chips .place-chip")) == len(second.places) > 0

    drive(make_app(services), scenario, size=(120, 50))


@pytest.mark.parametrize(
    ("story", "kind", "hashed", "out"),
    [
        (CODE_COMMENT, "bug", "# compute the total", ""),
        (NOTE_HEADING, "feature", "## Notes", "Don't touch payments"),
    ],
    ids=["code_comment", "note_heading"],
)
def test_a_short_story_with_a_hash_line_stays_with_the_intake(
    story: str, kind: str, hashed: str, out: str
) -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await told(wizard, pilot, story)
        assert wizard.kind == kind
        assert not wizard.document_mode
        assert not wizard.query_one("#wiz-body-field").display
        assert "Whole mandate" not in detected(wizard)
        request = wizard.request()
        assert request.what.startswith(story.split("\n", maxsplit=1)[0])
        assert hashed in request.what
        assert request.out_of_scope == out
        assert wizard.options().required is None

    drive(make_app(services), scenario, size=(120, 50))


def test_a_long_mandate_with_a_plain_first_line_keeps_that_line_as_the_what() -> None:
    text = "\n".join([PLAIN_TITLE, *phased_mandate().split("\n")[1:]])
    parsed = parse_mandate_file(text).request
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await told(wizard, pilot, text)
        assert wizard.kind == "refactor"
        assert wizard.query_one("#wiz-what", TextArea).text == PLAIN_TITLE
        assert wizard.query_one("#wiz-body-field").display
        body = wizard.query_one("#wiz-body", TextArea).text
        assert body == parsed.why and PLAIN_TITLE not in body
        assert body.startswith("## Contexto")
        assert "Whole mandate" in detected(wizard)

    drive(make_app(services), scenario, size=(120, 50))


@pytest.mark.parametrize(
    "story",
    [
        "Fix the login\nWhy: users cannot sign in",
        "Type: the user types an email\nand the form breaks",
    ],
    ids=["why_label", "type_label"],
)
def test_a_label_other_than_what_leaves_a_short_story_with_the_intake(story: str) -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await told(wizard, pilot, story)
        understanding = wizard.understanding
        assert understanding is not None and understanding.document is None
        assert not wizard.query_one("#wiz-body-field").display
        assert "Whole mandate" not in detected(wizard)
        assert wizard.options().required is None
        assert services.understood == [story]

    drive(make_app(services), scenario, size=(120, 50))


@pytest.mark.parametrize("layout", ["guided", "one_page"])
def test_an_empty_story_with_a_path_in_the_file_box_loads_and_understands_it_whole(
    layout: str,
) -> None:
    services = FakeServices(layout=layout, evidence_files={FILE: SHORT_FILE})

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        wizard.query_one("#wiz-file", Input).value = f" {FILE} "
        button = "#wiz-next" if layout == "guided" else "#wiz-understand"
        wizard.query_one(button, Button).press()
        await wait_for(pilot, lambda: wizard.understanding is not None)
        assert services.understood == [SHORT_FILE]
        assert wizard.story == SHORT_FILE.strip()
        understanding = wizard.understanding
        assert understanding is not None and understanding.document is not None
        assert wizard.query_one("#wiz-what", TextArea).text == "Arregla el total del carrito"
        assert f"Loaded {FILE}" in file_note(wizard)
        assert render(wizard.query_one("#wiz-error", Static)) == ""
        assert wizard.options().required == ESSENTIAL_FIELDS

    drive(make_app(services), scenario, size=(120, 50))


def test_an_empty_story_with_a_missing_file_in_the_file_box_says_the_file_is_missing() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        wizard.query_one("#wiz-file", Input).value = "docs/falta.md"
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: "File not found: docs/falta.md" in file_note(wizard))
        assert current(wizard) == "tell" and wizard.understanding is None
        assert services.understood == []
        wizard.query_one("#wiz-file", Input).value = "  "
        wizard.query_one("#wiz-next", Button).press()
        await pilot.pause()
        error = render(wizard.query_one("#wiz-error", Static))
        assert error == "Write what you need first."

    drive(make_app(services), scenario, size=(120, 50))


@pytest.mark.parametrize(
    "story", ["/fix the typo in README.md", "~ fix the typo in notes.md"], ids=["slash", "tilde"]
)
def test_a_spaced_slash_sentence_that_is_no_file_is_understood_as_the_story(story: str) -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await told(wizard, pilot, story)
        assert services.understood == [story]
        assert not wizard.query_one("#wiz-file-note").display
        assert wizard.query_one("#wiz-file", Input).value == ""
        understanding = wizard.understanding
        assert understanding is not None and understanding.document is None

    drive(make_app(services), scenario, size=(120, 50))


def test_a_tilde_sentence_is_the_story_when_its_home_cannot_be_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unresolvable_home(monkeypatch, tmp_path)
    services = FakeServices()
    monkeypatch.setattr(services, "read_evidence", ContainerServices(tmp_path).read_evidence)
    sentence = UNRESOLVED_HOME[0]

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await told(wizard, pilot, sentence)
        assert services.understood == [sentence]
        assert not wizard.query_one("#wiz-file-note").display

    drive(make_app(services), scenario, size=(120, 50))


def test_load_file_says_a_tilde_path_is_missing_when_its_home_cannot_be_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unresolvable_home(monkeypatch, tmp_path)
    services = FakeServices()
    monkeypatch.setattr(services, "read_evidence", ContainerServices(tmp_path).read_evidence)
    path = UNRESOLVED_HOME[1]

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        wizard.query_one("#wiz-file", Input).value = path
        wizard.query_one("#wiz-load", Button).press()
        await wait_for(pilot, lambda: f"File not found: {path}" in file_note(wizard))
        assert wizard.story == ""

    drive(make_app(services), scenario, size=(120, 50))


def test_a_spaced_posix_path_that_exists_still_loads_from_the_story_box() -> None:
    spaced = "/Users/me/My Docs/mandato.md"
    services = FakeServices(evidence_files={spaced: LABELLED_BUG})

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await told(wizard, pilot, spaced)
        assert services.understood == [LABELLED_BUG]
        assert wizard.query_one("#wiz-file", Input).value == spaced
        assert wizard.kind == "bug"

    drive(make_app(services), scenario, size=(120, 50))


def test_a_dropped_macos_path_with_escaped_spaces_loads_from_the_story_box() -> None:
    dropped = "/Users/me/My Docs/mandato.md"
    services = FakeServices(evidence_files={dropped: LABELLED_BUG})

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await told(wizard, pilot, "/Users/me/My\\ Docs/mandato.md ")
        assert services.understood == [LABELLED_BUG]
        assert wizard.query_one("#wiz-file", Input).value == dropped
        assert f"Loaded {dropped}" in file_note(wizard)

    drive(make_app(services), scenario, size=(120, 50))


def test_a_file_that_cannot_be_read_says_why_in_the_file_note() -> None:
    folder = IsADirectoryError(21, "Is a directory", "docs")
    services = FakeServices(evidence_errors={"docs": folder})

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        wizard.query_one("#wiz-file", Input).value = "docs"
        wizard.query_one("#wiz-load", Button).press()
        await wait_for(pilot, lambda: "Could not read docs: Is a directory" in file_note(wizard))
        assert wizard.story == ""

    drive(make_app(services), scenario, size=(120, 50))


def test_a_utf16_file_with_a_byte_order_mark_loads_as_text() -> None:
    services = FakeServices(evidence_bytes={FILE: LABELLED_BUG.encode("utf-16")})

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        wizard.query_one("#wiz-file", Input).value = FILE
        wizard.query_one("#wiz-load", Button).press()
        await wait_for(pilot, lambda: wizard.story == LABELLED_BUG.strip())
        assert f"Loaded {FILE}" in file_note(wizard)

    drive(make_app(services), scenario, size=(120, 50))


@pytest.mark.parametrize(
    ("encoding", "name"),
    [("cp1252", "docs/ansi.md"), ("utf-16-le", "docs/utf16.md")],
    ids=["ansi", "utf16_without_bom"],
)
def test_a_file_that_is_not_utf8_is_refused_in_the_file_note(encoding: str, name: str) -> None:
    services = FakeServices(evidence_bytes={name: LABELLED_BUG.encode(encoding)})

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        wizard.query_one("#wiz-file", Input).value = name
        wizard.query_one("#wiz-load", Button).press()
        await wait_for(pilot, lambda: NOT_UTF8.format(path=name) in file_note(wizard))
        assert wizard.story == ""
        wizard.query_one("#wiz-file", Input).value = ""
        wizard.query_one("#wiz-file-note").display = False
        wizard.query_one("#wiz-story", TextArea).text = name
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: NOT_UTF8.format(path=name) in file_note(wizard))
        assert current(wizard) == "tell" and services.understood == []

    drive(make_app(services, language="es"), scenario, size=(120, 50))


@pytest.mark.parametrize("language", ["en", "es"])
def test_the_tell_step_keeps_the_whole_story_box_in_view_at_80x24(language: str) -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await pilot.pause()
        story = wizard.query_one("#wiz-story", TextArea)
        assert story.region.height >= 5
        assert visible_rows(app, story) == story.region.height

    drive(make_app(FakeServices(), language=language), scenario, size=(80, 24))


@pytest.mark.parametrize("language", ["en", "es"])
@pytest.mark.parametrize(
    ("layout", "size"),
    [("guided", (120, 36)), ("guided", (80, 24)), ("one_page", (120, 36)), ("one_page", (80, 24))],
    ids=("guided_wide", "guided_narrow", "one_page_wide", "one_page_narrow"),
)
def test_the_tell_help_keeps_the_paste_size_on_one_row(
    language: str, layout: str, size: tuple[int, int]
) -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await pilot.pause()
        hint = wizard.query_one("#wiz-tell-help", Static)
        rows = [strip.text for strip in hint.render_lines(Region(0, 0, *hint.outer_size))]
        assert any("5 KiB" in row for row in rows), rows

    services = FakeServices(layout=layout)
    drive(make_app(services, language=language), scenario, size=size)


@pytest.mark.parametrize("language", ["en", "es"])
def test_the_one_page_tell_step_shows_understand_without_scrolling_at_80x24(
    language: str,
) -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await pilot.pause()
        understand = wizard.query_one("#wiz-understand", Button)
        assert understand.display
        assert visible_rows(app, understand) == understand.outer_size.height > 0
        story = wizard.query_one("#wiz-story", TextArea)
        assert visible_rows(app, story) == story.outer_size.height
        chips, row = wizard.query_one("#story-chips"), wizard.query_one("#wiz-file-row")
        assert chips.region.y < row.region.y
        story.text = STORY
        await pilot.pause()
        assert story.outer_size.height > 5
        assert visible_rows(app, understand) == understand.outer_size.height
        app.query_one(MandateView).set_layout("guided", save=False)
        await pilot.pause()
        assert chips.region.y > row.region.y

    services = FakeServices(layout="one_page")
    drive(make_app(services, language=language), scenario, size=(80, 24))


@pytest.mark.parametrize("language", ["en", "es"])
@pytest.mark.parametrize(
    ("layout", "size"),
    [("one_page", (120, 36)), ("guided", (120, 36)), ("guided", (80, 24))],
    ids=("one_page_wide", "guided_wide", "guided_narrow"),
)
def test_the_file_placeholder_fits_its_box(
    language: str, layout: str, size: tuple[int, int]
) -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await pilot.pause()
        box = wizard.query_one("#wiz-file", Input)
        assert len(box.placeholder) <= box.content_size.width, box.placeholder

    services = FakeServices(layout=layout)
    drive(make_app(services, language=language), scenario, size=size)


@pytest.mark.parametrize(
    ("language", "words", "button"),
    [
        ("en", ("Windows Terminal", "5 KiB", "“Paste anyway” is safe"), "Load file"),
        (
            "es",
            ("Windows Terminal", "5 KiB", "«Pegar de todas formas» es seguro"),
            "Cargar archivo",
        ),
    ],
)
def test_the_tell_step_says_the_long_paste_dialog_is_windows_terminals(
    language: str, words: tuple[str, ...], button: str
) -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        shown = render(wizard.query_one("#wiz-tell-help", Static))
        assert all(word in shown for word in words), shown
        assert str(wizard.query_one("#wiz-load", Button).label) == button

    drive(make_app(FakeServices(), language=language), scenario, size=(120, 50))


@pytest.mark.parametrize("language", ["en", "es"])
@pytest.mark.parametrize("size", [(120, 36), (80, 24)], ids=("wide", "narrow"))
def test_tell_file_snapshot(
    snap_compare: Callable[..., bool], language: str, size: tuple[int, int]
) -> None:
    async def loaded_file(pilot: Pilot[None]) -> None:
        app = pilot.app
        assert isinstance(app, CuantaApp)
        await loaded(pilot)
        wizard = await open_wizard(app, pilot)
        wizard.query_one("#wiz-file", Input).value = FILE
        wizard.query_one("#wiz-load", Button).press()
        await wait_for(pilot, lambda: bool(file_note(wizard)))
        await loaded(pilot)
        wizard.query_one("#wiz-file-note").scroll_visible(animate=False)
        await loaded(pilot)

    app = CuantaApp(
        FakeServices(evidence_files={FILE: phased_mandate()}),
        language,
        "calico-dark",
        motion=False,
        environ={"WT_SESSION": "1"},
        clock=lambda: 0.0,
    )
    assert snap_compare(app, terminal_size=size, run_before=loaded_file)
