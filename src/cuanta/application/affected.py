from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from cuanta.application.gateway import GatewayReport, RunGateway
from cuanta.domain.affected import (
    BASELINE_PHASE,
    BASELINE_RUN,
    Selection,
    changed_files,
    select,
)
from cuanta.domain.config import Config
from cuanta.domain.detection import Stack, VerifyTier
from cuanta.domain.ledger import Snapshot
from cuanta.ports.ledger import Ledger
from cuanta.ports.workspace import Workspace

START_PHASE = "start"


@dataclass(frozen=True, slots=True)
class ScopedReport:
    report: GatewayReport
    selection: Selection | None


class AffectedGateway:
    def __init__(
        self,
        gateway: RunGateway,
        workspace: Workspace,
        ledger: Ledger,
        exclusions: frozenset[str],
        neighbours: Callable[[], Mapping[str, frozenset[str]]],
    ) -> None:
        self._gateway = gateway
        self._workspace = workspace
        self._ledger = ledger
        self._exclusions = exclusions
        self._neighbours = neighbours

    def hashes(self) -> dict[str, str]:
        scan = self._workspace.scan(self._exclusions, collect_files=True)
        found: dict[str, str] = {}
        for path in scan.files:
            digest = self._workspace.sha256(path)
            if digest is not None:
                found[path] = digest
        return found

    def baseline(self, run_id: str) -> dict[str, str]:
        if run_id:
            started = self._ledger.snapshots(run_id, START_PHASE)
            if started:
                return {item.path: item.sha256 for item in started}
        stored = self._ledger.snapshots(BASELINE_RUN, BASELINE_PHASE)
        return {item.path: item.sha256 for item in stored}

    def selection(self, runner: str, run_id: str, current: Mapping[str, str]) -> Selection:
        baseline = self.baseline(run_id)
        changed = changed_files(baseline, current)
        return select(runner, changed, current, self._neighbours(), bool(baseline))

    def remember(self, current: Mapping[str, str]) -> None:
        self._ledger.add_snapshots(
            [
                Snapshot(BASELINE_RUN, BASELINE_PHASE, path, digest)
                for path, digest in current.items()
            ]
        )

    def run(
        self,
        stack: Stack,
        config: Config,
        tier: VerifyTier,
        affected: bool,
        run_id: str = "",
    ) -> ScopedReport:
        current = self.hashes()
        chosen: Selection | None = None
        if affected:
            choice, _ = self._gateway.choose(stack, config, tier)
            chosen = self.selection(choice.name, run_id, current)
        arguments = chosen.arguments if chosen is not None and not chosen.full else ()
        report = self._gateway.run(stack, config, tier, run_id=run_id, arguments=arguments)
        if not arguments:
            self.remember(current)
        return ScopedReport(report, chosen)
