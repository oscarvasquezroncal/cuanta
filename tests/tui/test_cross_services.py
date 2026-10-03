from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from cuanta.adapters.system.clock import FixedClock
from cuanta.application.cross_engine import CompletionState, CrossReport, CrossStep
from cuanta.application.estimate import Estimate, estimate
from cuanta.application.forecast import PlannedForecast, PlanSizes
from cuanta.application.mandate_flow import MandateOptions
from cuanta.application.routing import RoutePlan
from cuanta.bootstrap import Container, load_config
from cuanta.domain.change_plan import ChangePlan, EditTarget
from cuanta.domain.config import Config
from cuanta.domain.errors import NotAvailable
from cuanta.domain.implementation import LARGE_EDIT_TOKENS
from cuanta.domain.ledger import Run
from cuanta.domain.mandate import ESSENTIAL_FIELDS, MandateRequest, Shape
from cuanta.domain.mandate_file import NotText, whole_mandate
from cuanta.domain.messages import english, msg
from cuanta.domain.models import ModelEntry, Tier
from cuanta.domain.pricing import PriceTable
from cuanta.domain.routing import Role, RoleRoute, RoutingPolicy
from cuanta.domain.scout import ShapeChoice
from cuanta.ports.system import Completed
from cuanta.tui.services import ContainerServices, cross_report
from tests.cli.conftest import CLAUDE_HELP
from tests.cli.test_engine_guarantees import forge
from tests.fakes import FakeRunner, copy_repo
from tests.real_run import phased_mandate


@pytest.fixture
def fake_claude(monkeypatch: pytest.MonkeyPatch, isolated_user_dirs: Path) -> FakeRunner:
    runner = FakeRunner(
        binaries={"claude": "/bin/claude"},
        responses={
            "claude --version": Completed(0, "2.1.283 (Claude Code)\n", ""),
            "claude --help": Completed(0, CLAUDE_HELP, ""),
        },
    )

    def build(cls: type[Container], project: Path, verbose: bool = False) -> Container:
        return cls(
            project=project,
            config=load_config(project),
            runner=runner,
            clock=FixedClock(),
            home=isolated_user_dirs,
            verbose=verbose,
        )

    monkeypatch.setattr(Container, "for_project", classmethod(build))
    return runner


def test_a_cross_run_reaches_the_pipeline_screen_as_a_mandate_report() -> None:
    steps = (
        CrossStep(Role.ANALYST, "claude", "sonnet", "R1", True, 0.2, "found it"),
        CrossStep(Role.SENIOR, "codex", "gpt-6-sol", "R2", True, 0.5, "edited"),
    )
    report = CrossReport(
        steps,
        False,
        0.7,
        msg("cross.role_budget", role="tester"),
        changed_files=("src/a.ts",),
        state=CompletionState.PARTIAL,
    )
    root = Run("R1", "cross", engine="claude", cost_usd=0.2, task_type="feature")
    converted = cross_report(root, report)
    assert converted.run.id == "R1" and converted.run.cost_usd == 0.7
    assert not converted.ok
    assert converted.changed_files == ("src/a.ts",)
    assert converted.tests == "partial"
    assert converted.handoffs == ("found it", "edited")
    assert converted.text == "tester has no remaining reserved budget"
    assert converted.task_type == "feature"
    with pytest.raises(NotAvailable):
        cross_report(None, report)


def test_the_team_step_reuses_the_change_plan_compiled_for_the_same_request(
    tmp_path: Path,
) -> None:
    compiled: list[MandateRequest] = []

    def compile_plan(request: MandateRequest) -> ChangePlan:
        compiled.append(request)
        return ChangePlan(verify=(f"check {len(compiled)}",))

    container = cast("Container", SimpleNamespace(change_plan=compile_plan))
    services = ContainerServices(tmp_path)
    first = MandateRequest("bug", "fix add", "wrong sum")
    other = MandateRequest("bug", "fix sub", "wrong difference")
    assert services._compiled_plan(container, first).verify == ("check 1",)
    assert services._compiled_plan(container, first).verify == ("check 1",)
    assert services._compiled_plan(container, other).verify == ("check 2",)
    assert compiled == [first, other]
    services.use_request_files(("mandato.md",))
    assert services._compiled_plan(container, other).verify == ("check 3",)
    assert services._compiled_plan(container, other).verify == ("check 3",)
    assert compiled == [first, other, other]


def test_a_mandate_file_loaded_in_the_app_is_never_its_own_target(
    tmp_path: Path, fake_claude: FakeRunner
) -> None:
    root = copy_repo("python_backend", tmp_path)
    forge(root)
    (root / "mandato.md").write_text(phased_mandate(), encoding="utf-8")
    services = ContainerServices(root)
    parsed = whole_mandate(services.read_evidence("mandato.md"))
    assert parsed is not None
    request = replace(parsed.request, type="refactor")
    options = MandateOptions(required=ESSENTIAL_FIELDS)
    services.use_request_files(("mandato.md",))
    prompt = services.preview_mandate(request, 0, options).prompt
    assert "card:mandato.md" not in prompt and "window:mandato.md" not in prompt
    assert "mandato.md" not in {target.path for target in services.change_plan(request).edit}
    services.use_request_files((str(root / "mandato.md"), str(tmp_path / "elsewhere.md")))
    assert "mandato.md" not in {target.path for target in services.change_plan(request).edit}
    services.use_request_files(())
    assert "mandato.md" in {target.path for target in services.change_plan(request).edit}
    assert "card:mandato.md" in services.preview_mandate(request, 0, options).prompt
    assert not fake_claude.stdins


@pytest.mark.parametrize(
    ("engine", "profile"), [("codex", "balanced"), ("claude", "balanced"), ("claude", "fast")]
)
def test_a_failed_team_forecast_leaves_the_team_step_with_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: str, profile: str
) -> None:
    team = RoutePlan(RoutingPolicy(engines=(engine,)), None, None, (), (), "heuristic")
    reserves: list[bool] = []

    def plan_route(*args: object, **kwargs: object) -> tuple[RoutePlan, None]:
        return team, None

    def team_estimate(
        plan: RoutePlan,
        task_type: str,
        depth: str,
        cap: float,
        shape: str = "pipeline",
        repair: bool = True,
        docs_off: bool = False,
    ) -> Estimate:
        reserves.append(repair)
        return estimate(
            plan, (), PriceTable({}), task_type, depth, cap, shape, None, repair, docs_off
        )

    def team_forecast(*args: object, **kwargs: object) -> PlannedForecast:
        if profile == "fast":
            assert args[4] == "single"
            assert kwargs["implementation_profile"] == "fast"
            assert kwargs["variant"] == "low"
            assert kwargs["model"] == "claude-sonnet-5"
            assert kwargs["native"] is False
        raise ValueError("the code index is being written")

    container = SimpleNamespace(
        config=Config(engine=engine, implementation_profile=profile, implementation_variant="low"),
        plan_route=plan_route,
        team_estimate=team_estimate,
        change_plan=lambda request: ChangePlan(),
        team_forecast=team_forecast,
        shaped_options=lambda request, options: (options, ShapeChoice(Shape.PIPELINE)),
        fast_ready=lambda name: True,
        routing_pinned=lambda mode: False,
        docs_choice=lambda request, options: None,
        shape_plan=lambda plan, shape, docs: plan,
        main_model_plan=lambda plan, model: plan,
        close=lambda: None,
    )
    services = ContainerServices(tmp_path)
    monkeypatch.setattr(services, "_container", lambda: cast("Container", container))
    request = MandateRequest("bug", "fix add", "wrong sum")
    plan, found = services.team_plan(
        request, MandateOptions(engine=engine, model="claude-sonnet-5")
    )
    assert plan is team
    assert found.forecast is None
    assert found.forecast_error is not None
    assert english(found.forecast_error) == (
        "Forecast unavailable, the launch goes ahead without one: the code index is being written"
    )
    assert reserves == [False]


@pytest.mark.parametrize(
    ("kind", "ready", "pinned", "seen"),
    [
        ("bug", True, False, ("fast", "claude-opus-5-5", "high")),
        ("feature", True, False, ("fast", "claude-opus-5-5", "low")),
        ("refactor", True, False, ("balanced", "", "")),
        ("bug", False, False, ("balanced", "", "")),
        ("bug", True, True, ("balanced", "", "")),
    ],
)
def test_the_team_step_forecasts_the_auto_profile_with_the_default_of_its_kind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    ready: bool,
    pinned: bool,
    seen: tuple[str, str, str],
) -> None:
    team = RoutePlan(RoutingPolicy(engines=("claude",)), None, None, (), (), "heuristic")
    forecasts: list[tuple[object, object, object]] = []

    def team_forecast(*args: object, **kwargs: object) -> PlannedForecast:
        forecasts.append((kwargs["implementation_profile"], kwargs["model"], kwargs["variant"]))
        raise ValueError("no forecast in this test")

    container = SimpleNamespace(
        config=Config(engine="claude"),
        plan_route=lambda *args, **kwargs: (team, None),
        team_estimate=lambda plan, task_type, depth, cap, shape="pipeline", repair=True, docs_off=False: (
            estimate(plan, (), PriceTable({}), task_type, depth, cap, shape, None, repair, docs_off)
        ),
        change_plan=lambda request: ChangePlan(edit=(EditTarget("src/page.tsx", 0.9),)),
        plan_sizes=lambda plan: PlanSizes((LARGE_EDIT_TOKENS,), ()),
        team_forecast=team_forecast,
        shaped_options=lambda request, options: (options, ShapeChoice(Shape.PIPELINE)),
        fast_ready=lambda name: ready,
        routing_pinned=lambda mode: pinned,
        docs_choice=lambda request, options: None,
        shape_plan=lambda plan, shape, docs: plan,
        close=lambda: None,
    )
    services = ContainerServices(tmp_path)
    monkeypatch.setattr(services, "_container", lambda: cast("Container", container))
    request = MandateRequest(kind, "fix add", "wrong sum", constraints="same output", tests="sum")
    services.team_plan(request, MandateOptions())
    assert forecasts == [seen]


@pytest.mark.parametrize("pinned", [False, True])
def test_the_app_setup_says_whether_config_role_pins_keep_auto_balanced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned: bool
) -> None:
    modes: list[str] = []

    def routing_pinned(mode: str = "") -> bool:
        modes.append(mode)
        return pinned

    container = SimpleNamespace(
        config=Config(engine="claude", implementation_profile="auto"),
        engine=lambda name: None,
        known_models=lambda: (),
        has_forge_agents=lambda: True,
        init_estimate=lambda: None,
        routing_pinned=routing_pinned,
        close=lambda: None,
    )
    services = ContainerServices(tmp_path)
    monkeypatch.setattr(services, "_container", lambda: cast("Container", container))
    setup = services.mandate_setup()
    assert (setup.profile, setup.pinned) == ("auto", pinned)
    assert modes == [""]


def test_the_team_step_forecast_receives_the_whole_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    team = RoutePlan(RoutingPolicy(engines=("claude",)), None, None, (), (), "heuristic")
    received: list[object] = []

    def team_forecast(*args: object, **kwargs: object) -> PlannedForecast:
        received.append(kwargs.get("request"))
        raise ValueError("no forecast in this test")

    container = SimpleNamespace(
        config=Config(engine="claude"),
        plan_route=lambda *args, **kwargs: (team, None),
        team_estimate=lambda plan, task_type, depth, cap, shape="pipeline", repair=True, docs_off=False: (
            estimate(plan, (), PriceTable({}), task_type, depth, cap, shape, None, repair, docs_off)
        ),
        change_plan=lambda request: ChangePlan(),
        team_forecast=team_forecast,
        shaped_options=lambda request, options: (options, ShapeChoice(Shape.PIPELINE)),
        fast_ready=lambda name: True,
        routing_pinned=lambda mode: False,
        docs_choice=lambda request, options: None,
        shape_plan=lambda plan, shape, docs: plan,
        close=lambda: None,
    )
    services = ContainerServices(tmp_path)
    monkeypatch.setattr(services, "_container", lambda: cast("Container", container))
    request = MandateRequest(
        "feature",
        "Refactor del intérprete",
        phased_mandate(),
        tests="pytest tests/test_consult.py",
        out_of_scope="frontend",
    )
    _, found = services.team_plan(request, MandateOptions(profile="balanced"))
    assert received == [request]
    assert found.forecast_error is not None


@pytest.mark.parametrize(
    ("engine", "options", "names"),
    [
        ("codex", MandateOptions(engine="codex"), ("readonly", "telemetry")),
        (
            "codex",
            MandateOptions(engine="codex", budget_usd=2.0, max_turns=5, max_wall_min=30.0),
            ("spend", "readonly", "telemetry"),
        ),
        (
            "claude",
            MandateOptions(
                engine="claude", shape="scout", scout_mode="launch", budget_usd=2.0, max_turns=5
            ),
            ("spend", "turns", "readonly", "telemetry"),
        ),
    ],
    ids=["codex_unlimited", "codex_capped", "claude_scout_launch"],
)
def test_per_role_cards_show_the_guarantees_of_the_limits_each_launch_applies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine: str,
    options: MandateOptions,
    names: tuple[str, ...],
) -> None:
    model = ModelEntry(engine, f"{engine}-model", f"{engine}-model", engine)
    routes = tuple(
        RoleRoute(role, Tier.STANDARD, Tier.STANDARD, model, msg("route.policy", tier="standard"))
        for role in (Role.ANALYST, Role.SENIOR, Role.TESTER)
    )
    team = RoutePlan(RoutingPolicy(engines=(engine,)), None, None, (), routes, "heuristic")
    found = estimate(team, (), PriceTable({}), "feature", "normal", options.budget_usd or 0.0)
    container = SimpleNamespace(
        config=Config(engine=engine),
        pipeline_index_tools=lambda name: True,
        build_blocked=frozenset,
        close=lambda: None,
    )
    services = ContainerServices(tmp_path)
    monkeypatch.setattr(services, "_container", lambda: cast("Container", container))
    cards = services.team_cards(team, found, options, "feature")
    assert len(cards) == len(routes)
    assert {tuple(row.name for row in card.guarantees) for card in cards} == {names}


@pytest.mark.parametrize(
    ("options", "pured", "model", "mained"),
    [
        (
            MandateOptions(
                profile="balanced", model="claude-opus-5-5", variant="ultracode", pure=True
            ),
            ["claude-opus-5-5"],
            "claude-opus-5-5",
            [],
        ),
        (MandateOptions(profile="balanced", pure=True), ["claude-sonnet-5"], "", []),
        (
            MandateOptions(profile="balanced", model="claude-opus-5-5", variant="ultracode"),
            [],
            "claude-opus-5-5",
            ["claude-opus-5-5"],
        ),
        (MandateOptions(profile="balanced"), [], "", []),
    ],
    ids=["pure_on_its_model", "pure_on_the_plan_model", "not_pure", "plan_model"],
)
def test_the_team_step_forecasts_the_balanced_model_variant_and_pure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    options: MandateOptions,
    pured: list[str],
    model: str,
    mained: list[str],
) -> None:
    sonnet = ModelEntry("claude", "claude-sonnet-5", "claude-sonnet-5", "claude")
    orchestrator = RoleRoute(
        Role.ORCHESTRATOR, Tier.STANDARD, Tier.STANDARD, sonnet, msg("route.policy", tier="x")
    )
    team = RoutePlan(RoutingPolicy(engines=("claude",)), None, None, (), (orchestrator,), "h")
    pure_team = RoutePlan(RoutingPolicy(engines=("claude",)), None, None, (), (), "pure")
    main_team = RoutePlan(RoutingPolicy(engines=("claude",)), None, None, (), (), "main")
    asked: list[str] = []
    main_models: list[str] = []
    forecasts: list[tuple[object, object, object]] = []

    def pure_plan(plan: RoutePlan, chosen: str) -> RoutePlan:
        assert plan is team
        asked.append(chosen)
        return pure_team

    def main_model_plan(plan: RoutePlan, chosen: str) -> RoutePlan:
        assert plan is team
        main_models.append(chosen)
        return main_team

    def team_forecast(*args: object, **kwargs: object) -> PlannedForecast:
        forecasts.append((args[1], kwargs["model"], kwargs["variant"]))
        raise ValueError("no forecast in this test")

    container = SimpleNamespace(
        config=Config(engine="claude"),
        plan_route=lambda *args, **kwargs: (team, None),
        team_estimate=lambda plan, task_type, depth, cap, shape="pipeline", repair=True, docs_off=False: (
            estimate(plan, (), PriceTable({}), task_type, depth, cap, shape, None, repair, docs_off)
        ),
        change_plan=lambda request: ChangePlan(),
        team_forecast=team_forecast,
        shaped_options=lambda request, options: (options, ShapeChoice(Shape.PIPELINE)),
        fast_ready=lambda name: True,
        routing_pinned=lambda mode: False,
        docs_choice=lambda request, options: None,
        shape_plan=lambda plan, shape, docs: plan,
        pure_plan=pure_plan,
        main_model_plan=main_model_plan,
        close=lambda: None,
    )
    services = ContainerServices(tmp_path)
    monkeypatch.setattr(services, "_container", lambda: cast("Container", container))
    request = MandateRequest("refactor", "split the interpreter", constraints="same API")
    plan, _ = services.team_plan(request, options)
    assert asked == pured
    assert main_models == mained
    assert plan is (pure_team if pured else main_team if mained else team)
    assert forecasts == [(plan, model, options.variant)]


SPANISH_MANDATE = "TIPO: arreglo\nQUÉ: el total suma dos veces el envío\n"
UNRESOLVED_HOME = ("~ fix the typo in notes.md", "~cuanta-no-such-user/mandato.md")


def unresolvable_home(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setenv("USERPROFILE", str(root / "elsewhere"))
    monkeypatch.setenv("USERNAME", "someone")


@pytest.mark.parametrize("encoding", ["cp1252", "utf-16-le"])
def test_read_evidence_refuses_a_file_that_is_not_utf8_text(tmp_path: Path, encoding: str) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "mandato.md").write_bytes(SPANISH_MANDATE.encode(encoding))
    services = ContainerServices(tmp_path)
    with pytest.raises(NotText) as refused:
        services.read_evidence("docs/mandato.md")
    assert refused.value.path == "docs/mandato.md"
    assert english(refused.value.reason) == "docs/mandato.md is not UTF-8 text"


@pytest.mark.parametrize(
    "data",
    [
        SPANISH_MANDATE.encode("utf-8"),
        SPANISH_MANDATE.encode("utf-8-sig"),
        SPANISH_MANDATE.encode("utf-16"),
        b"\xfe\xff" + SPANISH_MANDATE.encode("utf-16-be"),
    ],
    ids=["utf8", "utf8_bom", "utf16_bom", "utf16_be_bom"],
)
def test_read_evidence_reads_utf8_and_files_with_a_byte_order_mark(
    tmp_path: Path, data: bytes
) -> None:
    (tmp_path / "mandato.md").write_bytes(data)
    services = ContainerServices(tmp_path)
    assert services.read_evidence("mandato.md") == SPANISH_MANDATE
    assert services.read_evidence(str(tmp_path / "mandato.md")) == SPANISH_MANDATE


@pytest.mark.parametrize("path", UNRESOLVED_HOME, ids=["sentence", "other_user"])
def test_read_evidence_says_a_file_is_missing_when_its_home_cannot_be_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    unresolvable_home(monkeypatch, tmp_path)
    with pytest.raises(FileNotFoundError) as missing:
        ContainerServices(tmp_path).read_evidence(path)
    assert missing.value.filename == path
