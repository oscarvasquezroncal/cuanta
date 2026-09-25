from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from cuanta.domain.errors import DomainFailure
from cuanta.domain.ledger import Run
from cuanta.domain.models import (
    TIER_ORDER,
    Availability,
    ModelEntry,
    Tier,
    TierTable,
    entry_from_json,
    entry_to_json,
    find,
    parse_tier,
    place,
)
from cuanta.ports.models import ModelCatalog
from cuanta.ports.workspace import Workspace

CACHE_PATH = ".cuanta/models.json"
PROBE_INPUT_TOKENS = 40
PROBE_OUTPUT_TOKENS = 5
PROBE_PROMPT = "Reply with the single word OK."
PROBE_BUDGET_USD = 0.05


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    entry: ModelEntry
    estimate: float | None
    ok: bool | None
    cost_usd: float | None


@dataclass(frozen=True, slots=True)
class CatalogView:
    entries: tuple[ModelEntry, ...]
    refreshed_at: str
    table_version: int
    verified_on: str
    engines: tuple[str, ...]
    cached: bool


@dataclass(frozen=True, slots=True)
class ModelStats:
    engine: str
    model: str
    tier: Tier | None
    runs: int
    ok: int
    cost_usd: float
    priced_runs: int

    @property
    def success(self) -> float:
        return self.ok / self.runs if self.runs else 0.0

    @property
    def average_cost(self) -> float | None:
        return self.cost_usd / self.priced_runs if self.priced_runs else None


class ModelService:
    def __init__(
        self,
        catalogs: Sequence[ModelCatalog],
        table: TierTable,
        overrides: Callable[[], Mapping[str, Tier]],
        save_override: Callable[[str, Tier], None],
        workspace: Workspace,
        now_iso: Callable[[], str],
    ) -> None:
        self._catalogs = catalogs
        self._table = table
        self._overrides = overrides
        self._save_override = save_override
        self._workspace = workspace
        self._now = now_iso

    def refresh(self) -> CatalogView:
        engines: list[str] = []
        entries: list[ModelEntry] = []
        for catalog in self._catalogs:
            if not catalog.available():
                continue
            engines.append(catalog.engine)
            entries.extend(catalog.list())
        placed = place(entries, self._table, self._overrides())
        refreshed = self._now()
        document = {
            "refreshed_at": refreshed,
            "table_version": self._table.version,
            "verified_on": self._table.verified_on,
            "engines": engines,
            "models": [entry_to_json(entry) for entry in placed],
        }
        self._workspace.write_text(CACHE_PATH, json.dumps(document, indent=2) + "\n")
        return CatalogView(
            placed, refreshed, self._table.version, self._table.verified_on, tuple(engines), False
        )

    def cached(self) -> CatalogView | None:
        text = self._workspace.read_text(CACHE_PATH)
        if text is None:
            return None
        try:
            data = json.loads(text)
        except ValueError:
            return None
        if not isinstance(data, dict) or not isinstance(data.get("models"), list):
            return None
        entries = tuple(
            entry
            for entry in (
                entry_from_json(item) for item in data["models"] if isinstance(item, dict)
            )
            if entry is not None
        )
        engines = data.get("engines")
        version = data.get("table_version")
        return CatalogView(
            place(entries, self._table, self._overrides()),
            str(data.get("refreshed_at", "")),
            version if isinstance(version, int) else 0,
            str(data.get("verified_on", "")),
            tuple(str(name) for name in engines) if isinstance(engines, list) else (),
            True,
        )

    def view(self, refresh: bool = False) -> CatalogView:
        if not refresh:
            cached = self.cached()
            if cached is not None and cached.table_version == self._table.version:
                return cached
        return self.refresh()

    def set_tier(self, reference: str, tier_name: str) -> ModelEntry:
        tier = parse_tier(tier_name)
        if tier is None:
            choices = ", ".join(item.value for item in TIER_ORDER)
            raise DomainFailure(f"unknown tier {tier_name}", f"use one of {choices}")
        entry = find(self.view().entries, reference)
        if entry is None:
            raise DomainFailure(
                f"unknown model {reference}", "run cuanta models list to see the catalog"
            )
        self._save_override(entry.key, tier)
        return replace(entry, tier=tier)

    def mark_probed(self, entry: ModelEntry) -> None:
        current = self.view()
        updated = tuple(
            replace(item, availability=Availability.PROBED) if item.key == entry.key else item
            for item in current.entries
        )
        document = {
            "refreshed_at": current.refreshed_at,
            "table_version": current.table_version,
            "verified_on": current.verified_on,
            "engines": list(current.engines),
            "models": [entry_to_json(item) for item in updated],
        }
        self._workspace.write_text(CACHE_PATH, json.dumps(document, indent=2) + "\n")


def model_stats(runs: Sequence[Run], entries: Sequence[ModelEntry]) -> tuple[ModelStats, ...]:
    grouped: dict[tuple[str, str], list[Run]] = {}
    for run in runs:
        if run.engine and run.model and run.status != "running":
            grouped.setdefault((run.engine, run.model), []).append(run)
    rows: list[ModelStats] = []
    for (engine, model), items in sorted(grouped.items()):
        entry = find((item for item in entries if item.engine == engine), model)
        priced = [run.cost_usd for run in items if run.cost_usd is not None]
        rows.append(
            ModelStats(
                engine=engine,
                model=model,
                tier=entry.tier if entry else None,
                runs=len(items),
                ok=sum(1 for run in items if run.status == "ok"),
                cost_usd=sum(priced),
                priced_runs=len(priced),
            )
        )
    return tuple(rows)
