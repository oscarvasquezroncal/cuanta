from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256

from cuanta.domain.code_index import IndexRow, index_path
from cuanta.domain.index_facts import history_prior
from cuanta.domain.real_costs import Attempt

HEURISTIC = (
    "Known attempt cost is allocated once across observed read/edit/cite files using "
    "normalized existing index-history priors. This is a retrospective heuristic, not "
    "measured or causal file cost. Unknown pricing and unallocated known cost remain separate."
)
_ACTIONS = frozenset({"read", "edit", "cite", "outcome", "failure"})
_OBSERVED = frozenset({"read", "edit", "cite"})
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]+")


@dataclass(frozen=True, slots=True)
class FileCostRow:
    path: str
    task_type: str
    mix: str
    prior: float
    known_allocated_usd: float
    samples: int
    known_samples: int
    unknown_samples: int
    unallocated_samples: int
    estimated_samples: int
    stale_samples: int

    @property
    def allocated_cost_usd(self) -> float | None:
        if not self.known_samples or self.unknown_samples or self.unallocated_samples:
            return None
        return self.known_allocated_usd

    @property
    def estimated(self) -> bool:
        return self.estimated_samples > 0


@dataclass(frozen=True, slots=True)
class FileCostReport:
    rows: tuple[FileCostRow, ...] = ()
    known_cost_usd: float = 0.0
    allocated_known_usd: float = 0.0
    unallocated_known_usd: float = 0.0
    attempts: int = 0
    known_attempts: int = 0
    allocated_attempts: int = 0
    unallocated_attempts: int = 0
    unknown_attempts: int = 0
    invalid_rows: int = 0
    heuristic: str = HEURISTIC


@dataclass(frozen=True, slots=True)
class _History:
    row: IndexRow
    run_id: str


@dataclass(frozen=True, slots=True)
class _Sample:
    rows: tuple[IndexRow, ...]
    allocation: float | None
    known: bool
    allocated: bool
    estimated: bool


def _timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _history(row: IndexRow) -> _History | None:
    try:
        value = json.loads(row.text)
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    path, run_id, task, action, at = (
        value.get(key) for key in ("path", "run_id", "task_type", "action", "at")
    )
    if (
        not isinstance(path, str)
        or not isinstance(run_id, str)
        or not _IDENTIFIER.fullmatch(run_id)
        or not isinstance(task, str)
        or not isinstance(action, str)
        or action not in _ACTIONS
        or not _timestamp(at)
        or not row.source_hash
        or row.provenance != "ledger:" + run_id
        or row.target != task
        or row.relation != action
    ):
        return None
    try:
        normalized = index_path(path)
        if normalized != row.path or index_path(row.path) != row.path:
            return None
    except ValueError:
        return None
    outcome, retries, signature = (
        value.get("outcome", ""),
        value.get("retries", 0),
        value.get("signature", ""),
    )
    if (
        not isinstance(outcome, str)
        or outcome not in {"", "accepted", "rejected"}
        or not isinstance(retries, int)
        or isinstance(retries, bool)
        or retries < 0
        or not isinstance(signature, str)
    ):
        return None
    identity = sha256(f"{run_id}\0{normalized}\0{action}\0{signature}".encode()).hexdigest()
    if row.id != "history:" + identity:
        return None
    return _History(row, run_id)


def _root_row(row: IndexRow, root_id: str) -> IndexRow:
    if row.provenance == "ledger:" + root_id:
        return row
    value = json.loads(row.text)
    value["run_id"] = root_id
    return replace(row, provenance="ledger:" + root_id, text=json.dumps(value, sort_keys=True))


def _cost(item: Attempt) -> float | None:
    value = item.cost
    return value if value is not None and math.isfinite(value) and value >= 0 else None


def file_cost_report(
    rows: Sequence[IndexRow], items: Sequence[Attempt], now: str
) -> FileCostReport:
    attempts = {item.run.id: item for item in items}
    owners: dict[str, str] = {}
    ambiguous: set[str] = set()
    for root_id, item in attempts.items():
        for run_id in {root_id, *item.run_ids}:
            if run_id in owners and owners[run_id] != root_id:
                ambiguous.add(run_id)
            owners[run_id] = root_id
    grouped: dict[str, dict[str, list[IndexRow]]] = defaultdict(lambda: defaultdict(list))
    invalid = 0
    for row in rows:
        record = _history(row)
        if record is None:
            invalid += 1
            continue
        owner_id = owners.get(record.run_id)
        if owner_id is None or record.run_id in ambiguous:
            continue
        item = attempts[owner_id]
        if record.row.target != item.run.task_type:
            invalid += 1
            continue
        grouped[owner_id][record.row.path].append(_root_row(record.row, owner_id))
    samples: dict[tuple[str, str, str], list[_Sample]] = defaultdict(list)
    known_cost = allocated_cost = 0.0
    known_count = allocated_count = unknown_count = 0
    for root_id, item in sorted(attempts.items()):
        cost = _cost(item)
        if cost is None:
            unknown_count += 1
        else:
            known_cost += cost
            known_count += 1
        observed = {
            path: tuple(records)
            for path, records in sorted(grouped[root_id].items())
            if any(row.relation in _OBSERVED for row in records)
        }
        weights = {
            path: history_prior(records, item.run.task_type, now)
            for path, records in observed.items()
        }
        total_weight = sum(weights.values())
        allocated = cost is not None and total_weight > 0 and math.isfinite(total_weight)
        shares: dict[str, float] = {}
        if allocated and cost is not None:
            remaining = cost
            for index, path in enumerate(observed):
                share = (
                    remaining
                    if index == len(observed) - 1
                    else min(remaining, cost * weights[path] / total_weight)
                )
                shares[path] = share
                remaining -= share
            allocated_cost += cost
            allocated_count += 1
        for path, records in observed.items():
            samples[(path, item.type, item.mix)].append(
                _Sample(records, shares.get(path), cost is not None, allocated, item.estimated)
            )
    result: list[FileCostRow] = []
    for (path, task, mix), group in sorted(samples.items()):
        evidence = tuple(row for sample in group for row in sample.rows)
        result.append(
            FileCostRow(
                path=path,
                task_type=task,
                mix=mix,
                prior=history_prior(evidence, "", now),
                known_allocated_usd=sum(
                    sample.allocation for sample in group if sample.allocation is not None
                ),
                samples=len(group),
                known_samples=sum(sample.known for sample in group),
                unknown_samples=sum(not sample.known for sample in group),
                unallocated_samples=sum(sample.known and not sample.allocated for sample in group),
                estimated_samples=sum(sample.known and sample.estimated for sample in group),
                stale_samples=sum(any(row.stale for row in sample.rows) for sample in group),
            )
        )
    return FileCostReport(
        rows=tuple(result),
        known_cost_usd=known_cost,
        allocated_known_usd=allocated_cost,
        unallocated_known_usd=known_cost - allocated_cost,
        attempts=len(attempts),
        known_attempts=known_count,
        allocated_attempts=allocated_count,
        unallocated_attempts=known_count - allocated_count,
        unknown_attempts=unknown_count,
        invalid_rows=invalid,
    )
