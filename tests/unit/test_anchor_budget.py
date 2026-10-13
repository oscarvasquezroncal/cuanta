from __future__ import annotations

from cuanta.domain.pack import ANCHOR_TOKENS, Layer, PackItem, compile_pack


def item(key: str, tokens: int, priority: int = 0, layer: Layer = Layer.L2) -> PackItem:
    return PackItem(
        key, layer, "x" * (tokens * 4), 1.0, "test", "src/a.py", 1, 9, priority=priority
    )


def test_cited_ranges_have_their_own_budget_before_the_rest() -> None:
    cited = tuple(item(f"anchor:src/a.py:{index}", 3_000, priority=1) for index in range(3))
    others = (item("excerpt:src/b.py", 1_500), item("excerpt:src/c.py", 1_500))
    pack = compile_pack((*cited, *others), 2_000, anchor_budget=ANCHOR_TOKENS)
    chosen = {entry.key for entry in pack.items}
    assert {entry.key for entry in cited} <= chosen
    assert len(chosen & {entry.key for entry in others}) == 1
    assert (pack.budget, pack.anchor_budget) == (2_000, ANCHOR_TOKENS)
    reasons = {decision.key: decision.reason for decision in pack.decisions}
    assert reasons["anchor:src/a.py:0"].startswith("cited range within its own budget")


def test_cited_ranges_beyond_their_budget_still_compete_for_the_rest() -> None:
    cited = tuple(item(f"anchor:src/a.py:{index}", 5_000, priority=1) for index in range(4))
    pack = compile_pack(cited, 6_000, anchor_budget=ANCHOR_TOKENS)
    assert len(pack.items) == 3
    without = compile_pack(cited, 6_000)
    assert len(without.items) == 1 and without.anchor_budget == 0
    assert ANCHOR_TOKENS == 12_000
