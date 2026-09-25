from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from cuanta.adapters.storage.capsule_store import FileCapsuleStore
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.adapters.system.process_runner import SubprocessRunner
from cuanta.adapters.testing.pytest_runner import PytestRunner
from cuanta.application.gateway import RunGateway
from cuanta.domain.config import Config
from cuanta.domain.detection import Stack, VerifyTier
from cuanta.domain.errors import NotAvailable
from cuanta.domain.testing import GatewayStatus
from cuanta.ports.test_runner import TestRunner
from tests.fakes import copy_repo


def _pytest_command() -> str:
    return f'"{sys.executable}" -m pytest -p no:cacheprovider'


def _gateway(root: Path, ledger: MemoryLedger) -> RunGateway:
    counter = iter(range(1, 1000))

    def runners(name: str) -> TestRunner | None:
        return PytestRunner(SubprocessRunner()) if name == "pytest" else None

    return RunGateway(
        root=root,
        runners=runners,
        ledger=ledger,
        capsules=FileCapsuleStore(root / ".cuanta" / "capsules"),
        clock=FixedClock(),
        new_id=lambda: f"T{next(counter):04d}",
        scratch=root / ".cuanta" / "tmp",
    )


@pytest.fixture(scope="module")
def failing_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return copy_repo("failing_suite", tmp_path_factory.mktemp("suite"))


def test_fifty_failures_from_three_causes_yield_three_signatures(failing_root: Path) -> None:
    ledger = MemoryLedger()
    stack = Stack(language="python", test_runner="pytest", test_command=_pytest_command())
    report = _gateway(failing_root, ledger).run(stack, Config(), VerifyTier.STRONG)
    assert report.status is GatewayStatus.RED
    assert report.outcome.failed == 50
    assert report.outcome.passed == 7
    assert len(report.signatures) == 3
    assert sorted(signature.tests for signature in report.signatures) == [15, 15, 20]
    contract = report.contract()
    assert len(contract["failures"]) == 3
    assert set(contract) >= {"status", "run", "failures", "signatures", "log_capsule", "persistent"}
    assert contract["run"]["failed"] == 50
    assert len(contract["run"]["result"].splitlines()) <= 12
    locations = {signature.location.split(":")[0] for signature in report.signatures}
    assert locations == {"tests/test_causes.py"}
    capsule = ledger.get_capsule(report.capsule)
    assert capsule is not None
    assert (failing_root / capsule.path).exists()
    json.dumps(contract)


def test_circuit_breaker_marks_persistent(failing_root: Path) -> None:
    ledger = MemoryLedger()
    stack = Stack(language="python", test_runner="pytest", test_command=_pytest_command())
    gateway = _gateway(failing_root, ledger)
    first = gateway.run(stack, Config(), VerifyTier.STRONG, run_id="RUN1")
    assert first.status is GatewayStatus.RED
    second = gateway.run(stack, Config(), VerifyTier.STRONG, run_id="RUN1")
    assert second.status is GatewayStatus.PERSISTENT
    assert len(second.persistent) == 3
    other = gateway.run(stack, Config(), VerifyTier.STRONG, run_id="RUN2")
    assert other.status is GatewayStatus.RED


def test_no_runner_is_not_available(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path, MemoryLedger())
    with pytest.raises(NotAvailable, match="VERIFY_TIER=weak"):
        gateway.run(Stack(), Config(), VerifyTier.WEAK)


def test_green_suite(tmp_path: Path) -> None:
    root = copy_repo("python_strong", tmp_path)
    stack = Stack(language="python", test_runner="pytest", test_command=_pytest_command())
    report = _gateway(root, MemoryLedger()).run(stack, Config(), VerifyTier.STRONG)
    assert report.status is GatewayStatus.GREEN
    assert report.outcome.passed == 1
    assert report.signatures == ()
