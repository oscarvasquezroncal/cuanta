from __future__ import annotations

from contextlib import suppress

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.widgets import Button, DataTable, Input, Static, TabbedContent, TabPane

from cuanta.application.map import MapFile, MapStatus
from cuanta.domain.code_index import HandlingCard, IndexRow, SearchHit
from cuanta.tui.i18n import Catalog
from cuanta.tui.services import Services
from cuanta.tui.widgets.flow import FlowRow


def card_content(t: Catalog, card: HandlingCard) -> Content:
    lines = [t("map.card_heading", path=card.path, role=t(f"map.role_{card.role}"))]
    for line in card.text.splitlines()[1:]:
        prefix, _, value = line.partition(" ")
        if prefix == "Purpose" and value.startswith(card.role.capitalize() + " for "):
            value = t(
                "map.default_purpose",
                role=t(f"map.role_{card.role}"),
                subject=value.removeprefix(card.role.capitalize() + " for ").removesuffix("."),
            )
        elif prefix == "Risk":
            for source, key in (
                ("fan-in=", "fan_in"),
                ("centrality=", "centrality"),
                ("exports=", "exports"),
            ):
                value = value.replace(source, t(f"map.risk_{key}") + "=")
            value = value.replace("sensitive", t("map.sensitive"))
        elif prefix == "Flags":
            value = value.replace("generated", t("map.generated")).replace(
                "do-not-edit", t("map.do_not_edit")
            )
        elif prefix == "Tests":
            value = value.replace("imports this file", t("map.imports_file"))
        lines.append(t(f"map.card_{prefix.lower()}") + " " + value)
    return Content("\n".join(lines))


class MapView(VerticalScroll):
    def __init__(self, services: Services, catalog: Catalog) -> None:
        super().__init__(id="map", classes="view")
        self._services = services
        self._t = catalog
        self.hits: tuple[SearchHit, ...] = ()
        self.selected: str = ""
        self.details: MapFile | None = None
        self.status: MapStatus | None = None
        self._query = ""

    def compose(self) -> ComposeResult:
        t = self._t
        with Vertical(classes="card", id="map-status-card"):
            yield Static(t("map.title"), classes="card-title")
            with Horizontal(id="map-status-chips"):
                for name in ("files", "coverage", "stale", "semantic"):
                    yield Static("", id=f"map-{name}", classes="map-chip")
            yield Static("", id="map-updated")
            with FlowRow(classes="button-row"):
                yield Button(t("map.revalidate"), id="map-revalidate", compact=True)
                yield Button(t("map.rebuild"), id="map-rebuild", compact=True)
        with Horizontal(id="map-search-row"):
            yield Input(placeholder=t("map.search_hint"), id="map-query")
            yield Button(t("map.search"), id="map-search", variant="primary", compact=True)
        yield Static(t("map.empty"), id="map-empty")
        yield DataTable(id="map-hits", cursor_type="row", zebra_stripes=True)
        yield Static("", id="map-reasons")
        with Vertical(classes="card", id="map-file-card"):
            yield Static("", id="map-file-title", classes="card-title")
            yield Static("", id="map-card")
            with TabbedContent(id="map-file-tabs"):
                with TabPane(t("map.fresh"), id="map-fresh-pane"):
                    yield Static("", id="map-fresh-facts")
                with TabPane(t("map.stale_facts"), id="map-stale-pane"):
                    yield Static(t("map.stale_hint"), classes="map-note")
                    yield Static("", id="map-stale-facts")
                with TabPane(t("map.impact"), id="map-impact-pane"):
                    yield Static("", id="map-impact")

    def on_mount(self) -> None:
        with suppress(NoMatches):
            table = self.query_one("#map-hits", DataTable)
            for key in ("file", "score", "matched"):
                table.add_column(self._t(f"map.col_{key}"), key=key)
            self.query_one("#map-file-card").display = False
            self.query_one("#map-hits").display = False

    def activate(self) -> None:
        self.refresh_status()

    @work(thread=True, exclusive=True, group="map-status", exit_on_error=False)
    def refresh_status(self, rebuild: bool = False, revalidate: bool = False) -> None:
        try:
            status = (
                self._services.map_revalidate()
                if revalidate
                else self._services.map_status(rebuild)
            )
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        self.app.call_from_thread(self.show_status, status)
        self.app.call_from_thread(self._refresh_selection)

    def _refresh_selection(self) -> None:
        if self._query:
            self.search(self._query)
        elif self.selected:
            self.open_file(self.selected)

    def show_status(self, state: MapStatus) -> None:
        self.status = state
        status = state.status
        t = self._t
        values = {
            "files": t("map.files", count=status.files),
            "coverage": t("map.coverage", value=f"{status.coverage:.0%}"),
            "stale": t("map.stale", count=state.stale_facts),
            "semantic": t("map.semantic_on" if state.semantic else "map.semantic_off"),
        }
        for name, value in values.items():
            self.query_one(f"#map-{name}", Static).update(Content(value))
        updated = status.updated_at[:19].replace("T", " ") or t("spectrum.na")
        self.query_one("#map-updated", Static).update(t("map.updated", at=updated))

    @work(thread=True, exclusive=True, group="map-search", exit_on_error=False)
    def search(self, query: str) -> None:
        self._query = query
        try:
            hits = self._services.map_search(query)
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        self.app.call_from_thread(self.show_hits, hits)

    def show_hits(self, hits: tuple[SearchHit, ...]) -> None:
        self.hits = hits
        table = self.query_one("#map-hits", DataTable)
        table.clear()
        for hit in hits:
            table.add_row(
                Text(hit.path), f"{hit.score:.2f}", ", ".join(hit.matched_terms), key=hit.path
            )
        table.display = bool(hits)
        self.query_one("#map-empty").display = not hits
        self.query_one("#map-file-card").display = False
        self.query_one("#map-reasons", Static).update("")
        self.selected = ""
        self.details = None
        if hits:
            self.open_file(hits[0].path)

    @work(thread=True, exclusive=True, group="map-file", exit_on_error=False)
    def open_file(self, path: str) -> None:
        try:
            details = self._services.map_file(path)
        except Exception as error:
            self.app.call_from_thread(self.app.notify, str(error), severity="error")
            return
        self.app.call_from_thread(self.show_file, path, details)

    def show_file(self, path: str, details: MapFile) -> None:
        self.selected, self.details = path, details
        t = self._t
        hit = next((hit for hit in self.hits if hit.path == path), None)
        reasons = self._reasons(hit) if hit is not None else ""
        self.query_one("#map-reasons", Static).update(
            Content.assemble((t("map.reasons") + "\n", "bold"), (reasons, ""))
        )
        self.query_one("#map-file-title", Static).update(Text(path))
        self.query_one("#map-card", Static).update(card_content(t, details.card))
        self.query_one("#map-fresh-facts", Static).update(self._facts(details.facts))
        self.query_one("#map-stale-facts", Static).update(self._facts(details.stale))
        impact = "\n".join(
            f"{edge.path} → {edge.target} ({edge.relation})" for edge in details.impact
        )
        self.query_one("#map-impact", Static).update(Content(impact or t("map.no_impact")))
        self.query_one("#map-file-card").display = True

    def _reasons(self, hit: SearchHit) -> str:
        rows = []
        for key, values in (
            ("matched", hit.matched_terms),
            ("graph", hit.edges),
            ("facts", hit.facts),
        ):
            if values:
                rows.append(self._t(f"map.reason_{key}", values=", ".join(values)))
        if hit.prior:
            rows.append(self._t("map.reason_prior", value=f"{hit.prior:.3f}"))
        return "\n".join(rows)

    def _facts(self, facts: tuple[IndexRow, ...]) -> Content:
        if not facts:
            return Content.styled(self._t("map.no_facts"), "$text-muted")
        lines = [
            f"{fact.path}:{fact.line}-{fact.end_line}  [{fact.provenance}]\n{fact.text}"
            for fact in facts
        ]
        return Content("\n\n".join(lines))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "map-query":
            event.stop()
            self.search(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "map-search":
            self.search(self.query_one("#map-query", Input).value)
        elif event.button.id == "map-rebuild":
            self.refresh_status(rebuild=True)
        elif event.button.id == "map-revalidate":
            self.refresh_status(revalidate=True)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "map-hits" and event.row_key.value:
            event.stop()
            self.open_file(event.row_key.value)
