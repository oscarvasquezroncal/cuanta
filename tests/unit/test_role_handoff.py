from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest

from cuanta.domain.role_handoff import (
    Fact,
    HandoffPlan,
    HandoffSource,
    HandoffStatus,
    RoleHandoff,
    VerifyResult,
    build_handoff,
    estimate_tokens,
    handoff_budget,
    last_json_object,
    line_digest,
    merge_chain,
    parse_anchor,
    refresh_chain,
    refresh_facts,
    render_chain,
)

FILES = {
    "src/a.ts": ["line one", "line two", "line three", "line four"],
    "src/b.ts": ["only"],
}


def lines_of(files: dict[str, list[str]]) -> Callable[[str], Sequence[str] | None]:
    def read(path: str) -> Sequence[str] | None:
        return files.get(path)

    return read


def test_the_last_json_object_wins_over_prose_and_earlier_objects() -> None:
    text = (
        'I found {"not": "this"} and more.\n\n```json\n{"summary": "done", "status": "done"}\n```'
    )
    assert last_json_object(text) == {"summary": "done", "status": "done"}
    trailing = 'Notes first.\n{"summary": "tail", "facts": [{"path": "a", "start": 1}]}'
    assert last_json_object(trailing) == {"summary": "tail", "facts": [{"path": "a", "start": 1}]}
    assert last_json_object("no json here") is None
    assert last_json_object('{"broken": ') is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("src/a.ts:12-20", ("src/a.ts", 12, 20)),
        ("src/a.ts:7", ("src/a.ts", 7, 7)),
        ("./src/a.ts:3:9 claim", ("src/a.ts", 3, 9)),
        ("src\\a.ts:2-4", ("src/a.ts", 2, 4)),
        ("src/a.ts:9-3", None),
        ("no anchor", None),
    ],
)
def test_anchors_parse_path_and_line_ranges(
    text: str, expected: tuple[str, int, int] | None
) -> None:
    assert parse_anchor(text) == expected


def test_a_json_handoff_is_structured_and_files_changed_come_from_snapshots() -> None:
    text = (
        "Analysis complete.\n"
        '{"summary": "Header needs a canonical tag", "decisions": ["keep metadata in layout"], '
        '"facts": [{"path": "src/a.ts", "start": 2, "end": 3, "claim": "metadata export"}, '
        '"src/b.ts:1 single line", {"anchor": "src/a.ts:4-4", "claim": "footer"}, '
        '{"path": "src/a.ts", "lines": "1-2", "claim": "imports"}, {"path": "bad"}], '
        '"plan": {"edit": ["src/a.ts"], "read": ["src/b.ts"], "verify": ["npm run lint"]}, '
        '"files_changed": ["claimed.ts"], "open_questions": ["which domain?"], '
        '"next_step": "edit layout", "status": "done"}'
    )
    handoff = build_handoff(
        text, "analyst", engine="claude", model="sonnet", changed=("src/real.ts",), run_id="R1"
    )
    assert handoff.source is HandoffSource.JSON
    assert handoff.status is HandoffStatus.DONE
    assert handoff.summary == "Header needs a canonical tag"
    assert handoff.decisions == ("keep metadata in layout",)
    assert [fact.anchor for fact in handoff.facts] == [
        "src/a.ts:2-3",
        "src/b.ts:1-1",
        "src/a.ts:4-4",
        "src/a.ts:1-2",
    ]
    assert handoff.facts[1].claim == "single line"
    assert handoff.plan == HandoffPlan(("src/a.ts",), ("src/b.ts",), ("npm run lint",))
    assert handoff.files_changed == ("src/real.ts",)
    assert handoff.open_questions == ("which domain?",)
    assert handoff.next_step == "edit layout"
    assert handoff.run_id == "R1"


def test_missing_json_falls_back_to_text_and_telemetry_reads() -> None:
    handoff = build_handoff(
        "First paragraph.\n\nThe canonical tag is missing.",
        "analyst",
        reads=(("src/a.ts", 1, 4),),
        changed=(),
    )
    assert handoff.source is HandoffSource.FALLBACK
    assert handoff.status is HandoffStatus.PARTIAL
    assert handoff.summary == "The canonical tag is missing."
    assert handoff.facts == (Fact("src/a.ts", 1, 4, "read by analyst"),)


def test_a_budget_stop_becomes_a_partial_salvage_handoff() -> None:
    handoff = build_handoff(
        "Reading files", "analyst", reads=(("src/b.ts", 1, 1),), stopped="error_max_budget_usd"
    )
    assert handoff.source is HandoffSource.SALVAGE
    assert handoff.status is HandoffStatus.PARTIAL
    assert handoff.reason == "error_max_budget_usd"
    assert handoff.facts == (Fact("src/b.ts", 1, 1, "read by analyst"),)
    unknown = build_handoff('{"status": "maybe", "summary": "x"}', "senior")
    assert unknown.status is HandoffStatus.DONE


def test_refresh_stamps_hashes_then_marks_changed_or_missing_ranges_stale() -> None:
    files = {key: list(value) for key, value in FILES.items()}
    facts = (
        Fact("src/a.ts", 2, 3, "x"),
        Fact("src/b.ts", 1, 1, "y"),
        Fact("src/gone.ts", 1, 1),
        Fact("src/a.ts", 3, 9),
    )
    stamped = refresh_facts(facts, lines_of(files))
    assert stamped[0].line_hash == line_digest(["line two", "line three"])
    assert [fact.stale for fact in stamped] == [False, False, True, True]
    files["src/a.ts"][1] = "line 2 edited"
    again = refresh_facts(stamped, lines_of(files))
    assert [fact.stale for fact in again] == [True, False, True, True]
    files["src/a.ts"][1] = "line two"
    assert not refresh_facts(again[:1], lines_of(files))[0].stale


def test_the_chain_accumulates_every_role_and_later_roles_win() -> None:
    analyst = RoleHandoff(
        "analyst",
        "claude",
        "sonnet",
        HandoffStatus.DONE,
        summary="found it",
        decisions=("use layout",),
        facts=(Fact("src/a.ts", 1, 2, "old claim"), Fact("src/b.ts", 1, 1, "b")),
        plan=HandoffPlan(("src/a.ts",), ("src/b.ts", "src/a.ts"), ("npm run lint",)),
        verification=(VerifyResult("npm run lint", 1, 2.0, ("src/a.ts:3 bad",)),),
        open_questions=("domain?",),
        next_step="edit",
    )
    senior = RoleHandoff(
        "senior",
        "codex",
        "gpt",
        HandoffStatus.PARTIAL,
        summary="edited",
        facts=(Fact("src/a.ts", 1, 2, "new claim"),),
        plan=HandoffPlan((), (), ("npm run build",)),
        files_changed=("src/a.ts",),
        verification=(VerifyResult("npm run lint", 0, 1.0),),
        next_step="test",
    )
    chain = merge_chain((analyst, senior))
    assert [handoff.role for handoff in chain.handoffs] == ["analyst", "senior"]
    assert [(fact.anchor, fact.claim) for fact in chain.facts] == [
        ("src/b.ts:1-1", "b"),
        ("src/a.ts:1-2", "new claim"),
    ]
    assert chain.plan == HandoffPlan(
        ("src/a.ts",), ("src/b.ts",), ("npm run lint", "npm run build")
    )
    assert chain.decisions == ("analyst: use layout",)
    assert chain.files_changed == ("src/a.ts",)
    assert [item.passed for item in chain.verification] == [True]
    assert chain.open_questions == ()
    assert chain.next_step == "test"
    assert chain.covered == ("src/b.ts", "src/a.ts")
    assert merge_chain(()).handoffs == ()


def test_rendering_marks_stale_facts_and_fits_the_depth_budget() -> None:
    facts = tuple(Fact("src/a.ts", n, n, f"claim {n} " + "x" * 40) for n in range(1, 60))
    handoff = RoleHandoff(
        "analyst",
        "claude",
        "sonnet",
        HandoffStatus.DONE,
        summary="summary",
        facts=(*facts, Fact("src/b.ts", 1, 1, "fresh")),
        verification=(VerifyResult("npm run build", 2, 3.5, ("src/a.ts:4 Type error",)),),
        capsule="C123",
        next_step="edit a",
    )
    chain = refresh_chain(merge_chain((handoff,)), lines_of({"src/b.ts": ["only"]}))
    text = render_chain(chain, 300)
    assert "- analyst [claude/sonnet] done: summary; full text: cuanta cat C123" in text
    assert "Next step: edit a" in text
    assert "`npm run build`: exit 2, 3.5s" in text
    assert "  src/a.ts:4 Type error" in text
    assert "- src/b.ts:1-1 fresh" in text
    assert "more items omitted" in text
    assert estimate_tokens(text) <= 300
    wide = render_chain(chain, 50_000)
    assert "Stale facts" in wide and "- src/a.ts:59-59 claim 59" in wide
    assert render_chain(merge_chain(()), 100) == ""


@pytest.mark.parametrize(
    ("depth", "tokens"),
    [("quick", 1200), ("normal", 2000), ("deep", 3000), ("", 2000), ("odd", 2000)],
)
def test_handoff_budgets_follow_depth(depth: str, tokens: int) -> None:
    assert handoff_budget(depth) == tokens


def test_verification_states_are_explicit() -> None:
    assert VerifyResult("a", 0).passed
    assert not VerifyResult("a", 0, timed_out=True).passed
    assert not VerifyResult("a", None).passed
    chain = merge_chain(
        (
            RoleHandoff(
                "senior",
                verification=(
                    VerifyResult("slow", 124, 900.0, timed_out=True),
                    VerifyResult("missing", None, 0.0, ("not found",)),
                ),
            ),
        )
    )
    text = render_chain(chain, 2000)
    assert "`slow`: timed out" in text
    assert "`missing`: did not start" in text
