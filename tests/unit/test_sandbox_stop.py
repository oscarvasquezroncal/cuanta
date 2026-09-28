from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cuanta.application.cross_engine import CompletionState, CrossReport
from cuanta.application.mandate_flow import MandateOptions
from cuanta.bootstrap import Container
from cuanta.domain.errors import DomainFailure
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import msg
from cuanta.tui import services as services_module
from cuanta.tui.services import ContainerServices

REQUEST = MandateRequest(type="bug", what="fix add", why="wrong", tests="t", out_of_scope="x")


def test_stop_while_the_copy_is_made_cancels_before_the_engine_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = ContainerServices(tmp_path)
    started: list[object] = []

    def run_sandboxed(self: Container, *args: Any, **kwargs: Any) -> object:
        assert services.stop_mandate()
        kwargs["on_start"](object(), object())
        started.append(True)
        return None

    monkeypatch.setattr(Container, "run_sandboxed", run_sandboxed)
    with pytest.raises(DomainFailure, match="stopped before the engine started"):
        services.run_mandate(
            REQUEST, 0, MandateOptions(simple=True, sandbox=True), lambda _: None, lambda _: None
        )
    assert started == []
    assert not services.stop_mandate()


class Team:
    def __init__(self, services: ContainerServices, stops: list[str]) -> None:
        self._services = services
        self._stops = stops

    def stop(self) -> bool:
        self._stops.append("team")
        return True

    def run(self, request: MandateRequest, plan: object, progress: object) -> CrossReport:
        assert self._services.stop_mandate()
        return CrossReport((), False, 0.0, msg("cross.stopped"), state=CompletionState.FAILED)


def test_stop_reaches_a_gpt_team_that_runs_one_launch_per_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = ContainerServices(tmp_path)
    stops: list[str] = []
    converted: list[CrossReport] = []
    monkeypatch.setattr(services_module, "role_plan", lambda *args: object())
    monkeypatch.setattr(Container, "cross_engine", lambda *args, **kwargs: Team(services, stops))
    monkeypatch.setattr(
        services_module, "cross_report", lambda run, report: converted.append(report)
    )
    services.run_mandate(REQUEST, 0, MandateOptions(engine="codex"), lambda _: None, lambda _: None)
    assert stops == ["team"]
    assert converted[0].stopped == msg("cross.stopped")
    assert not services.stop_mandate()


def test_stop_while_a_gpt_team_copy_is_made_cancels_before_any_role_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = ContainerServices(tmp_path)
    stops: list[str] = []

    def run_sandboxed_cross(self: Container, *args: Any, **kwargs: Any) -> object:
        assert services.stop_mandate()
        kwargs["on_start"](Team(services, stops))
        stops.append("started")
        return None

    monkeypatch.setattr(services_module, "role_plan", lambda *args: object())
    monkeypatch.setattr(Container, "run_sandboxed_cross", run_sandboxed_cross)
    with pytest.raises(DomainFailure, match="stopped before the engine started"):
        services.run_mandate(
            REQUEST,
            0,
            MandateOptions(engine="codex", sandbox=True),
            lambda _: None,
            lambda _: None,
        )
    assert stops == []
    assert not services.stop_mandate()
