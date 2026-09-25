from __future__ import annotations

from contextlib import suppress

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.widgets import Button, DataTable, Input, Label, Select, Static, Switch

from cuanta.application.models import CatalogView, ProbeOutcome
from cuanta.application.routing import RoleStats
from cuanta.domain.models import TIER_ORDER, ModelEntry
from cuanta.domain.routing import (
    ENGINE_ORDER,
    PRESET_ROLES,
    ROLES,
    Preset,
    RouteMode,
    RoutingPolicy,
)
from cuanta.tui.fmt import money
from cuanta.tui.i18n import Catalog
from cuanta.tui.services import Services
from cuanta.tui.views.mandate import parse_budget
from cuanta.tui.widgets.flow import FlowRow

CATALOG_COLUMNS = ("col_engine", "col_model", "col_tier", "col_context", "col_in", "col_out")
STATS_COLUMNS = ("col_task", "col_role", "col_tier", "col_runs", "col_success", "col_cost")
MODES = tuple(mode.value for mode in RouteMode)
PRESETS = tuple(preset.value for preset in Preset)
TIERS = tuple(tier.value for tier in TIER_ORDER)


def context_text(tokens: int) -> str:
    if not tokens:
        return "–"
    return f"{tokens // 1_000_000}M" if tokens >= 1_000_000 else f"{tokens // 1_000}k"


def price_text(value: float | None, unknown: str) -> str:
    return unknown if value is None else f"${value:,.2f}"


class ModelsView(VerticalScroll):
    def __init__(self, services: Services, catalog: Catalog) -> None:
        super().__init__(id="models", classes="view")
        self._services = services
        self._t = catalog
        self.entries: tuple[ModelEntry, ...] = ()
        self.engines: list[str] = list(ENGINE_ORDER)
        self.pending_probe: str = ""
        self.policy: RoutingPolicy | None = None

    def compose(self) -> ComposeResult:
        t = self._t
        with Vertical(id="catalog-card", classes="card"):
            with Horizontal(classes="card-head"):
                yield Static(t("models.catalog"), classes="card-title")
                yield Button(t("models.refresh"), id="models-refresh", compact=True)
            yield Static("", id="catalog-meta")
            yield DataTable(id="models-table", cursor_type="row", zebra_stripes=True)
            with FlowRow(id="model-actions", classes="button-row"):
                yield Select(
                    [(t(f"models.tier_{name}"), name) for name in TIERS],
                    id="model-tier",
                    allow_blank=False,
                    value=TIERS[1],
                )
                yield Button(t("models.set_tier"), id="models-set-tier", compact=True)
                yield Button(
                    t("models.probe"), id="models-probe", compact=True, tooltip=t("tips.probe")
                )
            yield Static("", id="probe-note")
        with Vertical(id="routing-card", classes="card"):
            yield Static(t("models.routing"), classes="card-title")
            with Horizontal(classes="field-row"):
                with Vertical(classes="field wide"):
                    yield Label(t("models.mode"))
                    yield Select(
                        [(t(f"models.mode_{name}"), name) for name in MODES],
                        id="route-mode",
                        allow_blank=False,
                        value=RouteMode.AUTO.value,
                    )
                with Vertical(classes="field"):
                    yield Label(t("models.preset"))
                    yield Select(
                        [(t(f"models.preset_{name}"), name) for name in PRESETS],
                        id="route-preset",
                        allow_blank=False,
                        value=Preset.BALANCED.value,
                    )
            with Vertical(id="role-rows"):
                for role in ROLES:
                    with Horizontal(classes="role-row", id=f"role-{role.value}"):
                        yield Static(t(f"models.role_{role.value}"), classes="role-name")
                        yield Select(
                            [(t(f"models.tier_{name}"), name) for name in TIERS],
                            id=f"tier-{role.value}",
                            allow_blank=False,
                            value=TIERS[1],
                        )
                        yield Switch(True, id=f"decide-{role.value}")
                        yield Static(t("models.instinct_decides"), classes="switch-label")
            yield Label(t("models.engine_order"))
            with Vertical(id="engine-order"):
                for engine in ENGINE_ORDER:
                    with Horizontal(classes="engine-row", id=f"engine-row-{engine}"):
                        yield Static(engine, classes="engine-name", id=f"engine-name-{engine}")
                        yield Button("↑", id=f"up-{engine}", compact=True)
                        yield Button("↓", id=f"down-{engine}", compact=True)
            with Horizontal(classes="field-row"):
                with Vertical(classes="field"):
                    yield Label(t("models.cap_mandate"))
                    yield Input(id="cap-mandate")
                with Vertical(classes="field"):
                    yield Label(t("models.cap_daily"))
                    yield Input(id="cap-daily")
                with Vertical(classes="field"):
                    yield Label(t("models.frontier"))
                    yield Switch(False, id="cap-frontier")
            yield Static("", id="routing-error", classes="field-error")
            yield Button(
                t("models.save_routing"), id="routing-save", variant="primary", compact=True
            )
        with Vertical(id="stats-card", classes="card"):
            yield Static(t("models.stats"), classes="card-title")
            yield DataTable(id="routing-stats", cursor_type="row")
            yield Static("", id="stats-empty")

    def on_mount(self) -> None:
        with suppress(NoMatches):
            for table_id, columns in (
                ("#models-table", CATALOG_COLUMNS),
                ("#routing-stats", STATS_COLUMNS),
            ):
                table = self.query_one(table_id, DataTable)
                for key in columns:
                    table.add_column(self._t(f"models.{key}"), key=key)
            self.load(False)

    @work(thread=True, exclusive=True, group="models", exit_on_error=False)
    def load(self, refresh: bool) -> None:
        try:
            view = self._services.models_view(refresh)
            policy = self._services.routing_policy()
            stats = self._services.routing_stats()
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        self.app.call_from_thread(self.show, view, policy, stats)

    def show(self, view: CatalogView, policy: RoutingPolicy, stats: tuple[RoleStats, ...]) -> None:
        self.show_catalog(view)
        self.show_policy(policy)
        self.show_stats(stats)

    def show_catalog(self, view: CatalogView) -> None:
        t = self._t
        self.entries = view.entries
        unknown = t("spectrum.na")
        table = self.query_one("#models-table", DataTable)
        table.clear()
        for entry in view.entries:
            name = f"{entry.id} ★" if entry.default else entry.id
            table.add_row(
                Text(entry.engine),
                Text(name),
                Text(t(f"models.tier_{entry.tier.value}")),
                Text(context_text(entry.context), justify="right"),
                Text(price_text(entry.input_price, unknown), justify="right"),
                Text(price_text(entry.output_price, unknown), justify="right"),
                key=entry.key,
            )
        engines = ", ".join(view.engines) or t("models.no_engines")
        meta = t("models.meta", engines=engines, count=len(view.entries), date=view.verified_on)
        self.query_one("#catalog-meta", Static).update(Content.styled(meta, "$text-muted"))

    def show_policy(self, policy: RoutingPolicy) -> None:
        self.policy = policy
        self.query_one("#route-mode", Select).value = policy.mode.value
        self.query_one("#route-preset", Select).value = policy.preset.value
        for role in ROLES:
            self.query_one(f"#tier-{role.value}", Select).value = policy.tier_for(role).value
            self.query_one(f"#decide-{role.value}", Switch).value = role not in policy.fixed_roles
        self.engines = [*policy.engines, *(e for e in ENGINE_ORDER if e not in policy.engines)]
        self._paint_engines()
        caps = policy.caps
        self.query_one("#cap-mandate", Input).value = f"{caps.mandate_usd:g}"
        self.query_one("#cap-daily", Input).value = f"{caps.daily_usd:g}"
        self.query_one("#cap-frontier", Switch).value = caps.frontier

    def show_stats(self, stats: tuple[RoleStats, ...]) -> None:
        t = self._t
        table = self.query_one("#routing-stats", DataTable)
        table.clear()
        for row in stats:
            table.add_row(
                Text(row.task_type),
                Text(t(f"models.role_{row.role}")),
                Text(t(f"models.tier_{row.tier}")),
                Text(f"{row.samples:,}", justify="right"),
                Text(f"{row.success:.0%}", justify="right"),
                Text(money(row.average_cost, t("spectrum.na")), justify="right"),
            )
        table.display = bool(stats)
        empty = Content.styled(t("models.stats_empty"), "$text-muted") if not stats else ""
        self.query_one("#stats-empty", Static).update(empty)

    def _paint_engines(self) -> None:
        order = self.query_one("#engine-order", Vertical)
        for index, engine in enumerate(self.engines):
            row = self.query_one(f"#engine-row-{engine}", Horizontal)
            order.move_child(row, before=index)
            label = f"{index + 1}. {engine}"
            self.query_one(f"#engine-name-{engine}", Static).update(label)

    def selected(self) -> ModelEntry | None:
        table = self.query_one("#models-table", DataTable)
        if not self.entries or table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        return next((entry for entry in self.entries if entry.key == key), None)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "route-preset" or not isinstance(event.value, str):
            return
        event.stop()
        preset = Preset(event.value)
        for role, tier in PRESET_ROLES[preset].items():
            self.query_one(f"#tier-{role.value}", Select).value = tier.value

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "models-table":
            return
        entry = self.selected()
        if entry is not None:
            self.query_one("#model-tier", Select).value = entry.tier.value
        self.pending_probe = ""
        self.query_one("#probe-note", Static).update("")
        self.query_one("#models-probe", Button).label = self._t("models.probe")

    def routing_values(self) -> dict[str, object] | None:
        t = self._t
        mandate = parse_budget(self.query_one("#cap-mandate", Input).value)
        daily = parse_budget(self.query_one("#cap-daily", Input).value)
        error = self.query_one("#routing-error", Static)
        if mandate is None or daily is None:
            error.update(Content.styled(t("models.invalid_cap"), "$error"))
            return None
        error.update("")
        values: dict[str, object] = {
            "mode": self.query_one("#route-mode", Select).value,
            "preset": self.query_one("#route-preset", Select).value,
            "engines": list(self.engines),
            "caps.mandate_usd": mandate,
            "caps.daily_usd": daily,
            "caps.frontier": self.query_one("#cap-frontier", Switch).value,
        }
        for role in ROLES:
            values[f"roles.{role.value}"] = self.query_one(f"#tier-{role.value}", Select).value
            values[f"decide.{role.value}"] = self.query_one(f"#decide-{role.value}", Switch).value
        return values

    def move_engine(self, engine: str, step: int) -> None:
        index = self.engines.index(engine)
        target = min(max(index + step, 0), len(self.engines) - 1)
        self.engines.insert(target, self.engines.pop(index))
        self._paint_engines()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id or ""
        event.stop()
        if button == "models-refresh":
            self.load(True)
        elif button == "models-set-tier":
            entry = self.selected()
            tier = self.query_one("#model-tier", Select).value
            if entry is not None and isinstance(tier, str):
                self.set_tier(entry.key, tier)
        elif button == "models-probe":
            entry = self.selected()
            if entry is not None:
                self.probe(entry.key, spend=self.pending_probe == entry.key)
        elif button == "routing-save":
            values = self.routing_values()
            if values is not None:
                self.save(values)
        elif button.startswith(("up-", "down-")):
            direction, engine = button.split("-", 1)
            self.move_engine(engine, -1 if direction == "up" else 1)

    @work(thread=True, exit_on_error=False)
    def set_tier(self, key: str, tier: str) -> None:
        try:
            entry = self._services.set_model_tier(key, tier)
            view = self._services.models_view(False)
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        note = self._t("models.tier_saved", model=entry.id, tier=self._t(f"models.tier_{tier}"))
        self.app.call_from_thread(self.show_catalog, view)
        self.app.call_from_thread(self.app.notify, note)

    @work(thread=True, exclusive=True, group="probe", exit_on_error=False)
    def probe(self, key: str, spend: bool) -> None:
        try:
            outcome = self._services.probe_model(key, spend)
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        self.app.call_from_thread(self.show_probe, outcome)

    def show_probe(self, outcome: ProbeOutcome) -> None:
        t = self._t
        note = self.query_one("#probe-note", Static)
        button = self.query_one("#models-probe", Button)
        unknown = t("spectrum.na")
        if outcome.ok is None:
            self.pending_probe = outcome.entry.key
            cost = (
                t("models.probe_unknown")
                if outcome.estimate is None
                else (f"${outcome.estimate:.4f}")
            )
            note.update(Content.styled(t("models.probe_estimate", cost=cost), "$warning"))
            button.label = t("models.probe_confirm")
            return
        self.pending_probe = ""
        button.label = t("models.probe")
        key = "models.probe_ok" if outcome.ok else "models.probe_failed"
        spent = money(outcome.cost_usd, unknown)
        note.update(Content.styled(t(key, cost=spent), "$success" if outcome.ok else "$error"))

    @work(thread=True, exit_on_error=False)
    def save(self, values: dict[str, object]) -> None:
        try:
            self._services.save_routing(values)
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        self.app.call_from_thread(self.app.notify, self._t("models.routing_saved"))
