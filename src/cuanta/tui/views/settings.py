from __future__ import annotations

from contextlib import suppress

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Button, Checkbox, Input, Label, Select, Static

from cuanta.domain.config import Config
from cuanta.domain.plugins import LEAN, SESSIONS
from cuanta.tui.i18n import LANGUAGES, Catalog
from cuanta.tui.services import Services
from cuanta.tui.theme import BACKGROUND_SOLID, BACKGROUNDS, CHOICES
from cuanta.tui.views.mandate import parse_budget

ENGINES = ("claude", "codex", "opencode")
LISTENER_MODES = ("auto", "manual")
PORT_RANGE = range(1024, 65536)


def parse_port(text: str) -> int | None:
    cleaned = text.strip()
    if not cleaned.isdigit():
        return None
    value = int(cleaned)
    return value if value in PORT_RANGE else None


def parse_exclusions(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


class SettingsView(VerticalScroll):
    class Saved(Message):
        def __init__(self, values: dict[str, object]) -> None:
            super().__init__()
            self.values = values

    def __init__(self, services: Services, catalog: Catalog) -> None:
        super().__init__(id="settings", classes="view")
        self._services = services
        self._t = catalog

    def compose(self) -> ComposeResult:
        t = self._t
        with Vertical(id="settings-card", classes="card"):
            yield Static(t("settings.title"), classes="card-title")
            yield Label(t("settings.theme"))
            yield Select(
                [(name, name) for name in CHOICES], id="set-theme", allow_blank=False, value="auto"
            )
            yield Label(t("settings.background"))
            yield Select(
                [(t(f"settings.background_{name}"), name) for name in BACKGROUNDS],
                id="set-background",
                allow_blank=False,
                value=BACKGROUND_SOLID,
            )
            yield Static(Content.styled(t("settings.background_help"), "$text-muted"))
            yield Label(t("settings.language"))
            yield Select(
                [(name, name) for name in LANGUAGES],
                id="set-language",
                allow_blank=False,
                value="en",
            )
            yield Checkbox(t("settings.emoji"), True, id="set-emoji", compact=True)
            yield Label(t("settings.listener"))
            yield Select(
                [(t(f"settings.listener_{mode}"), mode) for mode in LISTENER_MODES],
                id="set-listener",
                allow_blank=False,
                value=LISTENER_MODES[0],
            )
            yield Label(t("settings.session"))
            yield Select(
                [(t(f"settings.session_{name}"), name) for name in SESSIONS],
                id="set-session",
                allow_blank=False,
                value=LEAN,
            )
            yield Static(Content.styled(t("settings.session_help"), "$text-muted"))
            yield Label(t("settings.port"))
            yield Input(id="set-port")
            yield Static("", id="error-port", classes="field-error")
            yield Label(t("settings.engine"))
            yield Select(
                [(name, name) for name in ENGINES],
                id="set-engine",
                allow_blank=False,
                value="claude",
            )
            yield Label(t("settings.budget"))
            yield Input(id="set-budget")
            yield Static("", id="error-settings-budget", classes="field-error")
            yield Label(t("settings.exclusions"))
            yield Input(placeholder=t("settings.exclusions_placeholder"), id="set-exclusions")
            yield Static(Content.styled(t("settings.restart"), "$text-muted"))
            yield Button(t("settings.save"), id="settings-save", variant="primary", compact=True)

    def on_mount(self) -> None:
        with suppress(NoMatches):
            self.load()

    @work(thread=True, exit_on_error=False)
    def load(self) -> None:
        try:
            config = self._services.settings()
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        self.app.call_from_thread(self.show, config)

    def show(self, config: Config) -> None:
        theme = {"dark": "calico-dark", "light": "calico-light"}.get(config.theme, config.theme)
        self.query_one("#set-theme", Select).value = theme if theme in CHOICES else "auto"
        background = config.background if config.background in BACKGROUNDS else BACKGROUND_SOLID
        self.query_one("#set-background", Select).value = background
        language = config.language if config.language in LANGUAGES else self._t.language
        self.query_one("#set-language", Select).value = language
        self.query_one("#set-emoji", Checkbox).value = config.emoji
        mode = config.listener_mode if config.listener_mode in LISTENER_MODES else "auto"
        self.query_one("#set-listener", Select).value = mode
        session = config.run_session if config.run_session in SESSIONS else LEAN
        self.query_one("#set-session", Select).value = session
        self.query_one("#set-port", Input).value = str(config.port)
        engine = config.engine if config.engine in ENGINES else "claude"
        self.query_one("#set-engine", Select).value = engine
        self.query_one("#set-budget", Input).value = f"{config.budget_usd:g}"
        self.query_one("#set-exclusions", Input).value = ", ".join(config.exclusions)

    def values(self) -> dict[str, object] | None:
        t = self._t
        port = parse_port(self.query_one("#set-port", Input).value)
        budget = parse_budget(self.query_one("#set-budget", Input).value)
        port_error = self.query_one("#error-port", Static)
        budget_error = self.query_one("#error-settings-budget", Static)
        port_error.update(
            Content.styled(t("settings.invalid_port"), "$error") if port is None else ""
        )
        budget_error.update(
            Content.styled(t("settings.invalid_budget"), "$error") if budget is None else ""
        )
        if port is None or budget is None:
            return None
        return {
            "ui.theme": self.query_one("#set-theme", Select).value,
            "ui.background": self.query_one("#set-background", Select).value,
            "ui.language": self.query_one("#set-language", Select).value,
            "ui.emoji": self.query_one("#set-emoji", Checkbox).value,
            "listener.port": port,
            "ui.listener": self.query_one("#set-listener", Select).value,
            "runs.session": self.query_one("#set-session", Select).value,
            "engine": self.query_one("#set-engine", Select).value,
            "budget.usd": budget,
            "detect.exclude": parse_exclusions(self.query_one("#set-exclusions", Input).value),
        }

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "settings-save":
            return
        event.stop()
        values = self.values()
        if values is not None:
            self.save(values)

    @work(thread=True, exit_on_error=False)
    def save(self, values: dict[str, object]) -> None:
        try:
            for dotted, value in values.items():
                self._services.save_setting(dotted, value)
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        self.app.call_from_thread(self.app.notify, self._t("settings.saved"))
        self.app.call_from_thread(self.post_message, self.Saved(values))
