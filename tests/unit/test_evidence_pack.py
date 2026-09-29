from __future__ import annotations

import json
from collections.abc import Sequence

from cuanta.domain.change_plan import ChangePlan, EditTarget
from cuanta.domain.evidence_pack import (
    FACT_LIMIT,
    PACK_TOKENS,
    SNIPPET_LINES,
    TOP_FACTS,
    EvidencePack,
    PackSource,
    Snippet,
    change_digest,
    check_pack,
    fallback_pack,
    leaked_reads,
    limit_pack,
    outside_edits,
    pack_handoff,
    pack_instruction,
    pack_tokens,
    parse_pack,
    render_pack,
    safe_path,
    scope_plan,
    touched_files,
    trim_pack,
    validate_pack,
)
from cuanta.domain.role_handoff import Fact, HandoffSource, HandoffStatus

LINES = {"src/cart.ts": 200, "src/price.ts": 80, "tests/cart.test.ts": 40}


def lines_of(path: str) -> Sequence[str] | None:
    count = LINES.get(path)
    return None if count is None else [f"line {index}" for index in range(1, count + 1)]


def pack_json(**extra: object) -> str:
    data: dict[str, object] = {
        "summary": "cart totals come from price.ts",
        "facts": [
            {"path": "src/cart.ts", "start": 10, "end": 20, "claim": "total"},
            "src/price.ts:5-9 rounding",
        ],
        "snippets": [
            {"path": "src/cart.ts", "start": 10, "end": 12, "text": "const total = sum()"},
            {"anchor": "src/price.ts:5-6", "code": "round(x)"},
        ],
        "risks": ["rounding differs per currency"],
        "tests": ["tests/cart.test.ts"],
        "edit_set": ["src/cart.ts", ".\\src\\price.ts"],
        "status": "done",
    }
    data.update(extra)
    return "Explored the cart.\n```json\n" + json.dumps(data) + "\n```"


def big_pack(facts: int, snippets: int, edit: tuple[str, ...]) -> EvidencePack:
    return EvidencePack(
        summary="big",
        facts=tuple(
            Fact("src/cart.ts", index, index, f"claim number {index} " + "x" * 120)
            for index in range(1, facts + 1)
        ),
        snippets=tuple(
            Snippet("src/cart.ts", index, index + 30, "\n".join(["y" * 90] * 31))
            for index in range(1, snippets + 1)
        ),
        edit=edit,
    )


def test_a_pack_reads_facts_snippets_risks_tests_and_the_edit_set() -> None:
    pack = parse_pack(pack_json())
    assert pack is not None
    assert pack.summary == "cart totals come from price.ts"
    assert [fact.anchor for fact in pack.facts] == ["src/cart.ts:10-20", "src/price.ts:5-9"]
    assert [item.anchor for item in pack.snippets] == ["src/cart.ts:10-12", "src/price.ts:5-6"]
    assert pack.risks == ("rounding differs per currency",)
    assert pack.tests == ("tests/cart.test.ts",)
    assert pack.edit == ("src/cart.ts", "src/price.ts")
    assert pack.files == ("src/cart.ts", "src/price.ts")
    assert (pack.status, pack.source) == (HandoffStatus.DONE, PackSource.JSON)
    assert parse_pack("no json at all") is None
    planned = parse_pack(pack_json(edit_set=None, plan={"edit": ["src/cart.ts"]}))
    assert planned is not None and planned.edit == ()
    nested = parse_pack('{"plan": {"edit": ["src/cart.ts"]}, "summary": "s"}')
    assert nested is not None and nested.edit == ("src/cart.ts",)


def test_validation_drops_items_outside_the_working_copy_and_unsafe_paths() -> None:
    pack = EvidencePack(
        facts=(
            Fact("src/cart.ts", 1, 200),
            Fact("src/cart.ts", 150, 201),
            Fact("src/gone.ts", 1, 2),
            Fact("../secrets.txt", 1, 1),
        ),
        snippets=(Snippet("src/price.ts", 1, 2, "a"), Snippet("src/price.ts", 79, 81, "b")),
        edit=("src/cart.ts", "src/new_file.ts", "/etc/passwd", "C:/Windows/x", "a/../b"),
    )
    valid, invalid = validate_pack(pack, lines_of)
    assert [fact.anchor for fact in valid.facts] == ["src/cart.ts:1-200"]
    assert [item.anchor for item in valid.snippets] == ["src/price.ts:1-2"]
    assert valid.edit == ("src/cart.ts", "src/new_file.ts")
    assert invalid == 3 + 1 + 3
    unchecked, dropped = validate_pack(pack, None)
    assert len(unchecked.facts) == 3 and dropped == 1 + 3
    assert not safe_path("") and not safe_path("..\\up") and safe_path("docs/a.md")


def test_validation_replaces_snippet_text_with_the_lines_of_its_range() -> None:
    pack = EvidencePack(snippets=(Snippet("src/price.ts", 3, 4, "made up by the scout"),))
    valid, invalid = validate_pack(pack, lines_of)
    assert [item.text for item in valid.snippets] == ["line 3\nline 4"] and invalid == 0
    unchecked, _ = validate_pack(pack, None)
    assert unchecked.snippets[0].text == "made up by the scout"


def test_touched_files_maps_tool_paths_onto_the_changed_files() -> None:
    changed = ("a.py", "sub/a.py", "src/Cart.ts", "tests/cart.test.ts")
    touched = ("/repo/sub/a.py", "C:\\repo\\src\\cart.ts", "/repo/other.py", "./a.py")
    assert touched_files(changed, touched) == ("a.py", "sub/a.py", "src/Cart.ts")
    assert touched_files(changed, ()) == ()


def test_trimming_drops_snippets_first_then_facts_beyond_the_top_and_never_the_edit_set() -> None:
    edit = tuple(f"src/file_{index}.ts" for index in range(30))
    pack = big_pack(facts=30, snippets=8, edit=edit)
    assert pack_tokens(pack) > PACK_TOKENS
    budget = pack_tokens(big_pack(facts=20, snippets=0, edit=edit))
    trimmed, snippets, facts = trim_pack(pack, budget)
    assert snippets == 8 and trimmed.snippets == ()
    assert facts == 10 and len(trimmed.facts) == 20
    assert trimmed.facts == pack.facts[:20]
    assert trimmed.edit == edit
    assert pack_tokens(trimmed) <= budget
    roomy, kept_snippets, kept_facts = trim_pack(pack, pack_tokens(pack))
    assert (roomy, kept_snippets, kept_facts) == (pack, 0, 0)
    partial, only_snippets, no_facts = trim_pack(pack, pack_tokens(big_pack(30, 2, edit)))
    assert (len(partial.snippets), only_snippets, no_facts) == (2, 6, 0)
    assert trim_pack(pack)[1:] == (8 - len(trim_pack(pack)[0].snippets), 0)


def test_a_pack_still_over_budget_keeps_its_edit_set_and_says_so() -> None:
    edit = tuple(
        f"src/module_{index}/very/long/path/to/a/component_file.tsx" for index in range(90)
    )
    pack = big_pack(facts=TOP_FACTS + 10, snippets=3, edit=edit)
    check = check_pack(pack, None, budget=500)
    assert check.over_budget and check.trimmed
    assert check.pack.edit == edit
    assert len(check.pack.facts) == TOP_FACTS
    assert check.pack.snippets == ()
    assert (check.dropped_snippets, check.dropped_facts) == (3, 10)
    assert check.tokens == pack_tokens(check.pack) > check.budget
    assert check.raw_tokens == pack_tokens(pack)


def test_section_limits_cut_long_sections_and_clip_snippets() -> None:
    pack = EvidencePack(
        facts=tuple(Fact("src/cart.ts", index, index) for index in range(1, FACT_LIMIT + 6)),
        snippets=(Snippet("src/cart.ts", 1, 100, "\n".join(f"l{i}" for i in range(100))),),
        risks=tuple(f"risk {index}" for index in range(12)),
        tests=tuple(f"tests/t{index}.ts" for index in range(15)),
    )
    limited, count = limit_pack(pack)
    assert len(limited.facts) == FACT_LIMIT
    assert len(limited.risks) == 8 and len(limited.tests) == 12
    assert count == 5 + 4 + 3
    snippet = limited.snippets[0]
    assert len(snippet.text.splitlines()) == SNIPPET_LINES
    assert snippet.end == SNIPPET_LINES


def test_an_empty_edit_set_falls_back_to_the_change_plan_and_a_missing_pack_to_the_reads() -> None:
    parsed = parse_pack(pack_json(edit_set=[]))
    assert parsed is not None
    check = check_pack(parsed, lines_of, ("src/cart.ts",))
    assert check.edit_from_plan and check.pack.edit == ("src/cart.ts",)
    confirmed = check_pack(parsed, lines_of, ())
    assert not confirmed.edit_from_plan and confirmed.pack.edit == ()
    fallback = fallback_pack("I read the cart", (Fact("src/cart.ts", 1, 5, "read"),), ())
    assert (fallback.source, fallback.status) == (PackSource.FALLBACK, HandoffStatus.PARTIAL)
    handoff = pack_handoff(check_pack(fallback, lines_of, ("src/cart.ts",)), "scout")
    assert handoff.source is HandoffSource.FALLBACK
    assert handoff.plan.edit == ("src/cart.ts",)


def test_the_pack_handoff_and_the_senior_scope_carry_the_edit_set_and_the_pack_files() -> None:
    parsed = parse_pack(pack_json())
    assert parsed is not None
    check = check_pack(parsed, lines_of)
    handoff = pack_handoff(check, "scout", engine="codex", model="luna", capsule="cap:1")
    assert handoff.plan.edit == ("src/cart.ts", "src/price.ts")
    assert [fact.anchor for fact in handoff.facts] == ["src/cart.ts:10-20", "src/price.ts:5-9"]
    assert handoff.open_questions == ("rounding differs per currency",)
    assert (handoff.engine, handoff.model, handoff.capsule) == ("codex", "luna", "cap:1")
    guarded = ChangePlan(
        edit=(EditTarget("src/other.ts", 0.4),), read=("x",), guard=("secrets/**",), read_only=True
    )
    scope = scope_plan(guarded, check.pack)
    assert [target.path for target in scope.edit] == ["src/cart.ts", "src/price.ts"]
    assert scope.read == ()
    assert scope.guard == ("secrets/**",) and not scope.read_only
    text = render_pack(check.pack)
    assert "Edit set (the files you may edit):\n- src/cart.ts\n- src/price.ts" in text
    assert "--- src/cart.ts:10-12\nline 10\nline 11\nline 12" in text
    assert "const total = sum()" not in text
    assert "about 6,000 tokens" in pack_instruction()


def test_outside_edits_are_split_by_whether_the_senior_named_them() -> None:
    found = outside_edits(
        ("src/cart.ts", "src/extra.ts", "SRC/Other.ts"),
        ("src/cart.ts",),
        ("./src/other.ts",),
    )
    assert found.named == ("SRC/Other.ts",)
    assert found.unnamed == ("src/extra.ts",)
    assert found.paths == ("SRC/Other.ts", "src/extra.ts")
    leaks = leaked_reads(
        ("src/cart.ts", "src/price.ts", "src/unrelated.ts", "src/unrelated.ts"),
        ("src/cart.ts",),
        ("src/price.ts",),
    )
    assert leaks == ("src/unrelated.ts",)


def test_the_change_digest_shows_diffs_within_its_budget_and_lists_the_rest() -> None:
    before: dict[str, str | None] = {"a.ts": "one\ntwo", "new.ts": None, "big.ts": "x"}
    after = {"a.ts": "one\nthree", "new.ts": "fresh", "big.ts": "y\n" * 400, "other.ts": "z"}
    digest = change_digest(("a.ts", "new.ts", "big.ts", "other.ts"), before, after.get, 50)
    assert "--- a/a.ts" in digest and "+three" in digest
    assert "+fresh" in digest
    assert digest.endswith("Changed without a diff here (open them): big.ts, other.ts")
    assert change_digest((), before, after.get) == ""
