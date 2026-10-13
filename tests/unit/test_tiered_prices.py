from __future__ import annotations

import pytest

from cuanta.adapters.models.tiers import claude_facts, supported_fact
from cuanta.adapters.system.prices import load_prices
from cuanta.domain.engine import ModelUsage, StepUsage
from cuanta.domain.governor import step_usd
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.pricing import event_cost
from cuanta.domain.routing import Provider


def test_the_october_prices_are_in_the_table() -> None:
    table = load_prices()
    sonnet = table.lookup("claude-sonnet-5-5")
    haiku = table.lookup("claude-haiku-5-5")
    opus = table.lookup("claude-opus-5-5")
    assert sonnet is not None and (sonnet.input, sonnet.output, sonnet.cache_read) == (
        2.0,
        10.0,
        0.1,
    )
    assert haiku is not None and (haiku.input, haiku.output, haiku.cache_read) == (0.1, 0.5, 0.01)
    assert haiku.long is not None and haiku.long_above == 100_000
    assert (haiku.long.input, haiku.long.output, haiku.long.cache_read) == (0.5, 2.5, 0.05)
    assert opus is not None and opus.fast is not None
    assert (opus.fast.input, opus.fast.output) == (8.0, 40.0)
    old = table.lookup("claude-haiku-4-5")
    assert old is not None and (old.input, old.output, old.cache_read) == (1.0, 5.0, 0.1)


def test_each_request_is_priced_by_the_tier_of_its_own_prompt() -> None:
    haiku = load_prices().lookup("claude-haiku-5-5")
    assert haiku is not None
    small = LedgerEvent(
        model="claude-haiku-5-5",
        input_tokens=1_000,
        cache_read_tokens=90_000,
        output_tokens=1_000,
    )
    large = LedgerEvent(
        model="claude-haiku-5-5",
        input_tokens=1_000,
        cache_read_tokens=110_000,
        output_tokens=1_000,
    )
    assert event_cost(small, haiku) == pytest.approx(
        (1_000 * 0.1 + 90_000 * 0.01 + 1_000 * 0.5) / 1e6
    )
    assert event_cost(large, haiku) == pytest.approx(
        (1_000 * 0.5 + 110_000 * 0.05 + 1_000 * 2.5) / 1e6
    )
    step = StepUsage(ModelUsage("claude-haiku-5-5", input_tokens=1_000, cache_read_tokens=110_000))
    assert step_usd(step, haiku, Provider.CLAUDE) == pytest.approx(
        (1_000 * 0.5 + 110_000 * 0.05) / 1e6
    )


@pytest.mark.parametrize(
    ("alias", "version", "resolved"),
    [
        ("sonnet", "2.1.296 (Claude Code)", "claude-sonnet-5-5"),
        ("sonnet", "2.1.280", "claude-sonnet-5"),
        ("haiku", "2.1.293", "claude-haiku-5-5"),
        ("haiku", "2.1.292", "claude-haiku-4-5"),
        ("haiku", "", "claude-haiku-5-5"),
        ("opus", "2.1.200", "claude-opus-5-5"),
    ],
)
def test_claude_code_without_the_new_models_falls_back_to_the_previous_ones(
    alias: str, version: str, resolved: str
) -> None:
    fact = next(item for item in claude_facts() if item.alias == alias)
    assert supported_fact(fact, version).resolves == resolved


def test_the_tiers_name_opus_sonnet_and_haiku_five_five() -> None:
    facts = {fact.alias: fact for fact in claude_facts()}
    assert facts["sonnet"].display == "Sonnet 5.5"
    assert facts["haiku"].display == "Haiku 5.5" and facts["haiku"].context == 1_000_000
    assert "medium" in facts["haiku"].efforts
    assert supported_fact(facts["haiku"], "2.1.280").display == "Haiku 4.5"
