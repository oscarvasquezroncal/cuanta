from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from cuanta.domain.telemetry import WiringPlan
from cuanta.tui.i18n import Catalog
from cuanta.tui.widgets.flow import FlowRow

ALLOW = "allow"
DENY = "deny"


def plan_lines(plans: tuple[WiringPlan, ...], catalog: Catalog) -> Content:
    lines: list[Content] = []
    for plan in plans:
        backup = plan.backup if plan.exists else catalog("consent.created")
        lines.append(Content.styled(plan.engine, "bold $primary"))
        lines.append(
            Content.assemble((f"  {catalog('consent.file')}  ", "$text-muted"), plan.target)
        )
        lines.append(Content.assemble((f"  {catalog('consent.backup')}  ", "$text-muted"), backup))
    return Content("\n").join(lines)


class ConsentScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(self, catalog: Catalog, plans: tuple[WiringPlan, ...]) -> None:
        super().__init__(id="consent-screen")
        self._t = catalog
        self.plans = plans

    def compose(self) -> ComposeResult:
        t = self._t
        with Vertical(id="consent-dialog", classes="card"):
            yield Static(t("consent.title"), classes="card-title")
            yield Static(t("consent.body"), id="consent-body")
            yield Static(plan_lines(self.plans, t), id="consent-files")
            with FlowRow(classes="button-row"):
                yield Button(
                    t("consent.allow"), id="consent-allow", variant="primary", compact=True
                )
                yield Button(t("consent.deny"), id="consent-deny", compact=True)
                yield Button(t("consent.cancel"), id="consent-cancel", compact=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "consent-allow":
            self.dismiss(ALLOW)
        elif event.button.id == "consent-deny":
            self.dismiss(DENY)
        else:
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)
