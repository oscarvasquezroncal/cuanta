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
    def __init__(
        self, cost: float | None, model: str, result: RunResult | None = None, name: str = "codex"
    ) -> None:
        self._cost = cost
        self._model = model
        self._result = result
        self._name = name

    @property
    def name(self) -> str:
        return self._name

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
        result = self._result or RunResult(True, "success", self._cost, 1, "s", (usage,), "done")
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


@pytest.mark.parametrize(
    ("subtype", "terminal_reason", "expected"),
    [
        ("success", "completed", "success"),
        ("error_max_turns", "max_turns", "error_max_turns"),
        ("error", "max_turns", "error_max_turns"),
    ],
)
def test_launch_records_turns_and_terminal_reason(
    subtype: str, terminal_reason: str, expected: str
) -> None:
    result = RunResult(
        subtype == "success", subtype, 0.01, 21, "s", terminal_reason=terminal_reason
    )
    ledger = MemoryLedger()
    launcher = EngineLauncher(
        engine=SilentEngine(None, "haiku", result, "claude"),
        ledger=ledger,
        clock=FixedClock(),
        new_run_id=lambda: "RUN1",
        entropy=lambda size: b"\x01" * size,
        project_name="shop",
        port=4318,
        listener=None,
    )
    spec = LaunchSpec(kind="mandate", prompt="hi", cwd=".", allowed_tools=(), max_turns=20)
    launched = launcher.launch(spec, lambda event: None)
    assert launched.run.max_turns == 20
    assert launched.run.turns == 21
    assert launched.run.end_reason == expected
    assert ledger.get_run("RUN1") == launched.run
