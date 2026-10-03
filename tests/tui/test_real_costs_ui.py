from __future__ import annotations

from textual.pilot import Pilot
from textual.widgets import Button, DataTable, Static

from cuanta.tui.app import CuantaApp
from cuanta.tui.screens.confirm import ConfirmScreen
from cuanta.tui.screens.result import ResultScreen
from cuanta.tui.views.ledger import LedgerView
from tests.tui.fakes import (
    SANDBOX_RUN,
    FakeServices,
    sample_result,
    sandbox_handoff,
    sandbox_result,
    sandbox_trial,
    snapshot,
)
from tests.tui.test_app import drive, make_app, settle
from tests.tui.test_t5_screens import render, wait_for
from tests.tui.test_wizard import STORY, current, launched, open_wizard, tell

RUN = sample_result().run.id


async def _open(app: CuantaApp, pilot: Pilot[None], run_id: str = RUN) -> ResultScreen:
    view = app.services.result_view(run_id)
    assert view is not None
    app.push_screen(ResultScreen(app.services, app.catalog, view))
    await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
    screen = app.screen
    assert isinstance(screen, ResultScreen)
    await settle(app, pilot)
    return screen


def confirming(app: CuantaApp) -> bool:
    return isinstance(app.screen, ConfirmScreen) and bool(app.screen.query("#confirm-ok"))


def test_a_pending_run_shows_its_estimate_and_can_be_accepted() -> None:
    services = FakeServices(results={RUN: sample_result()})

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        screen = await _open(app, pilot)
        decision = render(screen.query_one("#result-decision", Static))
        assert "Estimate" in decision and "$0.10–$0.20 from history (n=4)" in decision
        assert "Actual" in decision and "in range" in decision
        assert "Waiting for your verdict" in decision
        assert screen.query_one("#result-reject").display
        screen.query_one("#result-accept", Button).press()
        await wait_for(pilot, lambda: confirming(app))
        app.screen.query_one("#confirm-ok", Button).press()
        await wait_for(pilot, lambda: services.decisions == [(RUN, "accepted")])
        await wait_for(pilot, lambda: not screen.query_one("#result-accept").display)
        assert not screen.query_one("#result-reject").display
        assert "Accepted 2026-09-26 11:00" in render(screen.query_one("#result-decision", Static))

    drive(make_app(services), scenario, size=(120, 44))


def test_cancelling_a_rejection_records_nothing() -> None:
    services = FakeServices(results={RUN: sample_result()})

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        screen = await _open(app, pilot)
        screen.query_one("#result-reject", Button).press()
        await wait_for(pilot, lambda: confirming(app))
        assert "still counts as an attempt" in render(app.screen.query_one("#confirm-body", Static))
        app.screen.query_one("#confirm-cancel", Button).press()
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        await settle(app, pilot)
        assert services.decisions == []

    drive(make_app(services), scenario, size=(120, 44))


def test_a_refused_decision_is_reported_and_keeps_the_buttons() -> None:
    services = FakeServices(results={RUN: sample_result()}, decision_error="already rejected")

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        screen = await _open(app, pilot)
        screen.query_one("#result-reject", Button).press()
        await wait_for(pilot, lambda: confirming(app))
        app.screen.query_one("#confirm-ok", Button).press()
        await wait_for(pilot, lambda: isinstance(app.screen, ResultScreen))
        await settle(app, pilot)
        assert screen.query_one("#result-reject").display

    drive(make_app(services), scenario, size=(120, 44))


def test_sandbox_runs_keep_apply_and_discard_and_investigations_can_be_accepted() -> None:
    audit = sandbox_result(trial=sandbox_trial(task_type="investigation"))
    services = FakeServices(
        results={SANDBOX_RUN: sandbox_result(), "01JAUDIT": audit},
        handoffs={SANDBOX_RUN: sandbox_handoff()},
    )

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        screen = await _open(app, pilot, SANDBOX_RUN)
        assert screen.query_one("#result-apply").display
        assert not screen.query_one("#result-accept").display
        assert not screen.query_one("#result-reject").display
        screen.dismiss(None)
        await wait_for(pilot, lambda: not isinstance(app.screen, ResultScreen))
        audit_screen = await _open(app, pilot, "01JAUDIT")
        assert audit_screen.query_one("#result-accept").display
        assert not audit_screen.query_one("#result-reject").display

    drive(make_app(services), scenario, size=(120, 44))


def test_the_ledger_switches_to_real_costs_and_back() -> None:
    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("5")
        view = app.query_one(LedgerView)
        await settle(app, pilot)
        toggle = view.query_one("#ledger-costs-toggle", Button)
        toggle.press()
        table = view.query_one("#ledger-costs-table", DataTable)
        await wait_for(pilot, lambda: table.row_count > 0)
        assert view.has_class("-costs")
        assert str(toggle.label) == "Runs"
        assert "Per accepted" in "".join(str(column.label) for column in table.columns.values())
        assert "Since 2026-08-27" in render(view.query_one("#ledger-costs-note", Static))
        toggle.press()
        await wait_for(pilot, lambda: not view.has_class("-costs"))
        assert str(toggle.label) == "Real costs"

    drive(make_app(), scenario, size=(120, 36))


def test_home_costs_card_has_an_empty_state_and_speaks_spanish() -> None:
    async def empty(app: CuantaApp, pilot: Pilot[None]) -> None:
        text = render(app.query_one("#home-costs", Static))
        assert "No mandates in the last 30 days yet." in text

    drive(make_app(FakeServices(snapshot(runs=()))), empty)

    async def spanish(app: CuantaApp, pilot: Pilot[None]) -> None:
        text = render(app.query_one("#home-costs", Static))
        assert "Auditoría" in text and "Por cambio aceptado" in text
        assert "≥$1.52" in text

    drive(make_app(FakeServices(), "es"), spanish)


def test_the_launch_carries_the_estimate_the_team_step_showed() -> None:
    services = FakeServices()

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        await tell(wizard, pilot, STORY)
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: current(wizard) == "team")
        await wait_for(pilot, lambda: wizard.estimate is not None)
        shown = wizard.estimate
        assert shown is not None
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: launched(app) is not None)
        screen = launched(app)
        assert screen is not None and screen.options.estimate == shown.bounds

    drive(make_app(services), scenario, size=(120, 50))


def test_the_verdict_line_needs_a_button_and_decisions_refresh_home() -> None:
    services = FakeServices(
        results={RUN: sample_result(), SANDBOX_RUN: sandbox_result()},
        handoffs={SANDBOX_RUN: sandbox_handoff()},
    )

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        sandbox = await _open(app, pilot, SANDBOX_RUN)
        assert "Waiting for your verdict" not in render(
            sandbox.query_one("#result-decision", Static)
        )
        sandbox.dismiss(None)
        await wait_for(pilot, lambda: not isinstance(app.screen, ResultScreen))
        before = services.calls.count("home")
        screen = await _open(app, pilot)
        screen.query_one("#result-accept", Button).press()
        await wait_for(pilot, lambda: confirming(app))
        app.screen.query_one("#confirm-ok", Button).press()
        await wait_for(pilot, lambda: services.calls.count("home") > before)

    drive(make_app(services), scenario, size=(120, 44))


def test_ledger_costs_show_runs_without_a_cost_and_safe_chip_colours() -> None:
    from cuanta.tui.views.ledger import chip_style

    assert chip_style("auto 50%").bold
    assert chip_style("#8FD3E8").color is not None

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        await pilot.press("5")
        view = app.query_one(LedgerView)
        await settle(app, pilot)
        view.query_one("#ledger-costs-toggle", Button).press()
        table = view.query_one("#ledger-costs-table", DataTable)
        await wait_for(pilot, lambda: table.row_count > 0)
        headers = [str(column.label).strip() for column in table.columns.values()]
        assert headers[:4] == ["Group", "Runs", "Accepted", "Per accepted"]
        assert "No cost" in headers
        feature = [str(cell).strip() for cell in table.get_row("type-feature")]
        assert feature[headers.index("No cost")] == "1"
        app.theme = "calico-light"
        await settle(app, pilot)
        assert table.row_count > 0

    drive(make_app(), scenario, size=(120, 36))
