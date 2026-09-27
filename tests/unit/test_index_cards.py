from __future__ import annotations

import json
from dataclasses import replace

import pytest

from cuanta.domain.code_index import HandlingCard, IndexedFile, IndexRow
from cuanta.domain.index_cards import handling_card
from cuanta.domain.spectrum import estimated_tokens

FILE = IndexedFile("src/auth.ts", "current", "typescript", 1000)


def _row(
    identity: str,
    text: str = "",
    relation: str = "function",
    path: str = FILE.path,
    target: str = "",
    provenance: str = "ast",
    line: int = 1,
) -> IndexRow:
    return IndexRow(identity, path, "current", provenance, text, line, line, target, relation)


def _card(
    file: IndexedFile = FILE,
    symbols: tuple[IndexRow, ...] = (),
    incoming: tuple[IndexRow, ...] = (),
    outgoing: tuple[IndexRow, ...] = (),
    notes: tuple[IndexRow, ...] = (),
    rules: tuple[IndexRow, ...] = (),
    tests: tuple[IndexRow, ...] = (),
    history: tuple[IndexRow, ...] = (),
    limit: int = 120,
) -> HandlingCard:
    return handling_card(file, symbols, incoming, outgoing, notes, rules, tests, history, limit)


def test_card_is_deterministic_and_uses_utf8_byte_token_estimates() -> None:
    symbols = (_row("z", "authorize"), _row("a", "café"))
    notes = (
        _row(
            "n2",
            "Keeps cafés authenticated.",
            "note",
            target="anchor:2",
            provenance="agent-note:current",
        ),
        _row("n1", "Preserves sesión.", "note", target="anchor:1", provenance="agent-note:current"),
    )
    incoming = tuple(_row(f"i{n}", path=f"src/page{n}.ts", target=FILE.path) for n in range(3))
    first = _card(symbols=symbols, notes=notes, incoming=incoming)
    second = _card(symbols=symbols[::-1], notes=notes[::-1], incoming=incoming[::-1])
    assert first == second
    assert first.text.encode() == second.text.encode()
    assert first.estimated_tokens == estimated_tokens(len(first.text.encode("utf-8")))
    assert 0 < first.estimated_tokens <= 120
    assert len(first.text.encode("utf-8")) <= 480


@pytest.mark.parametrize(
    ("path", "role"),
    [
        ("app/cart/page.tsx", "route"),
        ("src/routes/cart.ts", "route"),
        ("src/components/Cart.tsx", "component"),
        ("src/hooks/useCart.ts", "hook"),
        ("src/useCart.ts", "hook"),
        ("src/stores/cart.ts", "store"),
        ("src/cartStore.ts", "store"),
        ("src/services/cart.py", "service"),
        ("tests/test_cart.py", "test"),
        ("src/cart.test.ts", "test"),
        ("src/globals.css", "styles"),
        ("package.json", "config"),
        ("src/config.py", "config"),
        ("src/cart.py", "code"),
    ],
)
def test_role_and_path_remain_usable(path: str, role: str) -> None:
    card = _card(file=replace(FILE, path=path))
    assert card.role == role
    assert card.path == path
    assert card.text.startswith(path + " | " + role + "\n")
    assert card.purpose


def test_route_symbol_overrides_generic_path_role() -> None:
    card = _card(symbols=(_row("route", "/cart", "route"),))
    assert card.role == "route"


def test_purpose_prefers_note_then_finding_then_economy_summary_then_symbols() -> None:
    note = _row(
        "note", "Owns authorization.", "note", target="anchor:n", provenance="agent-note:current"
    )
    finding = _row(
        "finding", "Guards sessions.", "finding", target="anchor:f", provenance="report.md#hash"
    )
    summary = _row(
        "summary",
        "Summarizes sign-in.",
        "summary",
        target="anchor:s",
        provenance="economy-summary:model",
    )
    symbol = _row("symbol", "authorize")
    assert _card(notes=(summary, finding, note)).purpose == note.text
    assert _card(notes=(summary, finding)).purpose == finding.text
    summary_card = _card(notes=(summary,))
    assert summary_card.purpose == summary.text
    assert "Fact " not in summary_card.text
    assert _card(symbols=(symbol,)).purpose == "Code for authorize."


def test_unanchored_notes_and_stale_summaries_do_not_replace_current_symbols() -> None:
    unanchored = _row("unanchored", "UNVERIFIED_NOTE", "note", provenance="agent-note:current")
    summary = replace(
        _row(
            "summary",
            "Generated STALE_SUMMARY",
            "summary",
            target="anchor:summary",
            provenance="economy-summary:model",
        ),
        stale=True,
    )
    card = _card(symbols=(_row("symbol", "authorize"),), notes=(unanchored, summary))
    assert card.purpose == "Code for authorize."
    assert "UNVERIFIED_NOTE" not in card.text and "STALE_SUMMARY" not in card.text
    assert "Flags " not in card.text


def test_finding_keeps_anchored_range_and_report_source_when_clipped() -> None:
    finding = replace(
        _row(
            "finding",
            "Authorization must preserve session checks. " * 30,
            "finding",
            target="anchor:source",
            provenance="run/report.md#digest",
            line=4,
        ),
        end_line=9,
    )
    card = _card(notes=(finding,), limit=80)
    assert "Fact src/auth.ts:4-9 [run/report.md]: Authorization" in card.text
    assert "…" in card.text
    assert len(card.text.encode()) <= 320


def test_card_excludes_stale_mismatched_and_other_file_notes_and_symbols() -> None:
    current = _row("fresh", "Current purpose.", "finding", target="anchor:current")
    invalid = (
        replace(current, id="stale", text="STALE_FACT", stale=True),
        replace(current, id="mismatch", text="WRONG_HASH", source_hash="old"),
        replace(current, id="other", text="OTHER_FILE", path="src/other.ts"),
    )
    card = _card(
        notes=(*invalid, current),
        symbols=(_row("other-symbol", "OTHER_SYMBOL", path="src/other.ts"),),
    )
    assert card.purpose == current.text
    assert "Current purpose." in card.text
    assert all(
        value not in card.text
        for value in ("STALE_FACT", "WRONG_HASH", "OTHER_FILE", "OTHER_SYMBOL")
    )


def test_rule_scopes_keep_actual_section_sources_and_meaning_when_clipped() -> None:
    rule = _row(
        "rule",
        "## Typing\nPreserve strict typing. " + "Keep types safe. " * 100,
        "rule",
        path="CLAUDE.md",
        target="**",
        provenance="CLAUDE.md § Architecture > Typing",
    )
    local = replace(
        rule,
        id="local",
        path="src/CLAUDE.md",
        target="src/**",
        provenance="src/CLAUDE.md § Local",
        text="Keep catalogs bilingual.",
    )
    invalid = (
        replace(rule, id="other", target="tests/**", text="UNSCOPED_RULE"),
        replace(rule, id="sibling", target="src/authorize/**", text="SIBLING_RULE"),
        replace(rule, id="stale", stale=True, text="STALE_RULE"),
    )
    card = _card(rules=(local, rule, *invalid))
    assert "CLAUDE.md § Architecture > Typing: Preserve strict typing." in card.text
    assert "src/CLAUDE.md § Local: Keep catalogs bilingual." in card.text
    assert all(value not in card.text for value in ("UNSCOPED_RULE", "SIBLING_RULE", "STALE_RULE"))
    assert len(card.text.encode()) <= 480
    assert "…" in card.text


def test_risk_counts_unique_fan_in_exports_and_degree_and_marks_sensitive_files() -> None:
    incoming = (
        _row("i1", path="src/page.ts", target=FILE.path, relation="imports"),
        _row("i2", path="src/page.ts", target=FILE.path, relation="imports"),
        _row("i3", path="src/menu.ts", target=FILE.path, relation="imports"),
        _row("wrong", path="src/ignored.ts", target="src/other.ts"),
    )
    outgoing = (
        _row("o1", "authorize", "exports", target="src/auth.ts:1:authorize"),
        _row("o2", "authorize", "exports", target="symbol:authorize"),
        _row("o3", "database", "imports", target="src/db.ts"),
    )
    card = _card(incoming=incoming, outgoing=outgoing)
    assert "fan-in=2 centrality=3 exports=2; sensitive" in card.text


def test_duplicate_symbol_names_from_other_paths_do_not_leak_into_purpose() -> None:
    file = replace(FILE, path="src/cart.ts")
    symbols = (
        _row("other:Cart", "Cart", path="src/other.ts"),
        _row("cart:Cart", "Cart", path=file.path),
        _row("other:Secret", "Secret", path="src/other.ts"),
    )
    card = _card(file=file, symbols=symbols)
    assert card.purpose == "Code for Cart."
    assert "Secret" not in card.text
    assert "sensitive" not in card.text


def test_tests_facts_pitfalls_and_generated_flags_survive_a_typical_budget() -> None:
    note = _row(
        "note",
        "Generated. Do not edit.",
        "note",
        target="anchor:note",
        provenance="agent-note:current",
    )
    rule = _row(
        "rule", "Keep types.", "rule", path="CLAUDE.md", target="**", provenance="CLAUDE.md § Types"
    )
    test = _row("test", "npm test", "tests", path="tests/auth.test.ts", target=FILE.path, line=3)
    history = _row(
        "rejected",
        json.dumps({"action": "edit", "outcome": "rejected", "retries": 2, "signature": "timeout"}),
        "edit",
        provenance="ledger:R",
    )
    card = _card(notes=(note,), rules=(rule,), tests=(test,), history=(history,))
    assert "Flags generated; do-not-edit" in card.text
    assert "Rules CLAUDE.md § Types: Keep types." in card.text
    assert "Tests tests/auth.test.ts:3: npm test" in card.text
    assert "Fact src/auth.ts:1 [agent-note]:" in card.text
    assert "Pitfall ledger:R: rejected edit retries=2" in card.text
    assert len(card.text.encode()) <= 480


def test_history_shows_failures_and_retries_without_serialized_record_payload() -> None:
    failure = _row(
        "failed",
        json.dumps(
            {"action": "test", "outcome": "failed", "retries": 0, "signature": "missing dependency"}
        ),
        "test",
        provenance="ledger:F",
    )
    retry = _row(
        "retry",
        json.dumps({"action": "edit", "outcome": "accepted", "retries": 1}),
        "edit",
        provenance="ledger:T",
    )
    accepted = _row(
        "accepted",
        json.dumps({"action": "edit", "outcome": "accepted", "retries": 0}),
        "edit",
        provenance="ledger:A",
    )
    card = _card(history=(failure, retry, accepted))
    assert "failed test" in card.text and "missing dependency" in card.text
    assert "accepted edit retries=1" in card.text
    assert "ledger:A" not in card.text
    assert '"action"' not in card.text


def test_stale_edges_tests_rules_and_history_never_enter_card() -> None:
    stale = replace(_row("stale", "STALE_DATA", target=FILE.path), stale=True)
    card = _card(
        incoming=(stale,),
        outgoing=(stale,),
        tests=(stale,),
        history=(stale,),
        rules=(replace(stale, target="**"),),
    )
    assert "STALE_DATA" not in card.text
    assert "fan-in=0 centrality=0 exports=0" in card.text
    assert all(label not in card.text for label in ("Tests ", "Pitfall ", "Rules "))


def test_fenced_rule_code_is_not_sent_as_handling_instructions() -> None:
    rule = _row(
        "rule",
        "## Type rules\nKeep types strict.\n```python\nFULL_CODE_BODY\n```",
        "rule",
        path="CLAUDE.md",
        target="**",
        provenance="CLAUDE.md § Type rules",
    )
    card = _card(rules=(rule,))
    assert "Keep types strict." in card.text
    assert "FULL_CODE_BODY" not in card.text


def test_long_path_is_preserved_when_it_alone_exceeds_the_requested_budget() -> None:
    path = "src/" + "deep/" * 30 + "auth.ts"
    card = _card(file=replace(FILE, path=path), limit=10)
    assert card.text == path + " | code"
    assert card.path == path


@pytest.mark.parametrize("limit", [0, -1])
def test_nonpositive_token_limit_is_rejected(limit: int) -> None:
    with pytest.raises(ValueError, match="positive token limit"):
        _card(limit=limit)
