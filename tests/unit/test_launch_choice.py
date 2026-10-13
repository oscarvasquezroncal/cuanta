from __future__ import annotations

from pathlib import Path

import pytest

from cuanta.adapters.system.clock import FixedClock
from cuanta.application.mandate_flow import MandateOptions
from cuanta.bootstrap import Container
from cuanta.domain.change_plan import ChangePlan, EditTarget
from cuanta.domain.config import Config
from cuanta.domain.instinct import heuristic_scope
from cuanta.domain.launch_choice import (
    LaunchScope,
    scope_context,
    scope_message,
    scope_payload,
    single_effort,
)
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import english
from cuanta.ports.system import Completed
from cuanta.tui.i18n import Catalog
from tests.cli.conftest import CLAUDE_HELP
from tests.fakes import FakeRunner

NORMAL = LaunchScope("normal", 0.71, "jev", plan_files=2, anchors=1, history=3, configured="jev")
FALLBACK = LaunchScope(
    "complex", 0.6, "heuristic", "jev has no remote consent", 7, 0, 0, configured="jev"
)


def test_the_launch_names_the_decision_its_backend_and_why_in_both_languages() -> None:
    assert english(scope_message(NORMAL)) == (
        "Instinct (jev): normal change, p=0.71 → one session (Sonnet) · plan 2 files, "
        "1 cited ranges, 3 earlier runs of this type"
    )
    assert english(scope_message(FALLBACK)).startswith(
        "Instinct (heuristic; jev unavailable): complex change, p=0.60 → team"
    )
    spanish = Catalog("es").message(scope_message(NORMAL))
    assert spanish.startswith("Instinct (jev): cambio normal, p=0.71 → una sesión (Sonnet)")
    assert scope_payload(FALLBACK)["fallback"] == "jev has no remote consent"
    assert scope_payload(NORMAL)["shape"] == "single"


def test_a_wide_plan_or_many_cited_ranges_push_the_heuristic_to_complex() -> None:
    small = scope_context("bug", "Fix the cart total", "src/cart.py", 1, 1, 0)
    assert heuristic_scope(small).option == "normal"
    wide = scope_context("feature", "Add a migration across every subsystem", "src", 8, 6, 0)
    assert heuristic_scope(wide).option == "complex"
    assert (single_effort("feature"), single_effort("bug")) == ("medium", "high")


def container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str = "2.1.296 (Claude Code)"
) -> Container:
    runner = FakeRunner(
        binaries={"claude": "/bin/claude"},
        responses={
            "claude --version": Completed(0, version + "\n", ""),
            "claude --help": Completed(0, CLAUDE_HELP, ""),
        },
    )
    built = Container(tmp_path, Config(), runner=runner, clock=FixedClock(), home=tmp_path / "h")
    monkeypatch.setattr(built, "fast_ready", lambda name: True)
    return built


REQUEST = MandateRequest("bug", "Fix the cart total", "doubles shipping", where="src/cart.py")


def test_a_normal_change_runs_one_sonnet_session_with_the_effort_of_its_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = container(tmp_path, monkeypatch)
    monkeypatch.setattr(built, "launch_scope", lambda request: NORMAL)
    try:
        options, _ = built.shaped_options(REQUEST, MandateOptions())
        assert (options.profile, options.model, options.variant) == (
            "fast",
            "claude-sonnet-5-5",
            "high",
        )
        assert built.launch_decision == NORMAL
        assert built.advisor_choice("", options.model) == "opus"
        older = container(tmp_path / "old", monkeypatch, "2.1.280")
        monkeypatch.setattr(older, "launch_scope", lambda request: NORMAL)
        legacy, _ = older.shaped_options(REQUEST, MandateOptions())
        assert legacy.model == "claude-sonnet-5"
        pinned, _ = built.shaped_options(REQUEST, MandateOptions(model="claude-opus-5-5"))
        assert pinned.model == "claude-opus-5-5"
    finally:
        built.close()


def test_a_complex_change_runs_the_team_with_haiku_helpers_and_a_sonnet_senior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = container(tmp_path, monkeypatch)
    monkeypatch.setattr(built, "launch_scope", lambda request: FALLBACK)
    try:
        options, _ = built.shaped_options(REQUEST, MandateOptions())
        assert options.profile == "balanced" and not options.model
        assert dict(options.route.role_models) == {
            "analyst": "claude-haiku-5-5",
            "senior": "claude-sonnet-5-5",
            "tester": "claude-haiku-5-5",
        }
        assert dict(options.role_efforts) == {
            "analyst": "medium",
            "senior": "high",
            "tester": "medium",
            "docs": "low",
        }
    finally:
        built.close()


def test_an_explicit_team_without_pins_takes_the_team_defaults_not_opus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = container(tmp_path, monkeypatch)
    monkeypatch.setattr(built, "launch_scope", lambda request: NORMAL)
    try:
        options, _ = built.shaped_options(REQUEST, MandateOptions(shape="pipeline"))
        assert options.profile == "balanced" and not options.model
        assert dict(options.route.role_models) == {
            "analyst": "claude-haiku-5-5",
            "senior": "claude-sonnet-5-5",
            "tester": "claude-haiku-5-5",
        }
        assert built.launch_decision is None
        pinned, _ = built.shaped_options(
            REQUEST, MandateOptions(shape="pipeline", model="claude-opus-5-5")
        )
        assert pinned.model == "claude-opus-5-5" and not pinned.route.role_models
        chosen, _ = built.shaped_options(
            REQUEST, MandateOptions(shape="pipeline", profile="balanced")
        )
        assert not chosen.route.role_models
    finally:
        built.close()


def test_the_scope_decision_reads_the_plan_the_cited_ranges_and_the_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = container(tmp_path, monkeypatch)
    plan = ChangePlan(tuple(EditTarget(f"src/part_{index}.py", 1.0) for index in range(8)))
    monkeypatch.setattr(built, "change_plan", lambda request: plan)
    try:
        wide = MandateRequest(
            "feature", "Add a migration across every subsystem", "see src/a.py:1-9", where="src"
        )
        found = built.launch_scope(wide)
        assert (found.option, found.backend, found.plan_files, found.anchors) == (
            "complex",
            "heuristic",
            8,
            1,
        )
        assert found.history == 0 and not found.fallback
    finally:
        built.close()


def test_costly_choices_need_an_opt_in_that_names_their_multiplier() -> None:
    from cuanta.adapters.system.prices import load_prices
    from cuanta.domain.launch_choice import opt_ins

    prices = load_prices()
    assert opt_ins("claude-sonnet-5-5", False, "high", False, prices) == ()
    assert opt_ins("", False, "", True, prices) == ()
    opus = [english(item) for item in opt_ins("claude-opus-5-5", False, "", False, prices)]
    assert opus == ["claude-opus-5-5 as the main model: about 2.0× the default Sonnet 5.5 price"]
    fast = [english(item) for item in opt_ins("claude-opus-5-5", False, "fast", False, prices)]
    assert fast[-1] == "fast mode: about 2.0× the claude-opus-5-5 price"
    pure = [english(item) for item in opt_ins("claude-opus-5-5", True, "xhigh", False, prices)]
    assert len(pure) == 3 and pure[2].startswith("effort xhigh")
    team = [english(item) for item in opt_ins("", False, "ultracode", True, prices)]
    assert team == [
        "ultracode with a team: xhigh effort and workflows in every role; "
        "no measured multiplier yet"
    ]
    spanish = Catalog("es").message(opt_ins("claude-opus-5-5", False, "", False, prices)[0])
    assert "2.0×" in spanish and "Sonnet 5.5" in spanish
