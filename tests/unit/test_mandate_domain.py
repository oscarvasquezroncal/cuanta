from __future__ import annotations

import time
from pathlib import Path

import pytest

from cuanta.domain.instinct import heuristic_scope, heuristic_triage, scope_hint_line
from cuanta.domain.mandate import (
    MIN_PARTS,
    REPORT_LANGUAGE,
    REQUEST_MARKER,
    MandateRequest,
    PartKind,
    RequestParts,
    Shape,
    TemplateError,
    analyst_system_prompt,
    builtin_block,
    evidence_from_failure,
    extract_block,
    fill_request,
    first_part_offset,
    in_parts,
    investigation_builtin_tools,
    investigation_denied,
    investigation_tools,
    missing_fields,
    numbered_parts,
    parse_shape,
    part_spans,
    request_text,
    with_defaults,
)
from tests.real_run import phased_mandate

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


def test_only_single_context_investigations_name_builtin_tools() -> None:
    assert investigation_builtin_tools(False, Shape.SINGLE) == ("Read", "Grep", "Glob", "Bash")
    assert investigation_builtin_tools(True, Shape.PIPELINE) == ("Read", "Grep", "Glob", "Bash")
    assert investigation_builtin_tools(False, Shape.SINGLE, False) == ("Read", "Grep", "Glob")
    assert investigation_builtin_tools(False, Shape.PIPELINE) is None


@pytest.mark.parametrize(
    ("simple", "shape", "graph_available"),
    [
        (False, Shape.SINGLE, True),
        (False, Shape.PIPELINE, True),
        (True, Shape.SINGLE, True),
        (False, Shape.SINGLE, False),
        (False, Shape.PIPELINE, False),
    ],
)
def test_every_investigation_block_asks_for_the_request_language(
    simple: bool, shape: Shape, graph_available: bool
) -> None:
    block = builtin_block("investigation", simple, shape, graph_available)
    assert block is not None
    assert REPORT_LANGUAGE in block
    assert block.index(REPORT_LANGUAGE) < block.index(REQUEST_MARKER)
    assert "graph" not in REPORT_LANGUAGE.lower()
    assert "same language as the request" in REPORT_LANGUAGE


def test_the_analyst_prompt_falls_back_and_overrides_json_contracts() -> None:
    fallback = analyst_system_prompt("", "")
    assert fallback.startswith("You are the codebase analyst")
    assert fallback.endswith("not in JSON.")
    body = analyst_system_prompt("Return ONLY JSON.", "READ BUDGET (quick): 8 files.")
    assert body.split("\n\n")[0] == "Return ONLY JSON."
    assert body.endswith("READ BUDGET (quick): 8 files.")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("**FASE 1:** a\n**FASE 2:** b", RequestParts(PartKind.PHASE, 2)),
        ("- Phase 1: plan\n- Phase 2: build\n- Phase 3: ship", RequestParts(PartKind.PHASE, 3)),
        ("1. Fase I\n2. Fase II\n3. Fase III", RequestParts(PartKind.PHASE, 3)),
        ("> ## Etapa 1\n> ## Etapa 2\r\n> ## Etapa 2", RequestParts(PartKind.PHASE, 2)),
        ("* _Milestone 1:_ plan\n* _Stage 2:_ ship", RequestParts(PartKind.PHASE, 2)),
        (
            "Paso 1: crear\nPaso 2: probar\nPaso 3: documentar\nPaso 4: entregar",
            RequestParts(PartKind.STEP, 4),
        ),
        ("## Fase 1\nStep 1\nStep 2\nSteps 3", RequestParts(PartKind.STEP, 3)),
        ("## 1. Contexto\n## 2. Objetivo\n1) uno\n2) dos", RequestParts()),
        ("## Fase 1 — única\nla Fase 2 vendrá", RequestParts(PartKind.PHASE, 1)),
        ("Phase in the new client\nStepwise 2 changes\nhito 3", RequestParts(PartKind.PHASE, 1)),
        ("", RequestParts()),
    ],
)
def test_labelled_phases_and_steps_are_counted_once_each(text: str, expected: RequestParts) -> None:
    assert numbered_parts(text) == expected


@pytest.mark.parametrize(
    "blanks",
    [" " * 100_000, "## " + "\t" * 100_000, "- " + " " * 100_000, "> " * 50_000],
    ids=["indent", "heading", "bullet", "quote"],
)
def test_a_long_run_of_blanks_is_read_in_one_pass(blanks: str) -> None:
    text = f"{blanks}x\n## Fase 1\n## Fase 2"
    assert numbered_parts(text) == RequestParts(PartKind.PHASE, 2)
    assert first_part_offset(f"{blanks}x") is None
    assert part_spans(text) == ((len(f"{blanks}x\n"), len(text)),)
    assert part_spans(f"{blanks}x") == ()


def test_the_real_run_mandate_counts_five_phases_from_its_first_heading() -> None:
    text = phased_mandate()
    assert len(text.encode("utf-8")) > 11_000
    assert numbered_parts(text) == RequestParts(PartKind.PHASE, 5)
    assert numbered_parts(text).count >= MIN_PARTS
    offset = first_part_offset(text)
    assert offset is not None
    assert text[offset:].startswith("## Fase 1 — Auditoría")
    assert first_part_offset("intro\n**Paso 1:** crear") == len("intro\n")
    assert first_part_offset("## 1. Contexto\nla Fase 2 vendrá") is None


def _phase_text(text: str) -> list[str]:
    return [text[start:end] for start, end in part_spans(text)]


SECTIONED = (
    "Intro.\n"
    "## Fase 1 — Auditoría\n"
    "Leer.\n"
    "### Reglas de la fase\n"
    "No toques a.py.\n"
    "## Notas\n"
    "Global uno.\n"
    "## Fase 2 — Corrección\n"
    "Corregir.\n"
    "### Paso 2.1 — Detalle\n"
    "Más.\n"
    "## Reglas generales\n"
    "Global dos.\n"
)


def test_a_phase_ends_at_the_next_heading_of_its_level_or_above() -> None:
    assert _phase_text(SECTIONED) == [
        "## Fase 1 — Auditoría\nLeer.\n### Reglas de la fase\nNo toques a.py.\n",
        "## Fase 2 — Corrección\nCorregir.\n### Paso 2.1 — Detalle\nMás.\n",
    ]
    spans = part_spans(SECTIONED)
    assert not in_parts(0, spans)
    assert in_parts(SECTIONED.index("No toques"), spans)
    assert not in_parts(SECTIONED.index("Global uno"), spans)
    assert in_parts(SECTIONED.index("Más."), spans)
    assert not in_parts(SECTIONED.index("Global dos"), spans)
    assert not in_parts(len(SECTIONED), spans)


@pytest.mark.parametrize(
    ("text", "phases"),
    [
        (
            "**Fase 1:** a\n**Fase 2:** b\n**Reglas**\nNo toques x.\n",
            ["**Fase 1:** a\n**Fase 2:** b\n"],
        ),
        (
            "- Phase 1: plan\n- Phase 2: build\n- Do not touch y.\n## Rules\nDo not modify z.\n",
            ["- Phase 1: plan\n- Phase 2: build\n- Do not touch y.\n"],
        ),
        (
            "# Plan\n### Fase 1\na\n### Fase 2\nb\n## Reglas\nc\n",
            ["### Fase 1\na\n### Fase 2\nb\n"],
        ),
        (
            "## Fase 1\na\n#### Nota\nb\n## Fase 2\nc",
            ["## Fase 1\na\n#### Nota\nb\n## Fase 2\nc"],
        ),
        (
            "## Fase 1\r\na\r\n## Fase 2\r\nb\r\n## Reglas\r\nc\r\n",
            ["## Fase 1\r\na\r\n## Fase 2\r\nb\r\n"],
        ),
        (
            "> ## Etapa 1\n> a\n> ## Etapa 2\n> b\n> ## Notas\n> c",
            ["> ## Etapa 1\n> a\n> ## Etapa 2\n> b\n"],
        ),
        (
            "## Fase 1\n```python\n# antes\n## Fase 9\n```\nNo toques x.\n## Fase 2\nb\n",
            ["## Fase 1\n```python\n# antes\n## Fase 9\n```\nNo toques x.\n## Fase 2\nb\n"],
        ),
        (
            "## Fase 1\n```inline``` code\n## Fase 2\nb\n## Reglas\nc\n",
            ["## Fase 1\n```inline``` code\n## Fase 2\nb\n"],
        ),
        (
            "## Fase 1\na\n````\n```\n## Reglas\n````\nb\n## Fase 2\nc\n## Notas\nd\n",
            ["## Fase 1\na\n````\n```\n## Reglas\n````\nb\n## Fase 2\nc\n"],
        ),
        (
            "## Fase 1\n~~~\n```\n~~~\n## Notas\nx\n",
            ["## Fase 1\n~~~\n```\n~~~\n"],
        ),
        (
            "## Fase 1\n```python\n```python\n## Notas\n```\n## Notas\nx\n",
            ["## Fase 1\n```python\n```python\n## Notas\n```\n"],
        ),
        ("## Contexto\nSin fases.\n", []),
        ("", []),
    ],
    ids=[
        "bold",
        "list",
        "deeper",
        "nested",
        "crlf",
        "quoted",
        "fenced",
        "inline_fence",
        "long_fence",
        "tilde_fence",
        "info_string_fence",
        "none",
        "empty",
    ],
)
def test_each_labelled_part_spans_its_body_and_nothing_after_its_section(
    text: str, phases: list[str]
) -> None:
    assert _phase_text(text) == phases


def test_the_real_run_phases_end_where_its_closing_sections_begin() -> None:
    text = phased_mandate()
    spans = part_spans(text)
    assert len(spans) == 1
    start, end = spans[0]
    assert text[start:].startswith("## Fase 1 — Auditoría")
    assert text[end:].startswith("## Cómo trabajar")
    assert text[:end].rstrip().endswith("promesas sobre versiones futuras.")


SPAN_BYTES = 200_000
SPAN_BUDGET_S = 0.5


@pytest.mark.perf
@pytest.mark.parametrize(
    "unit",
    [
        "## Fase 1\nNo toques a.py.\n## Notas\nb\n",
        "### Paso 2\n#### Nota\n",
        "- " + " " * 997 + "x\n",
        "**Fase 3:** " + "*" * 500 + "\n",
        "**" + "a" * 2000 + "\n",
        "```\n# x\n",
    ],
    ids=["sections", "nested", "bullet", "stars", "open-bold", "fences"],
)
def test_phase_spans_of_a_200_kb_request_stay_within_budget(unit: str) -> None:
    text = (unit * (SPAN_BYTES // len(unit) + 1))[:SPAN_BYTES]
    started = time.perf_counter()
    spans = part_spans(text)
    assert time.perf_counter() - started < SPAN_BUDGET_S
    assert all(start < end for start, end in spans)


def test_the_request_text_joins_every_field_in_order_and_skips_empty_ones() -> None:
    full = MandateRequest("feature", "w", "evidence", "src/a.py", "c", "t", "o")
    assert request_text(full) == "feature\nw\nevidence\nsrc/a.py\nc\nt\no"
    sparse = MandateRequest("bug", "  fix add  ", "", where=" ", out_of_scope="tests\n")
    assert request_text(sparse) == "bug\nfix add\ntests"
    assert request_text(MandateRequest()) == ""
