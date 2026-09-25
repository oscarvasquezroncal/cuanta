from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.events import Click, Resize
from textual.message import Message
from textual.widgets import Static

from cuanta.application.home import HomeSnapshot
from cuanta.domain.progress import Status
from cuanta.domain.voice import Mood
from cuanta.tui.fmt import glyph, status_style
from cuanta.tui.i18n import Catalog
from cuanta.tui.widgets.michi import Michi

ENGINES = ("claude", "codex", "opencode")


def chip(name: str, status: Status, detail: str) -> Content:
    return Content.assemble(
        (f"{glyph(status)} ", status_style(status)), (name, "bold"), (f"  {detail}", "$text-muted")
    )


class ListenerChip(Static):
    class Toggled(Message):
        def __init__(self, running: bool) -> None:
            super().__init__()
            self.running = running

    running = False

    def on_click(self, event: Click) -> None:
        event.stop()
        self.post_message(self.Toggled(self.running))


CHIP_GAP = "  "


def fact_chips(facts: list[tuple[str, str]], width: int = 0) -> Content:
    lines: list[list[Content]] = [[]]
    used = 0
    for label, value in facts:
        piece = Content.assemble(
            (f" {label} ", "$text-muted on $boost"), (f"{value} ", "bold on $boost")
        )
        size = piece.cell_length
        gap = len(CHIP_GAP) if lines[-1] else 0
        if width > 0 and lines[-1] and used + gap + size > width:
            lines.append([])
            used, gap = 0, 0
        lines[-1].append(piece)
        used += gap + size
    return Content("\n").join(Content(CHIP_GAP).join(line) for line in lines)


class EngineChips(Static):
    class Opened(Message):
        pass

    def on_click(self, event: Click) -> None:
        event.stop()
        self.post_message(self.Opened())


class AppHeader(Horizontal):
    def __init__(self, catalog: Catalog, motion: bool = True) -> None:
        super().__init__(id="header")
        self._t = catalog
        self._motion = motion
        self._facts: list[tuple[str, str]] = []

    def on_resize(self, event: Resize) -> None:
        self._paint_facts()

    def _paint_facts(self) -> None:
        if not self._facts:
            return
        target = self.query_one("#facts", Static)
        target.update(fact_chips(self._facts, target.content_region.width))

    def compose(self) -> ComposeResult:
        yield Michi(scale=1, motion=self._motion, id="header-michi")
        with Vertical(id="brand"):
            yield Static(Content.styled(self._t("app.title"), "bold $primary"), id="wordmark")
            yield Static(Content.styled(self._t("app.subtitle"), "$text-muted"), id="tagline")
        yield Static(Content.styled(self._t("app.loading"), "$text-muted"), id="facts")
        with Vertical(id="chips"):
            yield EngineChips("", id="engine-chips")
            yield ListenerChip("", id="listener-chip")

    def show(self, snapshot: HomeSnapshot) -> None:
        t = self._t
        detection = snapshot.report.detection
        stack = detection.stack
        language = " ".join(part for part in (stack.language, stack.language_version) if part)
        facts = [
            (t("header.stack"), language or t("header.unknown")),
            (t("header.tier"), detection.verify_tier.value),
            (t("header.graph"), detection.graph_mode.value),
            (
                t("header.forge"),
                t("header.forge_installed" if snapshot.initialized else "header.forge_missing"),
            ),
        ]
        self._facts = facts
        self._paint_facts()
        self.call_after_refresh(self._paint_facts)
        rows = []
        for name in ENGINES:
            check = snapshot.check(f"engine {name}")
            ready = check is not None and check.status is Status.OK
            rows.append(
                chip(
                    name,
                    Status.OK if ready else Status.SKIP,
                    t("header.engine_ok") if ready else t("header.engine_missing"),
                )
            )
        listener = snapshot.check("listener")
        on = listener is not None and listener.status is Status.OK
        self.query_one("#engine-chips", Static).update(Content("\n").join(rows))
        toggle = self.query_one("#listener-chip", ListenerChip)
        toggle.running = on
        toggle.tooltip = t("header.listener_stop" if on else "header.listener_start")
        toggle.update(
            chip(
                t("header.listener"),
                Status.OK if on else Status.SKIP,
                t("header.listener_on") if on else t("header.listener_off"),
            )
        )
        self.query_one("#header-michi", Michi).mood = mood_for(snapshot)


def mood_for(snapshot: HomeSnapshot) -> Mood:
    if not snapshot.initialized:
        return Mood.SLEEPY
    if not snapshot.report.healthy:
        return Mood.ALARMED
    if snapshot.next_step is None:
        return Mood.HAPPY
    return Mood.WATCHING
