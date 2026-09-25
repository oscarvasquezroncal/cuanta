from __future__ import annotations

from collections.abc import Callable

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.adapters.system.prices import load_prices
from cuanta.application.engine_run import EngineLauncher, LaunchSpec
from cuanta.domain.engine import (
    EngineEvent,
    EngineOutcome,
    EngineRequest,
    ModelUsage,
    RunResult,
)


class SilentEngine:
    def __init__(self, cost: float | None, model: str) -> None:
        self._cost = cost
        self._model = model

    @property
    def name(self) -> str:
        return "codex"

    def available(self) -> bool:
        return True

    def version(self) -> str:
        return "x"

    def missing_flags(self) -> tuple[str, ...]:
        return ()

    def cancel(self) -> None:
        return None

    def command(self, request: EngineRequest) -> list[str]:
        return ["codex"]

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        usage = ModelUsage(self._model, input_tokens=1_000_000, output_tokens=0)
        result = RunResult(True, "success", self._cost, 1, "s", (usage,), "done")
        on_event(result)
        return EngineOutcome(0, result, 0)


def launch(cost: float | None, model: str) -> float | None:
    launcher = EngineLauncher(
        engine=SilentEngine(cost, model),
        ledger=MemoryLedger(),
        clock=FixedClock(),
        new_run_id=lambda: "RUN1",
        entropy=lambda size: b"\x01" * size,
        project_name="shop",
        port=4318,
        listener=None,
        prices=load_prices(),
    )
    spec = LaunchSpec(kind="mandate", prompt="hi", cwd=".", allowed_tools=())
    return launcher.launch(spec, lambda event: None).run.cost_usd


def test_reported_cost_wins() -> None:
    assert launch(0.42, "gpt-5.5") == 0.42


def test_unreported_cost_is_estimated_from_the_price_table() -> None:
    price = load_prices().lookup("gpt-5.5")
    assert price is not None
    assert launch(None, "gpt-5.5") == pytest.approx(price.input)


def test_unknown_price_stays_unknown() -> None:
    assert launch(None, "gpt-9-private") is None
