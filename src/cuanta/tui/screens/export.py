from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static

from cuanta.tui.i18n import Catalog
from cuanta.tui.widgets.flow import FlowRow

TABLES = ("all", "runs", "events", "test_runs", "decisions", "baselines")


class ExportScreen(ModalScreen[tuple[str, str, bool] | None]):
    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(self, catalog: Catalog, fmt: str) -> None:
        super().__init__(id="export-screen")
        self._t = catalog
        self.fmt = fmt

    def compose(self) -> ComposeResult:
        t = self._t
        with Vertical(id="export-dialog", classes="card"):
            yield Static(t("ledger.export_title"), classes="card-title")
            yield Label(t("ledger.export_table"))
            yield Select(
                [(name, name) for name in TABLES], value="all", allow_blank=False, id="export-table"
            )
            yield Label(t("ledger.export_path"))
            yield Input(self._path("all"), id="export-path")
            yield Checkbox(t("ledger.export_raw"), False, id="export-raw", compact=True)
            yield Static(self._hint("all"), id="export-hint", classes="muted")
            with FlowRow(classes="button-row"):
                yield Button(
                    t("ledger.export_confirm"), id="export-ok", variant="primary", compact=True
                )
                yield Button(t("mandate.cancel"), id="export-cancel", compact=True)

    def on_select_changed(self, event: Select.Changed) -> None:
        event.stop()
        table = event.value if isinstance(event.value, str) else "all"
        self.query_one("#export-path", Input).value = self._path(table)
        self.query_one("#export-hint", Static).update(self._hint(table))

    def _path(self, table: str) -> str:
        name = "ledger" if table == "all" else f"ledger-{table}"
        suffix = "zip" if self.fmt == "csv" and table == "all" else self.fmt
        return f".cuanta/exports/{name}.{suffix}"

    def _hint(self, table: str) -> str:
        if self.fmt == "csv":
            return self._t("ledger.export_hint_zip" if table == "all" else "ledger.export_hint_csv")
        return self._t("ledger.export_hint_json")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._confirm()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "export-ok":
            self._confirm()
        else:
            self.action_cancel()

    def _confirm(self) -> None:
        table = self.query_one("#export-table", Select).value
        path = self.query_one("#export-path", Input).value.strip()
        raw = self.query_one("#export-raw", Checkbox).value
        if path:
            self.dismiss((table if isinstance(table, str) else "all", path, raw))

    def action_cancel(self) -> None:
        self.dismiss(None)
