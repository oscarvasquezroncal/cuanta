from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from cuanta.domain.index_metrics import IndexMetrics, index_metrics
from cuanta.domain.ledger import LedgerEvent, Run


def _count(meta: Mapping[str, object], key: str) -> int | None:
    value = meta.get("index_learning")
    if not isinstance(value, Mapping):
        return None
    count = value.get(key)
    return count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None


def _paths(meta: Mapping[str, object], key: str) -> tuple[str, ...] | None:
    value = meta.get(key)
    if not isinstance(value, list | tuple):
        return None
    return tuple(item for item in value if isinstance(item, str))


def run_index_metrics(
    events: Sequence[LedgerEvent],
    runs: Sequence[Run],
    metadata: Callable[[str], Mapping[str, object] | None] | None = None,
) -> IndexMetrics:
    unique = {run.id: run for run in runs}
    metas = {run_id: metadata(run_id) or {} for run_id in unique} if metadata else {}
    saved = 0
    guards: list[str] = []
    outside: list[str] = []
    for run in unique.values():
        if run.parent_id in unique:
            continue
        meta = metas.get(run.id, {})
        children = [
            metas.get(child.id, {}) for child in unique.values() if child.parent_id == run.id
        ]
        count = _count(meta, "findings_saved")
        saved += (
            count
            if count is not None
            else sum(_count(item, "findings_saved") or 0 for item in children)
        )
        for key, target in (("guard_violations", guards), ("out_of_plan_edits", outside)):
            paths = _paths(meta, key)
            target.extend(
                paths
                if paths is not None
                else (path for child in children for path in _paths(child, key) or ())
            )
    stale = max((_count(meta, "stale_facts") or 0 for meta in metas.values()), default=0)
    return index_metrics(
        events,
        stale_facts=stale,
        findings_saved=saved,
        guard_violations=guards,
        out_of_plan_edits=outside,
    )
