from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.run_reports import RunReports
from cuanta.bootstrap import Container
from cuanta.domain.config import Config, layer_from_table, merge
from cuanta.domain.run_mode import (
    CLASSIC,
    V5,
    classic_config,
    classic_meta,
    classic_shape,
    run_mode,
)
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
        )
        == base
    )
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
