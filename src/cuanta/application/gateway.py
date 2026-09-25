from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cuanta.domain.affected import narrowed
from cuanta.domain.capsules import capsule_id
from cuanta.domain.config import Config
from cuanta.domain.detection import Stack, VerifyTier
from cuanta.domain.errors import NotAvailable
from cuanta.domain.gateway import RunnerChoice, choose_runner, split_command
from cuanta.domain.ledger import Capsule, SignatureRecord, TestRunRecord
from cuanta.domain.testing import (
    GatewayStatus,
    Signature,
    TestOutcome,
    cluster,
    gateway_status,
    persistent_signatures,
    summary,
)
from cuanta.ports.capsules import CapsuleStore
from cuanta.ports.ledger import Ledger
from cuanta.ports.system import Clock
from cuanta.ports.test_runner import TestRunner

CAPSULE_DIR = ".cuanta/capsules"


@dataclass(frozen=True, slots=True)
class GatewayReport:
    status: GatewayStatus
    runner: str
    command: str
    outcome: TestOutcome
    signatures: tuple[Signature, ...]
    capsule: str
    persistent: tuple[str, ...]
    summary: tuple[str, ...]
    test_run_id: str
    run_id: str
    triage: tuple[tuple[str, str], ...] = ()

    def contract(self) -> dict[str, Any]:
        triage = dict(self.triage)
        outcome = self.outcome
        failures = [
            {
                "test": signature.first,
                "error": signature.verbatim,
                "likely_cause": signature.location or None,
            }
            for signature in self.signatures
        ]
        return {
            "status": self.status.value,
            "run": {
                "command": self.command,
                "result": "\n".join(self.summary),
                "passed": outcome.passed,
                "failed": outcome.failed + outcome.errored,
            },
            "failures": failures,
            "signatures": [
                {
                    "id": signature.id,
                    "error": signature.verbatim,
                    "tests": signature.tests,
                    "first": signature.first,
                    "location": signature.location,
                    "triage": triage.get(signature.id, ""),
                }
                for signature in self.signatures
            ],
            "log_capsule": self.capsule,
            "persistent": list(self.persistent),
            "errored": outcome.errored,
            "skipped": outcome.skipped,
            "duration_s": round(outcome.duration_s, 3),
            "runner": self.runner,
            "test_run_id": self.test_run_id,
            "run_id": self.run_id,
        }


class RunGateway:
    def __init__(
        self,
        root: Path,
        runners: Callable[[str], TestRunner | None],
        ledger: Ledger,
        capsules: CapsuleStore,
        clock: Clock,
        new_id: Callable[[], str],
        scratch: Path,
        triage: Callable[[Signature, str], str] | None = None,
    ) -> None:
        self._triage = triage
        self._root = root
        self._runners = runners
        self._ledger = ledger
        self._capsules = capsules
        self._clock = clock
        self._new_id = new_id
        self._scratch = scratch

    def choose(
        self, stack: Stack, config: Config, tier: VerifyTier
    ) -> tuple[RunnerChoice, TestRunner]:
        choice = choose_runner(
            stack.test_runner, stack.test_command, config.test_runner, config.test_command
        )
        if choice is None:
            raise NotAvailable(
                f"no test runner found · VERIFY_TIER={tier}",
                "whiskers too short: add a test runner, or set test.command in .cuanta/config.toml",
            )
        runner = self._runners(choice.name)
        if runner is None:
            raise NotAvailable(
                f"unknown test runner: {choice.name}", "set test.runner in .cuanta/config.toml"
            )
        return choice, runner

    def run(
        self,
        stack: Stack,
        config: Config,
        tier: VerifyTier,
        run_id: str = "",
        env: Mapping[str, str] | None = None,
        arguments: Sequence[str] = (),
    ) -> GatewayReport:
        choice, runner = self.choose(stack, config, tier)
        windows = os.name == "nt"
        base = (
            split_command(choice.command, windows) if choice.command else runner.default_command()
        )
        if arguments:
            base = tuple(narrowed(base, choice.name, arguments))
        if not base:
            raise NotAvailable(
                f"{choice.name} needs a command", "set test.command in .cuanta/config.toml"
            )
        started_at = self._clock.now_iso()
        result = runner.run(base, self._root, self._scratch, env)
        outcome = result.outcome
        signatures = cluster(outcome.failures, str(self._root))
        digest, _, size = self._capsules.put(result.output)
        capsule = capsule_id(digest)
        lines = summary(outcome, signatures, runner.name)
        self._ledger.add_capsule(
            Capsule(
                id=capsule,
                sha256=digest,
                path=f"{CAPSULE_DIR}/{digest}.log",
                kind="test",
                size_bytes=size,
                lines=result.output.count("\n") + (1 if result.output else 0),
                summary="\n".join(lines),
                created_at=started_at,
                run_id=run_id,
            )
        )
        persistent: tuple[str, ...] = ()
        if run_id:
            previous = self._ledger.test_runs(run_id=run_id, limit=1)
            if previous:
                previous_ids = [
                    item.signature_id for item in self._ledger.signatures(previous[0].id)
                ]
                persistent = persistent_signatures(previous_ids, [item.id for item in signatures])
        status = gateway_status(outcome, persistent)
        triage: tuple[tuple[str, str], ...] = ()
        if self._triage is not None:
            triage = tuple((item.id, self._triage(item, run_id)) for item in signatures)
        test_run_id = self._new_id()
        self._ledger.add_test_run(
            TestRunRecord(
                id=test_run_id,
                run_id=run_id,
                runner=runner.name,
                command=result.command,
                status=status.value,
                passed=outcome.passed,
                failed=outcome.failed,
                errored=outcome.errored,
                skipped=outcome.skipped,
                duration_s=outcome.duration_s,
                capsule_id=capsule,
                started_at=started_at,
                exit_code=outcome.exit_code,
            ),
            [
                SignatureRecord(
                    test_run_id=test_run_id,
                    signature_id=signature.id,
                    error=signature.error,
                    verbatim=signature.verbatim,
                    first_test=signature.first,
                    location=signature.location,
                    tests=signature.tests,
                )
                for signature in signatures
            ],
        )
        return GatewayReport(
            status=status,
            runner=runner.name,
            command=result.command,
            outcome=outcome,
            signatures=signatures,
            capsule=capsule,
            persistent=persistent,
            summary=lines,
            test_run_id=test_run_id,
            run_id=run_id,
            triage=triage,
        )
