from __future__ import annotations

from cuanta.domain.models import (
    Availability,
    ModelEntry,
    Tier,
    TierSource,
    TierTable,
    entry_from_json,
    entry_to_json,
    find,
    place,
    probe_cost,
    tier_by_price,
)

TABLE = TierTable(
    version=1,
    verified_on="2026-09-23",
    anchors={
        "fable": Tier.FRONTIER,
        "opus": Tier.PREMIUM,
        "sonnet": Tier.STANDARD,
        "haiku": Tier.ECONOMY,
        "gpt-5.6-luna": Tier.ECONOMY,
    },
    aliases={"claude-opus-5-5": "opus", "claude-sonnet-5": "sonnet", "claude-haiku-4-5": "haiku"},
)


def entry(engine: str, model: str, price: float | None = None) -> ModelEntry:
    return ModelEntry(engine, model, model, "p", input_price=price)


def test_place_uses_override_then_anchor_then_family_then_price_then_default() -> None:
    entries = (
        entry("claude", "opus", 4.0),
        entry("claude", "sonnet", 2.0),
        entry("claude", "haiku", 1.0),
        entry("claude", "claude-sonnet-4-5", 3.0),
        entry("opencode", "vendor/cheap", 0.9),
        entry("opencode", "vendor/pricey", 5.0),
        entry("opencode", "vendor/free", 0.0),
        entry("codex", "gpt-5.6-luna", 0.2),
    )
    placed = {item.id: item for item in place(entries, TABLE, {"vendor/pricey": Tier.FRONTIER})}
    assert (placed["opus"].tier, placed["opus"].tier_source) == (Tier.PREMIUM, TierSource.ANCHOR)
    assert placed["claude-sonnet-4-5"].tier is Tier.STANDARD
    assert placed["claude-sonnet-4-5"].tier_source is TierSource.FAMILY
    assert (placed["vendor/cheap"].tier, placed["vendor/cheap"].tier_source) == (
        Tier.ECONOMY,
        TierSource.PRICE,
    )
    assert placed["vendor/pricey"].tier_source is TierSource.OVERRIDE
    assert placed["vendor/pricey"].tier is Tier.FRONTIER
    assert (placed["vendor/free"].tier, placed["vendor/free"].tier_source) == (
        Tier.STANDARD,
        TierSource.DEFAULT,
    )


def test_engine_qualified_override_wins() -> None:
    placed = place(
        (entry("codex", "gpt-5.6-luna", 0.2),), TABLE, {"codex:gpt-5.6-luna": Tier.PREMIUM}
    )
    assert placed[0].tier is Tier.PREMIUM


def test_tier_by_price_picks_the_nearest_anchor_on_a_log_scale() -> None:
    anchors = {Tier.ECONOMY: 1.0, Tier.STANDARD: 2.0, Tier.PREMIUM: 4.0}
    assert tier_by_price(1.2, anchors) is Tier.ECONOMY
    assert tier_by_price(3.9, anchors) is Tier.PREMIUM
    assert tier_by_price(None, anchors) is None
    assert tier_by_price(2.0, {}) is None


def test_json_round_trip_and_lookup() -> None:
    original = ModelEntry(
        "claude",
        "opus",
        "Opus 5.5",
        "anthropic",
        context=1_000_000,
        input_price=4.0,
        output_price=20.0,
        efforts=("low", "max"),
        availability=Availability.PROBED,
        resolved="claude-opus-5-5",
        default=True,
        tier=Tier.PREMIUM,
        tier_source=TierSource.ANCHOR,
    )
    assert entry_from_json(entry_to_json(original)) == original
    assert entry_from_json({"engine": "x"}) is None
    assert find((original,), "claude:opus") == original
    assert find((original,), "claude-opus-5-5") == original
    assert probe_cost(original, 40, 5) == (40 * 4.0 + 5 * 20.0) / 1_000_000
    assert probe_cost(entry("x", "y"), 40, 5) is None
