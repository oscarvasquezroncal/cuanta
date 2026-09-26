from __future__ import annotations

from textual.pilot import Pilot
from textual.widgets import Button, Checkbox, DataTable, Input, Select, Static, TabbedContent, Tree

from cuanta.domain.spectrum import LeakKind
from cuanta.tui.app import CuantaApp
from cuanta.tui.bars import bar, bar_lines, fit
from cuanta.tui.screens.export import ExportScreen
from cuanta.tui.services import ALL_IMPORTED
from cuanta.tui.views.ledger import LedgerView
from cuanta.tui.views.spectrum import SpectrumView
from tests.ledger_fixture import RUN
from tests.tui.fakes import FakeServices
from tests.tui.test_app import at, drive, make_app, settle


def notes(app: CuantaApp) -> list[str]:
    return [str(note.message) for note in app._notifications]


def render(widget: Static) -> str:
    return str(widget.render())


async def open_spectrum(app: CuantaApp, pilot: Pilot[None]) -> SpectrumView:
    await pilot.press("4")
    await settle(app, pilot)
    view = app.query_one(SpectrumView)
    for _ in range(100):
        if view.result is not None:
            break
        await pilot.pause(0.02)
    return view


def test_bars_scale_to_the_peak_and_fit_labels() -> None:
    assert bar(1.0, 4) == "████"
    assert bar(0.5, 4) == "██"
    assert bar(0.0, 4) == ""
    assert fit("abc", 5) == "abc  "
    assert fit("src/very/long/path.py", 8) == "…path.py"
    lines = bar_lines([("main", 100, 0.8), ("tester", 25, 0.2)], "empty", 8, 8)
    text = str(lines)
    assert "main" in text
    assert "80%" in text
    assert str(bar_lines([], "empty")) == "empty"


def test_spectrum_shows_metrics_bars_tree_and_leaks() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        view = await open_spectrum(app, pilot)
        assert view.result is not None
        label = render(view.query_one("#spectrum-label", Static))
        assert RUN in label
        assert "·" not in label
        tokens = view.query_one("#metric-tokens").query_one(".metric-value", Static)
        assert render(tokens) == "98.8k"
        cost = view.query_one("#metric-cost")
        assert render(cost.query_one(".metric-value", Static)) == "$0.90"
        assert "reported by the engine" in render(cost.query_one(".metric-note", Static))
        cache = view.query_one("#metric-cache")
        assert "warm cache · 20,000 tokens read (74%)" in render(
            cache.query_one(".metric-note", Static)
        )
        assert "warm cache" in render(view.query_one("#spectrum-overhead", Static))
        assert "main" in render(view.query_one("#bars-agent", Static))
        assert "src/app.py" in render(view.query_one("#bars-file", Static))
        tree = view.query_one("#spectrum-tree", Tree)
        assert tree.root.children
        assert view.query_one("#leaks", DataTable).row_count == 5
        assert view.selected_leak is not None
        assert view.selected_leak.kind is LeakKind.AMPLIFICATION
        assert view.query_one("#leak-gateway").display
        assert not view.query_one("#leak-reindex").display

    drive(make_app(), scenario)


def test_leak_actions_route_to_use_cases() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        view = await open_spectrum(app, pilot)
        view.query_one("#spectrum-tabs", TabbedContent).active = "tab-leaks"
        await pilot.pause()
        table = view.query_one("#leaks", DataTable)
        table.focus()
        await pilot.press("down", "down", "down", "down")
        await pilot.pause()
        assert view.selected_leak is not None
        assert view.selected_leak.kind is LeakKind.REPEATED_READ
        assert view.query_one("#leak-reindex").display
        view.query_one("#leak-reindex", Button).press()
        await settle(app, pilot)
        assert services.reindexed == 1
        assert "Graph reindexed: graph.json updated" in notes(app)
        table.focus()
        await pilot.press("up", "up", "up", "up")
        await pilot.pause()
        assert view.query_one("#leak-gateway").display
        view.query_one("#leak-gateway", Button).press()
        await settle(app, pilot)
        assert await at(app, pilot, "tests")
        assert services.calls.count("run_tests") == 1

    drive(make_app(services), scenario)


def test_import_selects_all_imported_sessions() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        view = await open_spectrum(app, pilot)
        await pilot.click("#spectrum-import")
        await settle(app, pilot)
        for _ in range(100):
            if view.result is not None and not view.result.runs:
                break
            await pilot.pause(0.02)
        assert services.imports == 1
        assert "Imported 15 events from past sessions." in notes(app)
        assert view.query_one("#spectrum-run", Select).value == ALL_IMPORTED
        assert "all imported sessions" in render(view.query_one("#spectrum-label", Static))

    drive(make_app(services), scenario)


def test_home_run_row_opens_that_run_in_spectrum() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        table = app.query_one("#runs", DataTable)
        table.focus()
        await pilot.press("down", "enter")
        await settle(app, pilot)
        view = app.query_one(SpectrumView)
        assert await at(app, pilot, "spectrum")
        for _ in range(500):
            if view.query_one("#spectrum-run", Select).value == "01JABCDEF0000000000000MAND1":
                break
            await pilot.pause(0.02)
        assert view.query_one("#spectrum-run", Select).value == "01JABCDEF0000000000000MAND1"

    drive(make_app(), scenario)


def test_empty_ledger_spectrum_invites_import() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("4")
        await settle(app, pilot)
        view = app.query_one(SpectrumView)
        assert view.query_one("#spectrum-empty").display
        assert not view.query_one("#metrics").display
        assert not any("Could not load" in note for note in notes(app))

    drive(make_app(FakeServices(empty_ledger=True)), scenario)


async def open_ledger(app: CuantaApp, pilot: Pilot[None]) -> LedgerView:
    await pilot.press("5")
    await settle(app, pilot)
    return app.query_one(LedgerView)


def test_ledger_filters_details_and_counts() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        view = await open_ledger(app, pilot)
        table = view.query_one("#ledger-runs", DataTable)
        assert table.row_count == 4
        assert "4 of 4 runs" in render(view.query_one("#ledger-count", Static))
        view.query_one("#filter-kind", Select).value = "mandate"
        await pilot.pause()
        assert table.row_count == 2
        view.query_one("#filter-engine", Select).value = "none"
        await pilot.pause()
        assert table.row_count == 0
        assert "No runs match these filters." in render(view.query_one("#ledger-empty", Static))
        view.query_one("#filter-engine", Select).value = ""
        view.query_one("#filter-kind", Select).value = ""
        view.query_one("#filter-since", Input).value = "2026-09-22"
        await pilot.pause()
        assert table.row_count == 2
        table.focus()
        await pilot.press("down")
        await pilot.pause()
        fields = render(view.query_one("#ledger-fields", Static))
        assert "Run" in fields and "Status" in fields

    drive(make_app(), scenario)


def test_ledger_export_dialog_writes_through_the_use_case() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await open_ledger(app, pilot)
        await pilot.click("#export-csv")
        await pilot.pause()
        assert isinstance(app.screen, ExportScreen)
        assert app.screen.query_one("#export-path", Input).value == ".cuanta/exports/ledger.zip"
        app.screen.query_one("#export-table", Select).value = "events"
        await pilot.pause()
        assert app.screen.query_one("#export-path", Input).value == (
            ".cuanta/exports/ledger-events.csv"
        )
        app.screen.query_one("#export-raw", Checkbox).value = True
        await pilot.click("#export-ok")
        await settle(app, pilot)
        assert services.exports == [("csv", "events", ".cuanta/exports/ledger-events.csv")]
        assert services.raw_exports == [True]
        assert "Exported 2,048 rows to /work/shop/.cuanta/exports/ledger-events.csv" in notes(app)

    drive(make_app(services), scenario)


def test_empty_ledger_invites_an_action() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        view = await open_ledger(app, pilot)
        assert "The ledger is empty" in render(view.query_one("#ledger-empty", Static))

    drive(make_app(FakeServices(empty_ledger=True)), scenario)
