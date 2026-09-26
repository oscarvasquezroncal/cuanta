from __future__ import annotations

from contextlib import suppress

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.widgets import Button, Checkbox, DataTable, RadioButton, RadioSet, Static

from cuanta.application.instinct_view import (
    BACKENDS,
    BackendStatus,
    JevCard,
    ProbeRow,
    sentence,
)
from cuanta.domain.ledger import Decision
from cuanta.domain.messages import Message as Said
from cuanta.domain.messages import msg, option_message, question_message
from cuanta.domain.progress import Status
from cuanta.tui.cells import labeled
from cuanta.tui.fmt import glyph, money, status_style
from cuanta.tui.i18n import Catalog
from cuanta.tui.services import Services
from cuanta.tui.widgets.flow import FlowRow

PROBE_COLUMNS = ("col_primitive", "col_question", "col_answer", "col_latency", "col_cost")
DECISION_COLUMNS = ("col_decision", "col_backend", "col_latency")
QUESTION_WIDTH = 48


def backend_label(status: BackendStatus, catalog: Catalog) -> Content:
    state = Status.OK if status.available else Status.WARN
    where = catalog("instinct.remote") if status.remote else catalog("instinct.local")
    availability = catalog("instinct.available" if status.available else "instinct.unavailable")
    parts: list[str | tuple[str, str]] = [
        (f"{glyph(state)} ", status_style(state)),
        (status.name, "bold"),
        (f"  {availability}, {where}", "$text-muted"),
    ]
    if status.current:
        parts.append((f"  {catalog('instinct.current')}", "$accent"))
    return Content.assemble(*parts)


class InstinctView(VerticalScroll):
    def __init__(self, services: Services, catalog: Catalog) -> None:
        super().__init__(id="instinct", classes="view")
        self._services = services
        self._t = catalog
        self.statuses: tuple[BackendStatus, ...] = ()

    def compose(self) -> ComposeResult:
        t = self._t
        with Vertical(id="jev-card", classes="card"):
            with Horizontal(classes="card-head"):
                yield Static(t("instinct.jev_title"), classes="card-title")
                yield Button(
                    t("instinct.test"), id="jev-test", compact=True, tooltip=t("tips.jev_test")
                )
            yield Static(Content.styled(t("app.loading"), "$text-muted"), id="jev-facts")
            yield Static("", id="jev-status")
        with Vertical(id="instinct-card", classes="card"):
            yield Static(t("instinct.title"), classes="card-title")
            with RadioSet(id="instinct-backends"):
                for name in BACKENDS:
                    yield RadioButton(name, id=f"backend-{name}")
            yield Static("", id="instinct-detail")
            yield Checkbox(t("instinct.consent"), id="instinct-consent", compact=True)
            yield Static(Content.styled(t("instinct.consent_help"), "$text-muted"))
            with FlowRow(classes="button-row"):
                yield Button(t("instinct.use"), id="instinct-use", variant="primary", compact=True)
                yield Button(t("instinct.probe"), id="instinct-probe", compact=True)
                yield Button(t("instinct.preview"), id="instinct-preview", compact=True)
            yield Static("", id="preview-help")
            yield Static("", id="preview-body")
        with Vertical(id="probe-card", classes="card"):
            yield Static(t("instinct.probe"), classes="card-title")
            yield DataTable(id="probe-table", cursor_type="row")
        with Vertical(id="decisions-card", classes="card"):
            yield Static(t("instinct.decisions"), classes="card-title")
            yield DataTable(id="decisions-table", cursor_type="row", zebra_stripes=True)
            yield Static("", id="decisions-empty")

    def on_mount(self) -> None:
        with suppress(NoMatches):
            for table_id, columns in (
                ("#probe-table", PROBE_COLUMNS),
                ("#decisions-table", DECISION_COLUMNS),
            ):
                table = self.query_one(table_id, DataTable)
                for key in columns:
                    table.add_column(self._t(f"instinct.{key}"), key=key)
            self.query_one("#probe-card").display = False
            self.query_one("#preview-help").display = False
            self.query_one("#preview-body").display = False
            self.reload()
            self.load_jev(False)

    @work(thread=True, exclusive=True, group="instinct", exit_on_error=False)
    def reload(self) -> None:
        try:
            statuses, decisions = self._services.instinct_overview()
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        self.app.call_from_thread(self.show, statuses, decisions)

    def show(self, statuses: tuple[BackendStatus, ...], decisions: tuple[Decision, ...]) -> None:
        self.statuses = statuses
        for status in statuses:
            button = self.query_one(f"#backend-{status.name}", RadioButton)
            button.label = backend_label(status, self._t)
            if status.current:
                button.value = True
        self._describe(self.selected())
        table = self.query_one("#decisions-table", DataTable)
        table.clear()
        for item in decisions[:50]:
            table.add_row(
                Text(self._t.message(sentence(item))),
                Text(item.backend),
                Text(f"{item.latency_ms:,} ms", justify="right"),
            )
        empty = not decisions
        table.display = not empty
        self.query_one("#decisions-empty", Static).update(
            Content.styled(self._t("instinct.no_decisions"), "$text-muted") if empty else ""
        )

    def _primitive(self, name: str) -> str:
        return self._t.message(msg(f"primitive.{name}"))

    def _question(self, text: str) -> str:
        return self._t.message(question_message(text), text)

    def _answer(self, text: str) -> str:
        option = option_message(text)
        return self._t.message(option) if isinstance(option, Said) else option

    def selected(self) -> BackendStatus | None:
        pressed = self.query_one("#instinct-backends", RadioSet).pressed_button
        name = (pressed.id or "").removeprefix("backend-") if pressed is not None else ""
        return next((status for status in self.statuses if status.name == name), None)

    def _describe(self, status: BackendStatus | None) -> None:
        detail = self.query_one("#instinct-detail", Static)
        consent = self.query_one("#instinct-consent", Checkbox)
        if status is None:
            detail.update("")
            return
        detail.update(Content.styled(self._t.message(status.detail), "$text-muted"))
        consent.value = status.consented
        consent.display = status.remote

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        event.stop()
        self._describe(self.selected())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id
        if button == "instinct-use":
            event.stop()
            status = self.selected()
            if status is None:
                return
            consent = self.query_one("#instinct-consent", Checkbox).value
            if status.remote and not consent:
                message = self._t("instinct.consent_needed", name=status.name)
                self.app.notify(message, severity="warning")
                return
            self.switch(status.name, consent)
        elif button == "jev-test":
            event.stop()
            self.query_one("#jev-test", Button).label = self._t("instinct.testing")
            self.load_jev(True)
        elif button == "instinct-preview":
            event.stop()
            body = self.query_one("#preview-body", Static)
            help_text = self.query_one("#preview-help", Static)
            showing = not body.display
            body.display = showing
            help_text.display = showing
            if showing:
                self.load_preview()
        elif button == "instinct-probe":
            event.stop()
            button_widget = self.query_one("#instinct-probe", Button)
            button_widget.label = self._t("instinct.probing")
            button_widget.disabled = True
            self.run_probe()

    @work(thread=True, exclusive=True, group="jev", exit_on_error=False)
    def load_jev(self, test: bool) -> None:
        try:
            card = self._services.jev_card(test)
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        self.app.call_from_thread(self.show_jev, card)

    def show_jev(self, card: JevCard) -> None:
        t = self._t
        self.query_one("#jev-test", Button).label = t("instinct.test")
        key = t("instinct.key_found") if card.key_present else t("instinct.key_absent")
        latency = f"{card.latency_ms:,} ms" if card.latency_ms is not None else "–"
        spend = t(
            "instinct.spend",
            cost=money(card.spend_week, t("spectrum.na")),
            count=f"{card.decisions_week:,}",
        )
        facts = [
            (t("instinct.key_label"), key),
            (t("instinct.endpoint_label"), card.endpoint),
            (t("instinct.model_label"), card.model),
            (t("instinct.latency_label"), latency),
            (t("instinct.spend_label"), spend),
        ]
        self.query_one("#jev-facts", Static).update(labeled(facts, width=12))
        status = self.query_one("#jev-status", Static)
        if card.status is None:
            status.update("")
            return
        state = Status.OK if card.ok else Status.FAIL
        status.update(
            Content.assemble((f"{glyph(state)} ", status_style(state)), t.message(card.status))
        )

    @work(thread=True, exit_on_error=False)
    def load_preview(self) -> None:
        try:
            text = self._services.instinct_preview()
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        self.app.call_from_thread(self.show_preview, text)

    def show_preview(self, text: str) -> None:
        help_text = Content.styled(self._t("instinct.preview_help"), "$text-muted")
        self.query_one("#preview-help", Static).update(help_text)
        self.query_one("#preview-body", Static).update(Content(text))

    @work(thread=True, exit_on_error=False)
    def switch(self, name: str, consent: bool) -> None:
        try:
            self._services.use_backend(name, consent)
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        self.app.call_from_thread(self.app.notify, self._t("instinct.switched", name=name))
        self.app.call_from_thread(self.reload)

    @work(thread=True, exclusive=True, group="instinct-probe", exit_on_error=False)
    def run_probe(self) -> None:
        try:
            rows = self._services.probe_instinct()
        except Exception as error:
            message = self._t("instinct.probe_failed", error=str(error))
            self.app.call_from_thread(self.app.notify, message, severity="error")
            self.app.call_from_thread(self._probe_done, [])
            return
        self.app.call_from_thread(self._probe_done, rows)
        self.app.call_from_thread(self.reload)

    def _probe_done(self, rows: list[ProbeRow]) -> None:
        button = self.query_one("#instinct-probe", Button)
        button.label = self._t("instinct.probe")
        button.disabled = False
        if not rows:
            return
        table = self.query_one("#probe-table", DataTable)
        table.clear()
        for row in rows:
            table.add_row(
                Text(self._primitive(row.primitive)),
                Text(self._question(row.question)[:QUESTION_WIDTH]),
                Text(self._t.message(row.answer)),
                Text(f"{row.latency_ms:,} ms", justify="right"),
                Text(
                    f"${row.cost_usd:.4f}" if row.cost_usd is not None else self._t("spectrum.na"),
                    justify="right",
                ),
            )
        self.query_one("#probe-card").display = True
