from __future__ import annotations

import pytest

from cuanta.adapters.engines.claude_code import build_command
from cuanta.domain.advisor import advisor_for, family_of, valid_pair
from cuanta.domain.engine import EngineRequest

NEW = "2.1.296 (Claude Code)"


@pytest.mark.parametrize(
    ("mode", "model", "version", "expected"),
    [
        ("auto", "claude-sonnet-5-5", NEW, "opus"),
        ("", "claude-sonnet-5-5", NEW, "opus"),
        ("fable", "claude-sonnet-5-5", NEW, "fable"),
        ("off", "claude-sonnet-5-5", NEW, ""),
        ("auto", "claude-haiku-5-5", NEW, ""),
        ("opus", "claude-haiku-5-5", NEW, "opus"),
        ("opus", "claude-haiku-5-5", "2.1.292", ""),
        ("auto", "claude-opus-5-5", NEW, ""),
        ("opus", "claude-opus-5-5", NEW, ""),
        ("auto", "claude-sonnet-5-5", "2.1.259", ""),
        ("auto", "claude-sonnet-5-5", "", ""),
    ],
)
def test_the_advisor_follows_the_valid_pairs_and_the_version(
    mode: str, model: str, version: str, expected: str
) -> None:
    assert advisor_for(mode, model, version) == expected


def test_families_and_pairs() -> None:
    assert family_of("claude-haiku-4-5") == "haiku"
    assert family_of("gpt-6-sol") == ""
    assert valid_pair("sonnet", "opus", "") and not valid_pair("sonnet", "haiku", NEW)
    assert not valid_pair("haiku", "sonnet", "2.1.280")


def test_the_advisor_reaches_the_claude_command_only_when_chosen() -> None:
    plain = build_command(("claude",), EngineRequest("p", ".", {}, model="claude-sonnet-5-5"))
    assert "--advisor" not in plain
    advised = build_command(
        ("claude",), EngineRequest("p", ".", {}, model="claude-sonnet-5-5", advisor="opus")
    )
    assert advised[advised.index("--advisor") + 1] == "opus"


def test_advisor_consultations_are_counted_apart_with_their_cost() -> None:
    from cuanta.adapters.system.prices import load_prices
    from cuanta.domain.advisor import advisor_message, advisor_spend
    from cuanta.domain.ledger import LedgerEvent
    from cuanta.domain.messages import english
    from cuanta.tui.i18n import Catalog

    events = (
        LedgerEvent(kind="api_request", model="claude-sonnet-5-5", cost_usd=0.5),
        LedgerEvent(kind="api_request", model="claude-opus-5-5", cost_usd=0.25),
        LedgerEvent(kind="api_request", model="claude-opus-5-5", output_tokens=1_000),
        LedgerEvent(kind="tool_result", model="claude-opus-5-5", cost_usd=9.0),
    )
    requests, cost = advisor_spend(events, "opus", load_prices())
    assert (requests, cost) == (2, 0.27)
    shown = advisor_message("opus", requests, cost)
    assert (
        english(shown)
        == "advisor opus: 2 consultations, $0.27 (counted in the run's spend and cap)"
    )
    assert Catalog("es").message(shown).startswith("advisor opus: 2 consultas, $0.27")
