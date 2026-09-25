from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from cuanta.bootstrap import Container
from cuanta.domain.messages import ENGLISH, msg, placeholders
from cuanta.tui import theme as themes
from cuanta.tui.app import CuantaApp
from cuanta.tui.commands import CLI_EQUIVALENT, COMMANDS, ICONS, SECTIONS, action_name
from cuanta.tui.fmt import compact, money
from cuanta.tui.i18n import Catalog, language_from_locale, load_catalog, resolve_language
from cuanta.tui.web import app_command, shell_command


def test_catalogs_have_identical_keys() -> None:
    spanish = {key for key in load_catalog("es") if not key.startswith("msg.")}
    assert set(load_catalog("en")) == spanish


def test_every_message_has_a_spanish_template_with_the_same_placeholders() -> None:
    spanish = load_catalog("es")
    assert {f"msg.{key}" for key in ENGLISH} == {key for key in spanish if key.startswith("msg.")}
    for key, template in ENGLISH.items():
        assert placeholders(spanish[f"msg.{key}"]) == placeholders(template), key


def test_catalog_translates_messages_and_falls_back_to_english() -> None:
    assert Catalog("es").message(msg("stage.port", port=47300)) == "puerto 47300"
    assert Catalog("en").message(msg("stage.port", port=47300)) == "port 47300"
    assert Catalog("es").message(msg("unknown.key")) == "unknown.key"
    assert Catalog("es").message(None) == ""


def test_catalog_falls_back_and_formats() -> None:
    catalog = Catalog("es")
    assert catalog("nav.home") == "Inicio"
    assert catalog("home.week_total", total="4.3M") == "4.3M tokens esta semana"
    assert catalog("missing.key") == "missing.key"
    assert Catalog("fr").language == "en"


@pytest.mark.parametrize(
    ("name", "expected"),
    [("es_MX.UTF-8", "es"), ("Spanish_Mexico", "es"), ("en_US", "en"), ("", "en"), ("C", "en")],
)
def test_language_from_locale(name: str, expected: str) -> None:
    assert language_from_locale(name) == expected


def test_language_precedence() -> None:
    assert resolve_language("es", "en", "en_US") == "es"
    assert resolve_language("", "es", "en_US") == "es"
    assert resolve_language("", "", "es_ES") == "es"
    assert resolve_language("xx", "", "en_GB") == "en"


def test_theme_resolution() -> None:
    assert themes.resolve("auto", False, {}) == themes.CALICO_DARK
    assert themes.resolve("auto", False, {"COLORFGBG": "0;15"}) == themes.CALICO_LIGHT
    assert themes.resolve("auto", True, {}) == themes.ANSI_DARK
    assert themes.resolve("auto", True, {"COLORFGBG": "0;15"}) == themes.ANSI_LIGHT
    assert themes.resolve("calico-light", True, {}) == themes.CALICO_LIGHT
    assert themes.resolve("ansi", False, {}) == themes.ANSI_DARK


def test_calico_palettes_follow_the_spec() -> None:
    dark, light = themes.calico_themes()
    assert (dark.background, dark.primary, dark.foreground) == ("#1C1B26", "#F4A87C", "#F6E3D4")
    assert (light.background, light.primary, light.foreground) == ("#F7F7FB", "#C8693A", "#2A2733")
    assert dark.variables["border"] == "#2E2C3B"
    assert light.variables["text-muted"] == "#6B7087"


def test_every_palette_command_maps_to_a_use_case_and_an_action() -> None:
    for command in COMMANDS:
        use_case = getattr(Container, command.use_case, None)
        assert callable(use_case), command.use_case
        assert callable(getattr(CuantaApp, f"action_{action_name(command.action)}", None))
        assert command.title_key in load_catalog("en")
        assert command.help_key in load_catalog("es")


def test_every_section_has_icon_and_cli_equivalent() -> None:
    assert set(SECTIONS) == set(ICONS) == set(CLI_EQUIVALENT)


def test_numbers_are_compact_and_money_is_na_safe() -> None:
    assert compact(999) == "999"
    assert compact(4_274_700) == "4.3M"
    assert compact(1_000) == "1k"
    assert money(None) == "–"
    assert money(None, "n/d") == "n/d"
    assert money(2.4187) == "$2.42"


def test_web_command_quotes_for_each_shell() -> None:
    parts = ["C:/Program Files/py.exe", "-m", "cuanta", "ui"]
    assert shell_command(parts, "win32").startswith('"C:/Program Files/py.exe"')
    assert shell_command(parts, "linux").startswith("'C:/Program Files/py.exe'")
    command = app_command(Path("proj"), "es", "calico-dark")
    assert command[command.index("--lang") + 1] == "es"


class FakeServer:
    created: ClassVar[list[tuple[str, str, int, str | None]]] = []
    served = 0

    def __init__(
        self, command: str, host: str = "localhost", port: int = 8000, title: str | None = None
    ) -> None:
        FakeServer.created.append((command, host, port, title))

    def serve(self, debug: bool = False) -> None:
        FakeServer.served += 1


def test_web_serves_the_app_command(monkeypatch: pytest.MonkeyPatch) -> None:
    import textual_serve.server

    from cuanta.tui.web import serve

    monkeypatch.setattr(textual_serve.server, "Server", FakeServer)
    serve(Path("proj"), "es", "calico-light", "127.0.0.1", 8123)
    command, host, port, title = FakeServer.created[-1]
    assert (host, port, title) == ("127.0.0.1", 8123, "cuanta")
    assert "-m cuanta" in command
    assert "--lang es" in command
    assert "--ui-theme calico-light" in command
    assert FakeServer.served == 1


def test_web_without_the_extra_explains_how_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from cuanta.domain.errors import NotAvailable
    from cuanta.tui.web import serve

    monkeypatch.setitem(sys.modules, "textual_serve.server", None)
    with pytest.raises(NotAvailable, match="web extra") as caught:
        serve(Path("proj"), "en", "auto", "localhost", 8000)
    assert "cuanta[web]" in caught.value.hint
