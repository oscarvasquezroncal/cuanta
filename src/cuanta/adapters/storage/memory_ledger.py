from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from cuanta.adapters.storage.migrations import LATEST_VERSION
from cuanta.domain.ledger import (
    Baseline,
    Capsule,
    Decision,
    LedgerEvent,
    RouteAudit,
    RoutingDecision,
    Run,
    SignatureRecord,
    Snapshot,
    TestRunRecord,
)
from cuanta.ports.ledger import EventQuery


class MemoryLedger:
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._events: list[LedgerEvent] = []
        self._test_runs: dict[str, TestRunRecord] = {}
        self._signatures: dict[str, list[SignatureRecord]] = {}
        self._capsules: dict[str, Capsule] = {}
        self._decisions: list[Decision] = []
        self._routing: list[RoutingDecision] = []
        self._audits: dict[tuple[str, str], RouteAudit] = {}
        self._snapshots: dict[tuple[str, str, str], Snapshot] = {}
        self._baselines: list[Baseline] = []

    def schema_version(self) -> int:
        return LATEST_VERSION

    def add_run(self, run: Run) -> None:
        self._runs[run.id] = run

    def update_run(self, run: Run) -> None:
        self._runs[run.id] = run

    def get_run(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def runs(self, kind: str = "", hu_ref: str = "", limit: int = 0) -> tuple[Run, ...]:
        selected = [
            run
            for run in sorted(self._runs.values(), key=lambda run: run.id, reverse=True)
            if (not kind or run.kind == kind) and (not hu_ref or run.hu_ref == hu_ref)
        ]
        return tuple(selected[:limit] if limit else selected)

    def run_by_trace(self, trace_id: str) -> Run | None:
        if not trace_id:
            return None
        return next((run for run in self._runs.values() if run.trace_id == trace_id), None)

    def add_events(self, events: Sequence[LedgerEvent]) -> int:
        start = max((event.id for event in self._events), default=0)
        for offset, event in enumerate(events, 1):
            self._events.append(replace(event, id=start + offset))
        return len(events)

    def delete_events(self, source: str) -> int:
        kept = [event for event in self._events if event.source != source]
        removed = len(self._events) - len(kept)
        self._events[:] = kept
        return removed

    def events(self, query: EventQuery) -> tuple[LedgerEvent, ...]:
        selected = [
            event
            for event in self._events
            if (not query.run_id or event.run_id == query.run_id)
            and (not query.session_id or event.session_id == query.session_id)
            and (not query.trace_id or event.trace_id == query.trace_id)
            and (not query.since or event.ts >= query.since)
        ]
        selected.sort(key=lambda event: (event.ts, event.id))
        return tuple(selected[: query.limit] if query.limit else selected)

    def add_test_run(self, record: TestRunRecord, signatures: Sequence[SignatureRecord]) -> None:
        self._test_runs[record.id] = record
        self._signatures[record.id] = list(signatures)

    def _ordered_test_runs(self) -> list[TestRunRecord]:
        return sorted(
            self._test_runs.values(),
            key=lambda record: (record.started_at, record.id),
            reverse=True,
        )

    def test_runs(self, run_id: str = "", limit: int = 0) -> tuple[TestRunRecord, ...]:
        selected = [
            record for record in self._ordered_test_runs() if not run_id or record.run_id == run_id
        ]
        return tuple(selected[:limit] if limit else selected)

    def signatures(self, test_run_id: str) -> tuple[SignatureRecord, ...]:
        items = self._signatures.get(test_run_id, [])
        return tuple(sorted(items, key=lambda item: (-item.tests, item.first_test)))

    def signature_history(self, signature_id: str) -> tuple[TestRunRecord, ...]:
        return tuple(
            record
            for record in self._ordered_test_runs()
            if any(
                item.signature_id == signature_id for item in self._signatures.get(record.id, [])
            )
        )

    def add_capsule(self, capsule: Capsule) -> None:
        self._capsules[capsule.id] = capsule

    def get_capsule(self, reference: str) -> Capsule | None:
        key = reference.removeprefix("cap:")
        if not key:
            return None
        exact = self._capsules.get(f"cap:{key}")
        if exact is not None:
            return exact
        matches = [
            capsule for name, capsule in self._capsules.items() if name.startswith(f"cap:{key}")
        ]
        return matches[0] if len(matches) == 1 else None

    def add_decision(self, decision: Decision) -> int:
        identifier = len(self._decisions) + 1
        self._decisions.append(replace(decision, id=identifier))
        return identifier

    def add_routing_decision(self, decision: RoutingDecision) -> int:
        identifier = len(self._routing) + 1
        self._routing.append(replace(decision, id=identifier))
        return identifier

    def routing_decisions(self, run_id: str = "", limit: int = 0) -> tuple[RoutingDecision, ...]:
        found = [item for item in reversed(self._routing) if not run_id or item.run_id == run_id]
        return tuple(found[:limit] if limit else found)

    def set_routing_outcome(
        self, run_id: str, outcome: str, cost_usd: float | None, retries: int
    ) -> None:
        self._routing = [
            replace(item, outcome=outcome, cost_usd=cost_usd, retries=retries)
            if item.run_id == run_id
            else item
            for item in self._routing
        ]

    def set_routing_accepted(self, run_id: str, accepted: bool) -> None:
        self._routing = [
            replace(item, accepted=1 if accepted else 0) if item.run_id == run_id else item
            for item in self._routing
        ]

    def add_route_audits(self, audits: Sequence[RouteAudit]) -> None:
        for audit in audits:
            self._audits[(audit.run_id, audit.agent)] = audit

    def route_audits(self, run_id: str) -> tuple[RouteAudit, ...]:
        return tuple(audit for (owner, _), audit in sorted(self._audits.items()) if owner == run_id)

    def set_decision_outcome(self, decision_id: int, outcome: str) -> None:
        self._decisions = [
            replace(item, outcome=outcome) if item.id == decision_id else item
            for item in self._decisions
        ]

    def decisions(self, run_id: str = "", limit: int = 0) -> tuple[Decision, ...]:
        selected = [
            item for item in reversed(self._decisions) if not run_id or item.run_id == run_id
        ]
        return tuple(selected[:limit] if limit else selected)

    def preview_decision(self, request_hash: str, question: str) -> int | None:
        for item in reversed(self._decisions):
            if (
                item.preview
                and not item.run_id
                and item.request_hash == request_hash
                and item.question == question
            ):
                return item.id
        return None

    def link_decisions(self, request_hash: str, run_id: str) -> int:
        linked = 0
        updated: list[Decision] = []
        for item in self._decisions:
            matches = item.request_hash == request_hash and not item.run_id
            updated.append(replace(item, run_id=run_id) if matches else item)
            linked += int(matches)
        self._decisions = updated
        return linked

    def close_run_decisions(self, run_id: str, outcome: str) -> None:
        self._decisions = [
            replace(item, outcome=outcome) if item.run_id == run_id and not item.outcome else item
            for item in self._decisions
        ]

    def add_snapshots(self, snapshots: Sequence[Snapshot]) -> None:
        for snapshot in snapshots:
            self._snapshots[(snapshot.run_id, snapshot.phase, snapshot.path)] = snapshot

    def snapshots(self, run_id: str, phase: str) -> tuple[Snapshot, ...]:
        return tuple(
            snapshot
            for key, snapshot in sorted(self._snapshots.items())
            if key[0] == run_id and key[1] == phase
        )

    def add_baselines(self, baselines: Sequence[Baseline]) -> None:
        self._baselines.extend(baselines)

    def baselines(self, run_id: str = "") -> tuple[Baseline, ...]:
        return tuple(item for item in self._baselines if not run_id or item.run_id == run_id)

    def close(self) -> None:
        return None
