from __future__ import annotations

from collections.abc import Callable

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, Input, Static

from cuanta.domain.change_plan import ChangePlan, EditTarget
from cuanta.domain.mandate import MandateRequest
from cuanta.tui.app import CuantaApp
from cuanta.tui.widgets.wizard import MandateWizard, PlanChip
from tests.tui.fakes import FakeServices
from tests.tui.test_app import drive, make_app
from tests.tui.test_snapshots import SIZES, THEMES, app_for
from tests.tui.test_t5_screens import render, wait_for
from tests.tui.test_wizard import current, launched, open_wizard

FEATURE_PLAN = ChangePlan(
    edit=(EditTarget("src/cart.py", 0.9, "cart totals"),),
    read=("tests/test_cart.py",),
    guard=("src/payments.py", "docs/billing.md"),
    verify=("uv run pytest tests/test_cart.py",),
    coverage=0.8,
)
FEATURE_REQUEST = MandateRequest(
    type="feature",
    what="Add cart totals",
    tests="The cart shows the sum of its item prices",
    where="src/cart.py",
    out_of_scope="src/payments.py, docs/billing.md",
)
SnapCompare = Callable[..., bool]


def path_chip(wizard: MandateWizard, path: str) -> PlanChip:
    return next(chip for chip in wizard.query(PlanChip) if chip.path == path)


def path_role(wizard: MandateWizard, path: str) -> str:
    return next(
        (chip.role for chip in wizard.query(PlanChip) if chip.path == path and not chip.disabled),
        "",
    )


def test_feature_plan_moves_paths_and_keeps_the_launch_choices() -> None:
    services = FakeServices(change_plan_result=FEATURE_PLAN)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        wizard.prefilled(FEATURE_REQUEST)
        await wait_for(pilot, lambda: path_role(wizard, "src/cart.py") == "edit")
        assert len(wizard.query(PlanChip)) == 4
        path_chip(wizard, "src/cart.py").press()
        await wait_for(pilot, lambda: path_role(wizard, "src/cart.py") == "read")
        plan = wizard.change_plan
        assert plan is not None
        assert "src/cart.py" in plan.read
        path_chip(wizard, "tests/test_cart.py").press()
        await wait_for(
            pilot,
            lambda: path_role(wizard, "tests/test_cart.py") == "guard",
        )
        path_chip(wizard, "src/payments.py").press()
        await wait_for(pilot, lambda: path_role(wizard, "src/payments.py") == "edit")
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: current(wizard) == "team")
        protected = wizard.query_one("#team-protected", Static)
        await wait_for(pilot, lambda: "Protected: 2 paths" in render(protected))
        assert len(services.change_plan_requests) >= 2
        assert services.understood == []
        wizard.query_one("#wiz-next", Button).press()
        await wait_for(pilot, lambda: launched(app) is not None)
        screen = launched(app)
        assert screen is not None
        assert dict(screen.options.plan_overrides) == {
            "src/cart.py": "read",
            "tests/test_cart.py": "guard",
            "src/payments.py": "edit",
        }
        assert wizard.change_plan is None
        assert not wizard.query_one("#change-plan").display

    drive(make_app(services), scenario, size=(120, 50))


def test_a_path_under_a_protected_pattern_stays_out_of_edit() -> None:
    services = FakeServices(change_plan_result=ChangePlan(read=("src/cart.py",), guard=("src/**",)))

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        wizard.prefilled(FEATURE_REQUEST)
        await wait_for(pilot, lambda: path_role(wizard, "src/cart.py") == "read")
        assert "Protected by src/**" in str(path_chip(wizard, "src/cart.py").tooltip)
        path_chip(wizard, "src/cart.py").press()
        await wait_for(pilot, lambda: path_role(wizard, "src/cart.py") == "guard")
        path_chip(wizard, "src/cart.py").press()
        await wait_for(pilot, lambda: path_role(wizard, "src/cart.py") == "read")
        assert not wizard.query("#plan-edit-0")
        path_chip(wizard, "src/**").press()
        await wait_for(pilot, lambda: path_role(wizard, "src/**") == "edit")
        path_chip(wizard, "src/cart.py").press()
        await wait_for(pilot, lambda: path_role(wizard, "src/cart.py") == "guard")
        path_chip(wizard, "src/cart.py").press()
        await wait_for(pilot, lambda: path_role(wizard, "src/cart.py") == "edit")
        before = wizard.change_plan
        wizard.go(2)
        await wait_for(pilot, lambda: wizard.change_plan is not before)
        await wait_for(pilot, lambda: path_role(wizard, "src/cart.py") == "edit")
        assert dict(wizard.options().plan_overrides) == {
            "src/cart.py": "edit",
            "src/**": "edit",
        }

    drive(make_app(services), scenario, size=(120, 50))


def test_reset_during_the_last_plan_mount_does_not_restore_stale_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_paths: list[str] = []

    def reset_on_mount(chip: PlanChip) -> None:
        if chip.role == "guard" and not reset_paths:
            reset_paths.append(chip.path)
            chip.app.query_one(MandateWizard).reset()

    monkeypatch.setattr(PlanChip, "on_mount", reset_on_mount, raising=False)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        wizard.prefilled(FEATURE_REQUEST)
        await wait_for(pilot, lambda: bool(reset_paths))
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert wizard.change_plan is None
        assert not wizard.query_one("#change-plan").display
        assert wizard.options().plan_overrides == ()

    drive(make_app(FakeServices(change_plan_result=FEATURE_PLAN)), scenario, size=(120, 50))


@pytest.mark.parametrize("kind", ["bug", "feature", "refactor", "investigation"])
def test_every_request_type_recompiles_the_plan_without_understanding(kind: str) -> None:
    readonly = kind == "investigation"
    plan = (
        ChangePlan(read=("src/cart.py",), guard=("src/payments.py",), read_only=True)
        if readonly
        else FEATURE_PLAN
    )
    services = FakeServices(change_plan_result=plan)

    async def scenario(app: CuantaApp, pilot: Pilot[None]) -> None:
        wizard = await open_wizard(app, pilot)
        request = MandateRequest(
            type=kind,
            what="Check cart totals",
            why="Why does the cart total drift?",
            tests="The cart total matches item prices",
            constraints="Keep the current cart behavior",
            out_of_scope="src/payments.py",
        )
        wizard.prefilled(request)
        await wait_for(
            pilot, lambda: path_role(wizard, "src/cart.py") == ("read" if readonly else "edit")
        )
        assert wizard.query_one("#change-plan").display
        if readonly:
            assert not wizard.query("#plan-edit-0")
            path_chip(wizard, "src/cart.py").press()
            await wait_for(pilot, lambda: path_role(wizard, "src/cart.py") == "guard")
            path_chip(wizard, "src/cart.py").press()
            await wait_for(pilot, lambda: path_role(wizard, "src/cart.py") == "read")
            assert not wizard.query("#plan-edit-0")
            assert all(role != "edit" for _, role in wizard.options().plan_overrides)
        wizard.query_one("#wiz-where", Input).value = "tests/test_cart.py"
        await wait_for(
            pilot,
            lambda: services.change_plan_requests[-1].where == "tests/test_cart.py",
        )
        assert services.understood == []

    drive(make_app(services), scenario, size=(120, 50))


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
@pytest.mark.parametrize("step", ["confirm", "team"])
def test_change_plan_snapshot(
    snap_compare: SnapCompare, theme: str, size: tuple[int, int], step: str
) -> None:
    async def planned(pilot: Pilot[None]) -> None:
        app = pilot.app
        assert isinstance(app, CuantaApp)
        wizard = await open_wizard(app, pilot)
        wizard.prefilled(FEATURE_REQUEST)
        await wait_for(pilot, lambda: path_role(wizard, "src/cart.py") == "edit")
        if step == "team":
            wizard.go(2)
            await wait_for(pilot, lambda: wizard.estimate is not None)
            wizard.query_one("#wizard-body").scroll_home(animate=False)
        else:
            wizard.query_one("#change-plan").scroll_visible(animate=False, top=True)
        await pilot.pause()

    services = FakeServices(change_plan_result=FEATURE_PLAN)
    assert snap_compare(app_for(theme, services), terminal_size=size, run_before=planned)
