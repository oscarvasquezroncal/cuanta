from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum


class Tier(StrEnum):
    ECONOMY = "economy"
    STANDARD = "standard"
    PREMIUM = "premium"
    FRONTIER = "frontier"


TIER_ORDER = (Tier.ECONOMY, Tier.STANDARD, Tier.PREMIUM, Tier.FRONTIER)
DEFAULT_TIER = Tier.STANDARD


class Availability(StrEnum):
    DETECTED = "detected"
    CONFIGURED = "configured"
    PROBED = "probed"


class Access(StrEnum):
    SUBSCRIPTION = "subscription"
    API = "api"
    UNKNOWN = "unknown"


class TierSource(StrEnum):
    ANCHOR = "anchor"
    FAMILY = "family"
    PRICE = "price"
    OVERRIDE = "override"
    DEFAULT = "default"


@dataclass(frozen=True, slots=True)
class ModelEntry:
    engine: str
    id: str
    display: str
    provider: str
    context: int = 0
    input_price: float | None = None
    output_price: float | None = None
    efforts: tuple[str, ...] = ()
    availability: Availability = Availability.DETECTED
    access: Access = Access.UNKNOWN
    resolved: str = ""
    default: bool = False
    tier: Tier = DEFAULT_TIER
    tier_source: TierSource = TierSource.DEFAULT

    @property
    def key(self) -> str:
        return f"{self.engine}:{self.id}"


@dataclass(frozen=True, slots=True)
class TierTable:
    version: int
    verified_on: str
    anchors: Mapping[str, Tier]
    aliases: Mapping[str, str] = field(default_factory=dict)

    def anchor(self, model: str) -> Tier | None:
        name = model.lower()
        return self.anchors.get(name) or self.anchors.get(self.aliases.get(name, ""))

    def family(self, model: str) -> Tier | None:
        tokens = set(re.split(r"[^a-z0-9]+", model.lower()))
        for alias in self.aliases.values():
            tier = self.anchors.get(alias)
            if tier is not None and alias in tokens:
                return tier
        return None


def tier_rank(tier: Tier) -> int:
    return TIER_ORDER.index(tier)


def parse_tier(value: str) -> Tier | None:
    try:
        return Tier(value.strip().lower())
    except ValueError:
        return None


def anchor_prices(entries: Iterable[ModelEntry], table: TierTable) -> dict[Tier, float]:
    grouped: dict[Tier, list[float]] = {}
    for entry in entries:
        tier = table.anchor(entry.id)
        if tier is not None and entry.input_price:
            grouped.setdefault(tier, []).append(entry.input_price)
    return {tier: sorted(prices)[len(prices) // 2] for tier, prices in grouped.items()}


def tier_by_price(price: float | None, anchors: Mapping[Tier, float]) -> Tier | None:
    if not price or not anchors:
        return None
    return min(anchors, key=lambda tier: (abs(math.log(price / anchors[tier])), tier_rank(tier)))


def place(
    entries: Sequence[ModelEntry],
    table: TierTable,
    overrides: Mapping[str, Tier],
) -> tuple[ModelEntry, ...]:
    prices = anchor_prices(entries, table)
    placed: list[ModelEntry] = []
    for entry in entries:
        override = overrides.get(entry.key) or overrides.get(entry.id)
        anchored = table.anchor(entry.id)
        family = table.family(entry.id)
        priced = tier_by_price(entry.input_price, prices)
        if override is not None:
            placed.append(replace(entry, tier=override, tier_source=TierSource.OVERRIDE))
        elif anchored is not None:
            placed.append(replace(entry, tier=anchored, tier_source=TierSource.ANCHOR))
        elif family is not None:
            placed.append(replace(entry, tier=family, tier_source=TierSource.FAMILY))
        elif priced is not None:
            placed.append(replace(entry, tier=priced, tier_source=TierSource.PRICE))
        else:
            placed.append(replace(entry, tier=DEFAULT_TIER, tier_source=TierSource.DEFAULT))
    return tuple(placed)


def by_tier(entries: Iterable[ModelEntry], tier: Tier) -> tuple[ModelEntry, ...]:
    return tuple(entry for entry in entries if entry.tier is tier)


def find(entries: Iterable[ModelEntry], reference: str) -> ModelEntry | None:
    wanted = reference.lower()
    for entry in entries:
        if wanted in {entry.key.lower(), entry.id.lower(), entry.resolved.lower()}:
            return entry
    return None


def entry_to_json(entry: ModelEntry) -> dict[str, object]:
    return {
        "engine": entry.engine,
        "id": entry.id,
        "display": entry.display,
        "provider": entry.provider,
        "context": entry.context,
        "input_price": entry.input_price,
        "output_price": entry.output_price,
        "efforts": list(entry.efforts),
        "availability": entry.availability.value,
        "access": entry.access.value,
        "resolved": entry.resolved,
        "default": entry.default,
        "tier": entry.tier.value,
        "tier_source": entry.tier_source.value,
    }


def _price(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def entry_from_json(data: Mapping[str, object]) -> ModelEntry | None:
    engine, model = data.get("engine"), data.get("id")
    if not isinstance(engine, str) or not isinstance(model, str):
        return None
    context = data.get("context")
    efforts = data.get("efforts")
    try:
        availability = Availability(str(data.get("availability", Availability.DETECTED.value)))
        access = Access(str(data.get("access", Access.UNKNOWN.value)))
        tier = Tier(str(data.get("tier", DEFAULT_TIER.value)))
        source = TierSource(str(data.get("tier_source", TierSource.DEFAULT.value)))
    except ValueError:
        return None
    return ModelEntry(
        engine=engine,
        id=model,
        display=str(data.get("display") or model),
        provider=str(data.get("provider") or ""),
        context=context if isinstance(context, int) else 0,
        input_price=_price(data.get("input_price")),
        output_price=_price(data.get("output_price")),
        efforts=tuple(str(item) for item in efforts) if isinstance(efforts, list) else (),
        availability=availability,
        access=access,
        resolved=str(data.get("resolved") or model),
        default=data.get("default") is True,
        tier=tier,
        tier_source=source,
    )


def probe_cost(entry: ModelEntry, input_tokens: int, output_tokens: int) -> float | None:
    if entry.input_price is None or entry.output_price is None:
        return None
    return (input_tokens * entry.input_price + output_tokens * entry.output_price) / 1_000_000
