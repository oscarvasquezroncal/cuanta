from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cuanta.application.mandate_flow import MandateOptions
from cuanta.bootstrap import Container
from cuanta.domain.errors import DomainFailure
from cuanta.domain.mandate import MandateRequest
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
