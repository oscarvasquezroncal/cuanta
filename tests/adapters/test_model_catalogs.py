from __future__ import annotations

import json
from pathlib import Path

from cuanta.adapters.models.claude_catalog import ClaudeCatalog
from cuanta.adapters.models.codex_catalog import CodexCatalog
from cuanta.adapters.models.opencode_catalog import OpenCodeCatalog, parse_verbose
from cuanta.adapters.models.tiers import claude_facts, load_tier_table
from cuanta.adapters.system.prices import load_prices
from cuanta.domain.models import Access, Availability, Tier, entry_to_json, probe_cost
from cuanta.domain.pricing import Price, dollars
from cuanta.ports.system import Completed
from tests.fakes import FakeRunner

CODEX_MODELS = {
    "models": [
        {
            "slug": "gpt-6-astra",
            "display_name": "GPT-6-Astra",
            "context_window": 272000,
            "visibility": "list",
            "supported_reasoning_levels": [{"effort": "low"}, {"effort": "max"}],
        },
        {"slug": "gpt-6-sol", "display_name": "GPT-6-Sol", "visibility": "list"},
        {"slug": "gpt-6-luna", "display_name": "GPT-6-Luna", "visibility": "list"},
        {"slug": "gpt-5.6-sol", "display_name": "GPT-5.6-Sol", "visibility": "list"},
        {"slug": "gpt-5.6-terra", "display_name": "GPT-5.6-Terra", "visibility": "list"},
        {"slug": "gpt-5.6-luna", "display_name": "GPT-5.6-Luna", "visibility": "list"},
        {"slug": "gpt-5.5", "display_name": "GPT-5.5", "visibility": "list"},
        {"slug": "codex-auto-review", "display_name": "Codex Auto Review", "visibility": "hide"},
    ]
}

OPENCODE_VERBOSE = """opencode/big-pickle
{
  "id": "big-pickle",
  "providerID": "opencode",
  "name": "Big Pickle",
  "status": "active",
  "cost": {"input": 0, "output": 0},
  "limit": {"context": 200000, "output": 32000},
  "variants": {}
}
openrouter/some/model
{
  "id": "some/model",
  "providerID": "openrouter",
  "name": "Some Model",
  "status": "active",
  "cost": {"input": 3.5, "output": 14},
  "limit": {"context": 128000},
  "variants": {"low": {}, "high": {}}
}
old/gone
{
  "id": "gone",
  "providerID": "old",
  "status": "deprecated",
  "cost": {"input": 1, "output": 1}
}
"""


def test_every_anchor_and_alias_in_the_tiers_file_is_known() -> None:
    table = load_tier_table()
    assert table.version >= 1
    assert table.anchor("opus") is Tier.PREMIUM
    assert table.anchor("claude-opus-5-5") is Tier.PREMIUM
    assert table.anchor("gpt-5.6-luna") is Tier.ECONOMY
    prices = load_prices()
    for fact in claude_facts():
        assert prices.lookup(fact.resolves) is not None, fact.resolves
    for model in ("gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
        assert prices.lookup(model) is not None, model


def test_codex_catalog_reads_the_listing_and_the_config(tmp_path: Path) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        'model = "gpt-5.6-sol"\n[profiles.fast]\nmodel = "gpt-9-private"\n', encoding="utf-8"
    )
    runner = FakeRunner(
        binaries={"codex": "/bin/codex"},
        responses={"codex debug models": Completed(0, json.dumps(CODEX_MODELS), "")},
    )
    catalog = CodexCatalog(runner, tmp_path, {"OPENAI_API_KEY": "set"}, load_prices())
    entries = {entry.id: entry for entry in catalog.list()}
    assert set(entries) == {
        "gpt-6-astra",
        "gpt-6-sol",
        "gpt-6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-9-private",
    }
    assert entries["gpt-6-astra"].efforts == ("low", "max")
    assert entries["gpt-6-astra"].context == 272000
    assert entries["gpt-5.6-sol"].default
    assert entries["gpt-5.6-sol"].availability is Availability.CONFIGURED
    assert entries["gpt-9-private"].availability is Availability.CONFIGURED
    assert entries["gpt-9-private"].input_price is None
    assert entries["gpt-9-private"].output_price is None
    assert dollars(probe_cost(entries["gpt-9-private"], 1_000, 1_000)) == "cost n/a"
    assert entries["gpt-6-astra"].access is Access.API


def test_codex_catalog_routes_models_with_verified_price_rows(tmp_path: Path) -> None:
    expected = {
        "gpt-6-astra": Price(input=10.0, output=50.0, cache_write=12.5, cache_read=1.0),
        "gpt-6-sol": Price(input=2.0, output=10.0, cache_write=2.5, cache_read=0.2),
        "gpt-6-luna": Price(input=0.1, output=0.5, cache_write=0.125, cache_read=0.01),
        "gpt-5.6-sol": Price(input=4.0, output=20.0, cache_write=5.0, cache_read=0.4),
        "gpt-5.6-terra": Price(input=2.0, output=12.0, cache_write=2.5, cache_read=0.2),
        "gpt-5.6-luna": Price(input=0.2, output=1.2, cache_write=0.25, cache_read=0.02),
        "gpt-5.5": Price(input=5.0, output=30.0, cache_write=None, cache_read=0.5),
    }
    runner = FakeRunner(
        binaries={"codex": "/bin/codex"},
        responses={"codex debug models": Completed(0, json.dumps(CODEX_MODELS), "")},
    )
    prices = load_prices()
    entries = CodexCatalog(runner, tmp_path, {}, prices).list()
    assert {entry.id for entry in entries} == set(expected)
    for entry in entries:
        price = expected[entry.id]
        assert prices.lookup(entry.resolved) == price
        assert (entry.input_price, entry.output_price) == (price.input, price.output)
        assert probe_cost(entry, 1_000_000, 1_000_000) == price.input + price.output


def test_codex_catalog_keeps_unknown_listed_model_unpriced(tmp_path: Path) -> None:
    runner = FakeRunner(
        binaries={"codex": "/bin/codex"},
        responses={
            "codex debug models": Completed(
                0, json.dumps({"models": [{"slug": "gpt-6-sol-private"}]}), ""
            )
        },
    )
    prices = load_prices()
    (entry,) = CodexCatalog(runner, tmp_path, {}, prices).list()
    assert prices.lookup(entry.resolved) is None
    assert entry.input_price is None and entry.output_price is None
    assert dollars(probe_cost(entry, 1_000_000, 1_000_000)) == "cost n/a"
    assert entry_to_json(entry)["input_price"] is None
    assert entry_to_json(entry)["output_price"] is None


def test_opencode_catalog_parses_verbose_blocks(tmp_path: Path) -> None:
    assert [name for name, _ in parse_verbose(OPENCODE_VERBOSE)] == [
        "opencode/big-pickle",
        "openrouter/some/model",
        "old/gone",
    ]
    (tmp_path / "opencode.json").write_text('{"model": "openrouter/some/model"}', "utf-8")
    runner = FakeRunner(
        binaries={"opencode": "/bin/opencode"},
        responses={"opencode models --verbose": Completed(0, OPENCODE_VERBOSE, "")},
    )
    entries = {entry.id: entry for entry in OpenCodeCatalog(runner, tmp_path, tmp_path).list()}
    assert set(entries) == {"opencode/big-pickle", "openrouter/some/model"}
    chosen = entries["openrouter/some/model"]
    assert (chosen.input_price, chosen.output_price, chosen.context) == (3.5, 14.0, 128000)
    assert chosen.efforts == ("low", "high")
    assert chosen.default and chosen.availability is Availability.CONFIGURED


def test_claude_catalog_respects_the_allowlist_default_and_env(tmp_path: Path) -> None:
    home, project = tmp_path / "home", tmp_path / "repo"
    (home / ".claude").mkdir(parents=True)
    (project / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"availableModels": ["sonnet", "opus", "claude-opus-4-6"], "model": "opus"}),
        encoding="utf-8",
    )
    (project / ".claude" / "settings.local.json").write_text(
        json.dumps({"model": "sonnet"}), encoding="utf-8"
    )
    environ = {"ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-5"}
    catalog = ClaudeCatalog(home, project, environ, load_prices(), installed=True, managed=())
    entries = {entry.id: entry for entry in catalog.list()}
    assert set(entries) == {"sonnet", "opus", "claude-opus-4-6"}
    assert entries["sonnet"].default
    assert not entries["opus"].default
    assert entries["opus"].resolved == "claude-opus-5"
    assert entries["sonnet"].context == 1_000_000
    assert entries["opus"].availability is Availability.CONFIGURED


def test_claude_catalog_without_settings_lists_every_alias(tmp_path: Path) -> None:
    catalog = ClaudeCatalog(tmp_path, tmp_path, {}, load_prices(), installed=True, managed=())
    entries = {entry.id: entry for entry in catalog.list()}
    assert {"fable", "opus", "sonnet", "haiku"} <= set(entries)
    assert entries["opus"].default
    assert entries["opus"].resolved == "claude-opus-5-5"
    assert entries["haiku"].efforts == ()
