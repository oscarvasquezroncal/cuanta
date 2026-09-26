from __future__ import annotations

from datetime import date

from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.report import (
    FileRef,
    context_split,
    docs_path,
    file_refs,
    first_request_event,
    link_file_refs,
    next_request,
    parse_sections,
    slug,
    strip_preamble,
    unified_diff,
)

FORGE_REPORT = """Done in one pass.

## FINAL REPORT

1. **WHAT WAS DONE** — phase 1: `src/shop/cart.py` now sums line totals.
2. **RECOMMENDATIONS**
   - rounding drifts in src/shop/tax.py:41 when the rate has three decimals.
3. **DOCS UPDATED** — CHANGELOG_INTERNAL.md
4. **COMMIT PROPOSAL** — fix(cart): sum line totals
5. **NEXT** — close IMP-004.

```
=== REQUEST ===

TYPE:            bug | feature
WHAT:            Round tax per line
WHY / EVIDENCE:  src/shop/tax.py:41 rounds once per cart
                 which drifts by a cent
WHERE:           src/shop/tax.py
OUT OF SCOPE:    the cart API
```
"""


def test_forge_final_report_splits_into_its_sections() -> None:
    sections = parse_sections(FORGE_REPORT)
    keys = [section.key for section in sections]
    assert keys == ["preamble", "done", "recommendations", "docs", "commit", "next"]
    assert sections[1].body.startswith("phase 1")
    assert "tax.py:41" in sections[2].body


def test_plain_text_is_not_split() -> None:
    assert parse_sections("All good.\nNext I would look at the tests.") == ()


def test_report_preamble_is_stripped_at_the_first_heading() -> None:
    text = "I have enough evidence.\n\n## SUMMARY\nok\n\n## FINDINGS\n- src/x.py:1\n"
    cleaned = strip_preamble(text)
    assert cleaned == text[text.index("## SUMMARY") :]
    assert parse_sections(cleaned)[0].key == "summary"
    assert strip_preamble(cleaned) == cleaned
    assert strip_preamble(FORGE_REPORT).startswith("## FINAL REPORT")
    assert strip_preamble("Done.\n\n## SUMMARY\nok\n") == "## SUMMARY\nok\n"


def test_report_preamble_skips_fenced_headings_and_preserves_narratives() -> None:
    fenced = "Ran:\n```\n# command\n```\n## SUMMARY\nok\n## NEXT\n- n\n"
    assert strip_preamble(fenced) == "## SUMMARY\nok\n## NEXT\n- n\n"
    numbered = "Done.\n\n1. **WHAT WAS DONE** - x\n2. **NEXT** - y\n"
    assert strip_preamble(numbered).startswith("1. **WHAT WAS DONE**")
    narrative = "I finished the refresh.\n\n**What was out of date**\n- x\n"
    assert strip_preamble(narrative) == narrative
    plain = "All good.\nNext I would look at the tests."
    assert strip_preamble(plain) == plain


def test_next_section_becomes_a_prefilled_request() -> None:
    request = next_request(FORGE_REPORT)
    assert request is not None
    assert request.type == "bug"
    assert request.what == "Round tax per line"
    assert request.why == "src/shop/tax.py:41 rounds once per cart\nwhich drifts by a cent"
    assert request.where == "src/shop/tax.py"
    assert request.out_of_scope == "the cart API"


def test_next_without_a_request_block_uses_its_first_line() -> None:
    text = "## SUMMARY\nfine\n\n## NEXT STEP\n- Add a test for empty carts.\n"
    request = next_request(text)
    assert request is not None
    assert request.what == "Add a test for empty carts."


def test_file_references_are_found_and_linked_outside_code_fences() -> None:
    text = "see src/app/page.tsx:12 and page.tsx:12:4\n```\nnot/linked.py:3\n```\nREADME.md:7"
    refs = file_refs(text)
    assert FileRef("src/app/page.tsx", 12) in refs
    assert FileRef("README.md", 7) in refs
    linked = link_file_refs(text, "cuanta-file:")
    assert "[src/app/page.tsx:12](cuanta-file:src/app/page.tsx:12)" in linked
    assert "not/linked.py:3\n" in linked
    assert "(cuanta-file:not/linked.py:3)" not in linked


def test_docs_path_puts_investigations_in_their_folder() -> None:
    day = date(2026, 9, 24)
    assert docs_path("investigation", "Revisión de la landing!", day) == (
        "docs/investigations/2026-09-24-revision-de-la-landing.md"
    )
    assert docs_path("bug", "", day) == "docs/runs/2026-09-24-report.md"
    assert len(slug("x" * 200)) <= 48


def test_context_split_uses_the_first_request() -> None:
    events = [
        LedgerEvent(kind="api_request", ts="2", input_tokens=10, cache_read_tokens=5),
        LedgerEvent(
            kind="api_request",
            ts="1",
            input_tokens=4,
            cache_read_tokens=20_482,
            cache_write_tokens=32_625,
        ),
        LedgerEvent(kind="tool_result", ts="0", input_tokens=999),
    ]
    split = context_split(events, 5_641)
    assert split is not None
    assert split.first_request == 53_111
    assert split.request == 1_410
    assert split.fixed == 51_701
    assert 0.97 < split.fixed_share < 0.98
    assert context_split([], 100) is None
    assert first_request_event(events) is events[1]
    assert first_request_event([]) is None


def test_unified_diff_marks_changes() -> None:
    diff = unified_diff("a\nb\n", "a\nc\n", "x.py")
    assert "--- a/x.py" in diff
    assert "-b" in diff
    assert "+c" in diff
