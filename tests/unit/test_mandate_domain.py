from __future__ import annotations

from pathlib import Path

import pytest

from cuanta.domain.instinct import heuristic_scope, heuristic_triage, scope_hint_line
from cuanta.domain.mandate import (
    REQUEST_MARKER,
    MandateRequest,
    Shape,
    TemplateError,
    analyst_system_prompt,
    evidence_from_failure,
    extract_block,
    fill_request,
    investigation_denied,
    investigation_tools,
    missing_fields,
    parse_shape,
    with_defaults,
)

TEMPLATE = (
    Path(__file__).parents[2]
    / "src"
    / "cuanta"
    / "assets"
    / "forge"
    / "skills"
    / "agent-system-init"
    / "templates"
    / "MANDATE_TEMPLATE.template.md"
)


def filled_template() -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    return text.replace("{{PROJECT_NAME}}", "shop").replace("{{SENIOR_NAME}}", "python-senior")


def test_block_above_request_is_byte_identical() -> None:
    block = extract_block(filled_template())
    request = MandateRequest(
        type="bug", what="fix totals", why="AssertionError: 30 != 31", out_of_scope="billing"
    )
    prompt = fill_request(
        block, request, "SCOPE HINT (cuanta instinct · heuristic): normal · p=0.60"
    )
    above = block[: block.index(REQUEST_MARKER)]
    assert prompt.startswith(above)
    tail = prompt[len(above) :]
    assert tail.startswith(
        "SCOPE HINT (cuanta instinct · heuristic): normal · p=0.60\n\n=== REQUEST ==="
    )
    assert "TYPE:            bug" in tail
    assert "OUT OF SCOPE:    billing" in tail
    assert "WHERE:           unknown" in tail
    assert "EXPECTED TESTS:  regression fixture for the pasted error" in tail


def test_multiline_evidence_is_indented() -> None:
    block = extract_block(filled_template())
    request = MandateRequest(type="bug", what="w", why="line one\nline two", out_of_scope="x")
    prompt = fill_request(block, request)
    assert "WHY / EVIDENCE:  line one\n                 line two" in prompt


def test_template_errors() -> None:
    with pytest.raises(TemplateError):
        extract_block("no marker here")
    with pytest.raises(TemplateError):
        extract_block("=== REQUEST === but no fences")
    with pytest.raises(TemplateError):
        fill_request("plain", MandateRequest())


def test_missing_fields() -> None:
    assert missing_fields(MandateRequest()) == ("type", "what", "why", "out_of_scope")
    assert missing_fields(MandateRequest("bug", "w", "e", out_of_scope="o")) == ()


def test_evidence_from_failure() -> None:
    text = evidence_from_failure(
        [("abc123", "KeyError: 'x'", 3, "tests/t.py::test_a", "src/a.py:9")], "pytest -q", "cap:ff"
    )
    assert "[abc123] x3 KeyError: 'x' at src/a.py:9" in text
    assert text.endswith("cuanta cat cap:ff --level L2")


def test_scope_heuristic() -> None:
    assert (
        heuristic_scope({"type": "bug", "what": "fix typo in README", "where": "README.md"}).option
        == "trivial"
    )
    assert (
        heuristic_scope(
            {"type": "bug", "what": "fix totals rounding", "where": "src/cart.py"}
        ).option
        == "normal"
    )
    complex_choice = heuristic_scope(
        {
            "type": "refactor",
            "what": "migrate every module across the subsystem boundary",
            "where": "",
            "signatures": 3,
        }
    )
    assert complex_choice.option == "complex"
    assert 0 < complex_choice.probability <= 0.9
    assert "trivial" in scope_hint_line(
        heuristic_scope({"what": "rename x", "where": "a"}), "heuristic"
    )


def test_triage_heuristic() -> None:
    assert (
        heuristic_triage({"error": "ConnectionError: connection refused"}).option == "environment"
    )
    assert heuristic_triage({"flips": 3}).option == "flaky"
    assert heuristic_triage({"seen_before": 2}).option == "pre-existing"
    assert heuristic_triage({}).option == "caused by this run"


def test_clip_evidence_keeps_head_tail_and_points_at_the_capsule() -> None:
    from cuanta.domain.mandate import INLINE_EVIDENCE_LIMIT, clip_evidence

    short = "boom"
    assert clip_evidence(short, "cap:x") == short
    text = "HEAD" + "m" * 100_000 + "TAIL"
    clipped = clip_evidence(text, "cap:abc")
    assert len(clipped) <= INLINE_EVIDENCE_LIMIT
    assert clipped.startswith("HEAD")
    assert "TAIL" in clipped
    assert "cuanta cat cap:abc --level L2" in clipped
    assert "100,008 characters" in clipped


def test_an_investigation_defaults_to_its_deliverable_not_a_regression_fixture() -> None:
    filled = with_defaults(MandateRequest(type="investigation", what="w", why="y"))
    assert filled.tests == "Deliverable: a written report"
    bug = with_defaults(MandateRequest(type="bug", what="w", why="y"))
    assert bug.tests == "regression fixture for the pasted error"
    chosen = with_defaults(MandateRequest(type="investigation", tests="Deliverable: a diagram"))
    assert chosen.tests == "Deliverable: a diagram"


def test_shapes_decide_delegation() -> None:
    assert parse_shape("PIPELINE") is Shape.PIPELINE
    assert parse_shape("") is Shape.SINGLE
    assert "Agent" not in investigation_tools(False, Shape.SINGLE)
    assert "Agent" in investigation_tools(False, Shape.PIPELINE)
    assert {"Agent", "Task"} <= set(investigation_denied(False, Shape.SINGLE))
    assert {"Agent", "Task"} <= set(investigation_denied(True, Shape.PIPELINE))
    assert "Agent" not in investigation_denied(False, Shape.PIPELINE)


def test_the_analyst_prompt_falls_back_and_overrides_json_contracts() -> None:
    fallback = analyst_system_prompt("", "")
    assert fallback.startswith("You are the codebase analyst")
    assert fallback.endswith("not in JSON.")
    body = analyst_system_prompt("Return ONLY JSON.", "READ BUDGET (quick): 8 files.")
    assert body.split("\n\n")[0] == "Return ONLY JSON."
    assert body.endswith("READ BUDGET (quick): 8 files.")
