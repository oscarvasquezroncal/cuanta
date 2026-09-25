from __future__ import annotations

from contextlib import suppress

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, ContentSwitcher, RadioButton, RadioSet, Static

from cuanta.domain.progress import Status
from cuanta.domain.routing import Preset
from cuanta.tui.fmt import glyph, status_style
from cuanta.tui.i18n import Catalog

PANELS = ("welcome", "engines", "preset")
ENGINES = ("claude", "codex", "opencode")


class OnboardingScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "skip", show=False)]

    def __init__(self, catalog: Catalog, engines: dict[str, bool], preset: str) -> None:
        super().__init__()
        self._t = catalog
        self.engines = engines
        self.preset = preset
        self.panel = 0

    def compose(self) -> ComposeResult:
        t = self._t
        with Vertical(id="onboarding-card", classes="card"):
            yield Static("", id="onboarding-steps")
            with ContentSwitcher(id="onboarding-body", initial="panel-welcome"):
                with Vertical(id="panel-welcome"):
                    yield Static(t("onboarding.welcome_title"), classes="card-title")
                    yield Static(t("onboarding.welcome_body"))
                with Vertical(id="panel-engines"):
                    yield Static(t("onboarding.engines_title"), classes="card-title")
                    yield Static(self._engine_lines(), id="onboarding-engines")
                    yield Static(Content.styled(t("onboarding.engines_help"), "$text-muted"))
                with Vertical(id="panel-preset"):
                    yield Static(t("onboarding.preset_title"), classes="card-title")
                    with RadioSet(id="onboarding-preset"):
                        for preset in Preset:
                            name = t(f"models.preset_{preset.value}")
                            label = f"{name} — {t(f'onboarding.preset_{preset.value}')}"
                            yield RadioButton(
                                label,
                                value=preset.value == self.preset,
                                id=f"preset-{preset.value}",
                            )
            with Horizontal(id="onboarding-nav"):
                yield Button(t("onboarding.skip"), id="onboarding-skip", compact=True)
                yield Button(t("wizard.back"), id="onboarding-back", compact=True)
                yield Button(
                    t("wizard.next"), id="onboarding-next", variant="primary", compact=True
                )

    def _engine_lines(self) -> Content:
        t = self._t
        lines = []
        for name in ENGINES:
            ready = self.engines.get(name, False)
            state = Status.OK if ready else Status.SKIP
            lines.append(
                Content.assemble(
                    (f"{glyph(state)} ", status_style(state)),
                    (name, "bold"),
                    (
                        f"  {t('header.engine_ok' if ready else 'header.engine_missing')}",
                        "$text-muted",
                    ),
                )
            )
        return Content("\n").join(lines)

    def on_mount(self) -> None:
        with suppress(NoMatches):
            self._paint()

    def _paint(self) -> None:
        t = self._t
        marks = "  ".join("●" if index == self.panel else "○" for index in range(len(PANELS)))
        self.query_one("#onboarding-steps", Static).update(Content.styled(marks, "$accent"))
        body = self.query_one("#onboarding-body", ContentSwitcher)
        body.current = f"panel-{PANELS[self.panel]}"
        self.query_one("#onboarding-back", Button).display = self.panel > 0
        last = self.panel == len(PANELS) - 1
        self.query_one("#onboarding-next", Button).label = t(
            "onboarding.finish" if last else "wizard.next"
        )

    def chosen(self) -> str:
        pressed = self.query_one("#onboarding-preset", RadioSet).pressed_button
        return (pressed.id or "").removeprefix("preset-") if pressed is not None else self.preset

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button = event.button.id
        if button == "onboarding-skip":
            self.dismiss(None)
        elif button == "onboarding-back":
            self.panel = max(0, self.panel - 1)
            self._paint()
        elif button == "onboarding-next":
            if self.panel == len(PANELS) - 1:
                self.dismiss(self.chosen())
                return
            self.panel += 1
            self._paint()

    def action_skip(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("question_mark", "close", show=False),
    ]

    def __init__(self, catalog: Catalog, section: str, command: str) -> None:
        super().__init__()
        self._t = catalog
        self.section = section
        self.command = command

    def compose(self) -> ComposeResult:
        t = self._t
        with Vertical(id="help-card", classes="card"):
            yield Static(t(f"nav.{self.section}"), classes="card-title")
            yield Static(t(f"help.{self.section}"), id="help-body")
            yield Static(
                Content.assemble((f"{t('help.cli')}  ", "$text-muted"), (self.command, "$accent"))
            )
            yield Static(Content.styled(t("help.keys"), "$text-muted"))
            yield Button(t("app.tip_close"), id="help-close", compact=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
