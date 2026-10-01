from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.mandate_flow import MandateOptions
from cuanta.application.run_reports import RunReports
from cuanta.bootstrap import Container
from cuanta.domain.config import Config, layer_from_table, merge
from cuanta.domain.mandate import MandateRequest, Shape
from cuanta.domain.run_mode import (
    CLASSIC,
    V5,
    classic_config,
    classic_meta,
    classic_shape,
    run_mode,
)
from cuanta.ports.system import Completed
from tests.fakes import FakeRunner


def container(root: Path, config: Config) -> Container:
    return Container(
        project=root, config=config, runner=FakeRunner(), clock=FixedClock(), home=root
    )


@pytest.mark.parametrize(("value", "expected"), [(False, False), ("off", False), (True, True)])
def test_the_governor_key_is_on_by_default_and_can_be_turned_off(
    value: object, expected: bool
) -> None:
    assert Config().governor is True
    assert merge([layer_from_table({"runs": {"governor": value}})]).governor is expected


def test_classic_turns_off_v5_steering_and_discipline_and_keeps_docs_on() -> None:
    base = replace(Config(), docs_mode="off", read_discipline=True)
    classic = classic_config(base)
    assert classic.docs_mode == "on"
    assert not classic.read_discipline and not classic.pipeline_read_discipline
    assert classic.governor is False
    assert (
        replace(
            classic,
            docs_mode="off",
            read_discipline=True,
            pipeline_read_discipline=True,
            governor=True,
            implementation_profile=base.implementation_profile,
        )
        == base
    )
    assert classic.implementation_profile == "balanced"
    assert classic_shape("", "feature", False) == "pipeline"
    assert classic_shape("", "bug", False) == "pipeline"
    assert classic_shape("", "investigation", False) == ""
    assert classic_shape("single", "investigation", False) == "single"
    assert classic_shape("", "feature", True) == ""
    assert classic_shape("pipeline", "refactor", False) == "pipeline"
    assert classic_meta(CLASSIC) == {"mode": "classic"} and classic_meta("") == {}
    assert run_mode({"mode": "classic"}) == CLASSIC
    assert run_mode({}) == V5 and run_mode({"mode": "other"}) == V5


def test_runs_governor_off_wires_no_governor_into_either_runner(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    governed = container(tmp_path, Config())
    assert governed.mandate_flow(ledger)._governor is not None
    assert governed.cross_engine(ledger, 1.0)._governor is not None
    quiet = container(tmp_path, replace(Config(), governor=False))
    assert quiet.mandate_flow(ledger)._governor is None
    assert quiet.cross_engine(ledger, 1.0)._governor is None


def test_a_classic_container_records_the_mode_on_every_saved_run(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    classic = container(tmp_path, Config())
    classic.classic()
    assert classic.run_mode == CLASSIC and classic.config == classic_config(Config())
    assert classic.pipeline_read_discipline("claude") is False
    flow = classic.mandate_flow(ledger)
    assert flow._governor is None and flow._pipeline_read_discipline is False
    assert classic.sandbox_container(tmp_path / "copy").run_mode == CLASSIC
    pipeline = classic.cross_engine(ledger, 1.0)
    assert pipeline._governor is None
    save = pipeline._save_metrics
    assert save is not None
    save("run-1", {"spent_usd": 0.2})
    reports = RunReports(LocalWorkspace(tmp_path))
    assert reports.meta("run-1") == {"spent_usd": 0.2, "mode": "classic"}
    plain = container(tmp_path, Config()).cross_engine(ledger, 1.0)._save_metrics
    assert plain is not None
    plain("run-2", {"spent_usd": 0.1})
    assert reports.meta("run-2") == {"spent_usd": 0.1}


def test_shaped_options_resolve_the_profile_before_any_team_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    feature = MandateRequest(
        "feature", "add a badge", "users ask", tests="badge", out_of_scope="cart"
    )
    auto = container(tmp_path, Config())
    asked: list[str] = []

    def ready(name: str) -> bool:
        asked.append(name)
        return True

    def no_shape(*args: object) -> object:
        raise AssertionError("a fast run needs no team shape")

    monkeypatch.setattr(auto, "fast_ready", ready)
    monkeypatch.setattr(auto, "team_shape", no_shape)
    options, choice = auto.shaped_options(feature, MandateOptions())
    assert options.profile == "fast" and choice.shape is Shape.SINGLE and asked == ["claude"]
    with pytest.raises(AssertionError, match="no team shape"):
        auto.shaped_options(feature, MandateOptions(), cross_engine=True)
    classic = container(tmp_path, Config())
    classic.classic()
    monkeypatch.setattr(classic, "fast_ready", ready)
    monkeypatch.setattr(classic, "team_shape", no_shape)
    with pytest.raises(AssertionError, match="no team shape"):
        classic.shaped_options(feature, MandateOptions(shape="single"))
    assert asked == ["claude"]


def test_fast_needs_an_engine_that_takes_turns_and_a_project_check(tmp_path: Path) -> None:
    def ready(help_text: str) -> tuple[tuple[str, ...], bool]:
        runner = FakeRunner(binaries={"claude": "/bin/claude"})
        runner.responses["claude --help"] = Completed(0, help_text, "")
        found = Container(
            project=tmp_path, config=Config(), runner=runner, clock=FixedClock(), home=tmp_path
        )
        first = found.fast_ready("claude")
        assert found.fast_ready("claude") is first
        assert sum(call[-1] == "--help" for call in runner.calls) == 1
        return found.static_checks(), first

    assert ready("--input-format stream-json") == ((), False)
    (tmp_path / "package.json").write_text('{"scripts": {"lint": "eslint ."}}', encoding="utf-8")
    assert ready("--input-format stream-json") == (("npm run lint",), True)
    assert ready("--output-format json") == (("npm run lint",), False)
