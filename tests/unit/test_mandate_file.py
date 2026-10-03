from __future__ import annotations

import codecs
from pathlib import Path

import pytest

from cuanta.domain.errors import DomainFailure
from cuanta.domain.mandate import (
    ESSENTIAL_FIELDS,
    NONE_STATED,
    SIMPLE_BLOCK,
    MandateRequest,
    fill_request,
    missing_fields,
    with_defaults,
)
from cuanta.domain.mandate_file import (
    NotText,
    decoded_text,
    mandate_type,
    parse_mandate_file,
    title_and_rest,
    with_overrides,
)
from tests.real_run import phased_mandate

REAL_TITLE = "Mandato — refactor del intérprete de consultas y del endpoint de consulta"
FORGE = Path(__file__).parents[2] / "src/cuanta/assets/forge"
VENDORED = FORGE / "skills/agent-system-init/templates/MANDATE_TEMPLATE.template.md"
LABELLED_ES = """# Botón de WhatsApp

**TIPO:** funcionalidad
Qué: un botón de WhatsApp en cada producto
Por qué: los clientes lo piden en la encuesta.
  Llega desde el formulario de contacto.
Dónde: src/components/ProductCard.tsx
Restricciones: sin dependencias nuevas
Pruebas: npm run build en verde
Fuera de alcance: el checkout
"""
EVERY_FIELD = ("type", "what", "why", "where", "constraints", "tests", "out_of_scope")
PROSE_FILES = {
    "fuera_de_alcance": (
        "# Arreglar el endpoint de consulta\n\n## Fase 1\n\nFuera de alcance: el modelo antiguo.\n"
        "Revisa app/routers/consult.py:3 y corrige el acceso a result[0].\n\n## Fase 2\n\n"
        "Añade la prueba de regresión.\n"
    ),
    "restricciones": (
        "# Corregir el intérprete\n\nRestricciones: mantén la API pública del endpoint.\n"
        "Corrige app/services/interpreter.py:287-289 para que acumule las intenciones.\n"
    ),
    "type_sentence": (
        "# Freeze the answer type\n\nType: the `Answer` type must stay frozen.\n"
        "Add a field to app/services/interpreter.py.\n"
    ),
    "tests_bullet": (
        "# Fix the cart\n\nSome context paragraph.\n\n- Tests: run them all before you finish.\n"
        "- Keep app/routers/consult.py as is.\n\nMore prose here about the fix.\n"
    ),
}
FILLED_REQUEST = (
    "TYPE:            bug\n"
    "WHAT:            The consult endpoint crashes on an empty answer list\n"
    "WHY / EVIDENCE:  IndexError: list index out of range\n"
    "WHERE:           app/routers/consult.py\n"
    "CONSTRAINTS:     <invariants, flag directions, what must stay byte-identical>\n"
    "EXPECTED TESTS:  <what proof you want; "
    '"regression fixture for the pasted error" is a good default>\n'
    "OUT OF SCOPE:    billing\n"
)
CONSULT_BUG = MandateRequest(
    type="bug",
    what="The consult endpoint crashes on an empty answer list",
    why="IndexError: list index out of range",
    where="app/routers/consult.py",
    out_of_scope="billing",
)
PLAIN_TITLE = (
    "Refactor del intérprete de consultas y del endpoint\n"
    "El intérprete descarta intenciones; hay que acumularlas.\n\n"
    "## Fase 1 — Auditoría\n\nRevisa app/services/interpreter.py:287-289.\n\n"
    "## Fase 2 — Corrección\n\nAcumula las intenciones.\n"
)
SPANISH_TEXT = (
    "# Arreglar el intérprete\n\nEl bucle hace break en app/services/interpreter.py:287.\n"
)
BARE_LOG = "```\nTraceback (most recent call last):\nIndexError: list index out of range\n```"
NOTES = "\n## Notes\n\nThe crash is in app/routers/consult.py, line 3.\n"
LEADING_TYPES = {
    "first_line": ("TYPE: docs\nUpdate the README with the new --from flag.\n", "docs"),
    "blank_after": ("TYPE: chore\n\nBump the fastapi pin in pyproject.toml.\n", "chore"),
    "after_title": ("# Update the README\n\nTYPE: docs\n\nMention the new --from flag.\n", "docs"),
    "spanish": ("Tipo: tarea\nAñadir el badge del carrito.\n", "tarea"),
    "setext_title": ("Update the README\n=================\nTYPE: docs\n\nMore text.\n", "docs"),
}


def template_copy(request_lines: str, after: str = "") -> str:
    template = VENDORED.read_text(encoding="utf-8")
    marker = template.index("=== REQUEST ===")
    closing = template.index("```", marker)
    return template[:marker] + "=== REQUEST ===\n\n" + request_lines + template[closing:] + after


def unfenced_copy(request_lines: str, above: str = "") -> str:
    template = VENDORED.read_text(encoding="utf-8")
    marker = template.index("=== REQUEST ===")
    inner = template[template.index("\n", template.rindex("```", 0, marker)) + 1 : marker]
    return above + inner + "=== REQUEST ===\n\n" + request_lines


def test_an_unlabelled_file_is_its_first_heading_and_the_whole_rest_as_evidence() -> None:
    text = phased_mandate()
    parsed = parse_mandate_file(text)
    assert not parsed.labelled
    assert parsed.labelled_fields == ()
    request = parsed.request
    assert request.what == REAL_TITLE
    assert request.why == text.partition("\n")[2].strip()
    assert len(request.why) > 11_000
    assert request.type == ""
    assert (request.where, request.constraints, request.tests, request.out_of_scope) == (
        "",
        "",
        "",
        "",
    )


def test_without_a_heading_the_first_non_empty_line_is_the_what() -> None:
    parsed = parse_mandate_file("\n\n  Arregla el total del carrito\nEl total suma dos veces.\n")
    assert parsed.request.what == "Arregla el total del carrito"
    assert parsed.request.why == "El total suma dos veces."


def test_the_first_heading_wins_and_the_text_before_it_stays_in_the_evidence() -> None:
    text = "Nota para el equipo.\n\n## Refactor del intérprete ##\n\nDetalle.\n"
    request = parse_mandate_file(text).request
    assert request.what == "Refactor del intérprete"
    assert request.why == "Nota para el equipo.\n\n\nDetalle."


@pytest.mark.parametrize(
    ("text", "what"),
    [
        (PLAIN_TITLE, "Refactor del intérprete de consultas y del endpoint"),
        ("Arregla el carrito\n\n### Paso 1\n\nHaz x.\n", "Arregla el carrito"),
        ("Nota.\n\n## Título\n\nTexto.\n\n# Otro\n", "Nota."),
        ("Nota.\n\n# Título\n\n## Sección A\n\n## Sección B\n", "Título"),
        ("Nota.\n\nRefactor\n========\n\nDetalle.\n", "Refactor"),
    ],
    ids=["phases", "numbered_step", "higher_heading", "lone_top_heading", "setext"],
)
def test_a_heading_after_a_plain_first_line_is_the_title_only_when_it_stands_alone(
    text: str, what: str
) -> None:
    request = parse_mandate_file(text).request
    assert request.what == what
    lines = [line for line in text.splitlines() if line.strip()]
    kept = [line for line in lines if what not in line and set(line) != {"="}]
    assert [line for line in request.why.splitlines() if line.strip()] == kept


def test_a_plain_first_line_stays_the_title_of_a_sectioned_mandate() -> None:
    title = "Refactoriza el intérprete para que el endpoint responda todas las intenciones."
    text = title + "\n" + phased_mandate().partition("\n")[2]
    request = parse_mandate_file(text).request
    assert request.what == title
    assert request.why.startswith("## Contexto")


@pytest.mark.parametrize("underline", ["===================", "---"])
def test_a_setext_title_drops_its_underline(underline: str) -> None:
    request = parse_mandate_file(f"Arreglar el carrito\n{underline}\n\nCuerpo del mandato.").request
    assert (request.what, request.why) == ("Arreglar el carrito", "Cuerpo del mandato.")


def test_headings_inside_code_fences_are_not_titles() -> None:
    text = "```\n# not a title\n```\nFix the parser\n"
    assert title_and_rest(text) == ("Fix the parser", "```\n# not a title\n```")
    assert title_and_rest("") == ("", "")


@pytest.mark.parametrize(
    "text",
    [
        "# Title\n\n````\n```\nTYPE: bug\n```\nWHAT: hidden\n````\n\nBody.\n",
        "# Title\n\n~~~\n```\nWHAT: hidden\n~~~\n\nBody.\n",
        "# Title\n\n```\n~~~\nWHAT: hidden\n```\n\nBody.\n",
        "# Title\n\n```python\nx = 1\n```python\nWHAT: hidden\n```\n\nBody.\n",
    ],
    ids=["four_backticks", "tildes", "backticks", "info_string_never_closes"],
)
def test_a_fence_closes_only_with_its_own_character_and_at_least_its_length(text: str) -> None:
    parsed = parse_mandate_file(text)
    assert not parsed.labelled
    assert parsed.request == MandateRequest(what="Title", why=text.partition("\n")[2].strip())


def test_a_filled_request_block_splits_back_into_its_fields() -> None:
    request = MandateRequest(
        type="bug",
        what="fix totals",
        why="AssertionError: 30 != 31\n  at cart.py:41\n\nsecond paragraph",
        where="src/cart.py",
        constraints="keep the API",
        tests="tests/test_cart.py",
        out_of_scope="billing",
    )
    text = "```\n" + fill_request(SIMPLE_BLOCK, request) + "```\n"
    parsed = parse_mandate_file(text)
    assert parsed.labelled
    assert parsed.labelled_fields == EVERY_FIELD
    assert parsed.request == request


def test_an_indented_paragraph_continues_a_value_the_way_fill_request_writes_it() -> None:
    request = MandateRequest(
        type="refactor",
        what="split the interpreter\n\ninto two passes",
        why="the log",
        where="app/services/interpreter.py",
        constraints="keep the public API\n\nand the CLI flags",
        tests="tests/test_interpreter.py",
        out_of_scope="billing\n\nand payments",
    )
    text = "```\n" + fill_request(SIMPLE_BLOCK, request) + "```\n"
    assert parse_mandate_file(text).request == request


def test_spanish_and_title_case_labels_split_into_fields() -> None:
    parsed = parse_mandate_file(LABELLED_ES)
    assert parsed.labelled_fields == EVERY_FIELD
    assert parsed.request == MandateRequest(
        type="feature",
        what="un botón de WhatsApp en cada producto",
        why=(
            "# Botón de WhatsApp\n\nlos clientes lo piden en la encuesta.\n"
            "Llega desde el formulario de contacto."
        ),
        where="src/components/ProductCard.tsx",
        constraints="sin dependencias nuevas",
        tests="npm run build en verde",
        out_of_scope="el checkout",
    )


@pytest.mark.parametrize(
    ("line", "field"),
    [
        ("TYPE: bug", "type"),
        ("WHY / EVIDENCE: boom", "why"),
        ("WHY/EVIDENCE: boom", "why"),
        ("EVIDENCE: boom", "why"),
        ("POR QUÉ / EVIDENCIA: boom", "why"),
        ("por que: boom", "why"),
        ("Evidencias: boom", "why"),
        ("EXPECTED TESTS: boom", "tests"),
        ("PRUEBAS ESPERADAS: boom", "tests"),
        ("Prueba: boom", "tests"),
        ("Test: boom", "tests"),
        ("Restricción: boom", "constraints"),
        ("RESTRICCION: boom", "constraints"),
        ("- **OUT OF SCOPE**: boom", "out_of_scope"),
        ("## FUERA DE ALCANCE: boom", "out_of_scope"),
        ("FUERA DEL ALCANCE: boom", "out_of_scope"),
        ("DONDE: boom", "where"),
        ("QUE: boom", "what"),
    ],
)
def test_every_label_spelling_names_its_field(line: str, field: str) -> None:
    request = parse_mandate_file(f"WHAT: the change\n{line}\n").request
    expected = "bug" if field == "type" else "boom"
    if field == "what":
        assert request.what == "the change\n\nboom"
    else:
        assert getattr(request, field) == expected


def test_fuera_del_alcance_is_out_of_scope_wherever_it_follows() -> None:
    labels = (
        "TIPO: error",
        "QUÉ: arreglar el acceso a result[0] en app/routers/consult.py",
        "POR QUÉ: IndexError al recibir una lista vacía",
        "FUERA DEL ALCANCE: app/services/interpreter.py",
    )
    for order in (labels, (*labels[:2], labels[3], labels[2])):
        request = parse_mandate_file("\n".join(order) + "\n").request
        assert request.what == "arreglar el acceso a result[0] en app/routers/consult.py"
        assert request.why == "IndexError al recibir una lista vacía"
        assert request.out_of_scope == "app/services/interpreter.py"


def test_label_words_without_a_colon_or_inside_fences_stay_text() -> None:
    text = "# Title\n\nPruebas en verde.\nTipo de cambio: mayor\n```\nTYPE: bug\n```\n"
    parsed = parse_mandate_file(text)
    assert not parsed.labelled
    assert parsed.request.type == ""
    assert "TYPE: bug" in parsed.request.why


@pytest.mark.parametrize("text", list(PROSE_FILES.values()), ids=list(PROSE_FILES))
def test_label_words_in_prose_without_a_type_what_or_why_label_stay_text(text: str) -> None:
    parsed = parse_mandate_file(text)
    title, _, rest = text.partition("\n")
    assert not parsed.labelled
    assert parsed.labelled_fields == ()
    assert parsed.request == MandateRequest(what=title.removeprefix("# "), why=rest.strip())


@pytest.mark.parametrize(
    "trigger",
    [
        "Tipo: error",
        "TYPE: feature | bug | refactor | investigation",
        "Qué: arreglar el total",
        "Why: the total doubles",
        "EVIDENCIA: el log",
        "Por qué: falla",
    ],
)
def test_a_type_what_or_why_label_makes_every_other_label_count(trigger: str) -> None:
    parsed = parse_mandate_file(f"Arreglar el total\n{trigger}\nFuera de alcance: los pagos\n")
    assert parsed.labelled
    assert parsed.request.out_of_scope == "los pagos"


def test_an_unknown_type_in_a_labelled_file_is_kept_for_the_refusal() -> None:
    parsed = parse_mandate_file("WHAT: freeze the answer type\nType: chore\n")
    assert parsed.request.type == "chore"
    assert parsed.labelled_fields == ("type", "what")


@pytest.mark.parametrize(("text", "kind"), list(LEADING_TYPES.values()), ids=list(LEADING_TYPES))
def test_a_type_line_that_opens_the_file_is_its_type_whatever_the_value(
    text: str, kind: str
) -> None:
    parsed = parse_mandate_file(text)
    assert parsed.labelled
    assert parsed.request.type == kind
    assert "type" in parsed.labelled_fields
    assert not parsed.request.what.lower().startswith(("type", "tipo"))
    assert not parsed.request.why.lower().startswith(("type", "tipo"))


def test_a_type_line_later_in_the_file_stays_text() -> None:
    parsed = parse_mandate_file("Update the README\n\nSome context.\nType: docs\n")
    assert not parsed.labelled
    assert parsed.request.why == "Some context.\nType: docs"


def test_a_type_line_after_a_title_counts_only_when_it_is_written_as_a_label() -> None:
    text = (
        "# Add a timeout option\n\nType: integer\nDefault: 30\n"
        "The option sets the request timeout of POST /consult.\n"
    )
    parsed = parse_mandate_file(text)
    assert not parsed.labelled
    assert parsed.request.what == "Add a timeout option"
    assert parsed.request.why.startswith("Type: integer\nDefault: 30")


def test_a_labelled_value_ends_at_a_blank_line_and_the_rest_joins_the_evidence() -> None:
    text = (
        "TYPE: bug\nWHAT: the consult endpoint crashes\n\nIt crashes on an empty list.\n"
        "OUT OF SCOPE: billing\n\nThe crash is in app/routers/consult.py, line 3.\n"
    )
    request = parse_mandate_file(text).request
    assert request.what == "the consult endpoint crashes"
    assert request.out_of_scope == "billing"
    assert request.why == (
        "It crashes on an empty list.\n\nThe crash is in app/routers/consult.py, line 3."
    )


def test_a_field_ends_at_a_heading_and_the_rest_joins_the_evidence() -> None:
    text = (
        "WHAT: refactor\nOUT OF SCOPE: the frontend\nand the mobile app\n\n"
        "## Phase 1\nTouch src/app.py.\nWHY: because\n"
    )
    request = parse_mandate_file(text).request
    assert request.out_of_scope == "the frontend\nand the mobile app"
    assert request.why == "## Phase 1\nTouch src/app.py.\n\nbecause"


def test_a_labelled_file_without_what_takes_it_from_the_text_around_the_labels() -> None:
    request = parse_mandate_file("# Arreglar el botón\n\nTIPO: error\nDetalle.\n").request
    assert (request.type, request.what) == ("bug", "Arreglar el botón")
    assert request.why == "Detalle."


def test_only_the_stated_fields_are_named_and_placeholders_are_not() -> None:
    text = (
        "Qué: el total\nTIPO: feature | bug | refactor | investigation\n"
        "Fuera de alcance: <what must NOT be touched — always fill this>\n"
    )
    parsed = parse_mandate_file(text)
    assert parsed.labelled_fields == ("what",)
    assert parsed.named
    assert not parse_mandate_file("Fix the login\nWhy: users cannot sign in").named


def test_a_template_copy_keeps_only_its_request_block() -> None:
    text = (
        "# Mandate template\n\nCopy this whole file.\n\n---\n\n```\n# MANDATE\n\n"
        "Execution contract.\n\n=== REQUEST ===\n\n"
        "TYPE:            feature | bug | refactor | investigation\n"
        "WHAT:            <the change, concretely>\n"
        "WHY / EVIDENCE:  the log\n```\n"
    )
    request = parse_mandate_file(text).request
    assert (request.type, request.what, request.why) == ("", "", "the log")


def test_a_filled_copy_of_the_vendored_template_is_its_request_block() -> None:
    parsed = parse_mandate_file(template_copy(FILLED_REQUEST))
    assert parsed.request == CONSULT_BUG
    assert parsed.labelled_fields == ("type", "what", "why", "where", "out_of_scope")


def test_notes_after_a_template_copy_join_the_evidence_and_guard_nothing() -> None:
    notes = (
        "\n## Notes\n\n"
        "The crash is in app/routers/consult.py, line 3, when the interpreter returns nothing.\n"
    )
    request = parse_mandate_file(template_copy(FILLED_REQUEST, notes)).request
    assert request.out_of_scope == "billing"
    assert request.why == (
        "IndexError: list index out of range\n\n## Notes\n\n"
        "The crash is in app/routers/consult.py, line 3, when the interpreter returns nothing."
    )
    assert request.where == "app/routers/consult.py"


def test_a_log_fenced_with_its_language_inside_a_template_copy_keeps_the_labels_after_it() -> None:
    log = "```text\nTraceback (most recent call last):\nIndexError: list index out of range\n```"
    lines = FILLED_REQUEST.replace("IndexError: list index out of range\n", f"\n{log}\n")
    request = parse_mandate_file(template_copy(lines)).request
    assert request.why == log
    assert (request.where, request.out_of_scope) == ("app/routers/consult.py", "billing")


def test_a_bare_fenced_log_inside_a_template_copy_keeps_the_labels_after_it() -> None:
    lines = FILLED_REQUEST.replace("IndexError: list index out of range\n", f"\n{BARE_LOG}\n")
    parsed = parse_mandate_file(template_copy(lines))
    assert parsed.request.why == BARE_LOG
    assert (parsed.request.where, parsed.request.out_of_scope) == (
        "app/routers/consult.py",
        "billing",
    )
    assert parsed.labelled_fields == ("type", "what", "why", "where", "out_of_scope")


def test_a_bare_fenced_log_and_notes_after_a_template_copy_keep_the_labels_and_the_notes() -> None:
    lines = FILLED_REQUEST.replace("IndexError: list index out of range\n", f"\n{BARE_LOG}\n")
    request = parse_mandate_file(template_copy(lines, NOTES)).request
    assert (request.where, request.out_of_scope) == ("app/routers/consult.py", "billing")
    assert request.why == BARE_LOG + "\n\n" + NOTES.strip()


def test_notes_with_a_label_and_a_fenced_log_after_a_template_copy_join_the_evidence() -> None:
    notes = (
        "\n## Notes\n\nWhy: it broke after the upgrade. The traceback:\n\n```\n"
        '  File "app/routers/consult.py", line 3, in consult\n'
        "IndexError: list index out of range\n```\n"
    )
    request = parse_mandate_file(template_copy(FILLED_REQUEST, notes)).request
    assert (request.where, request.out_of_scope) == ("app/routers/consult.py", "billing")
    assert "## Notes" in request.why
    assert "Why: it broke after the upgrade." in request.why


def test_text_above_an_unfenced_request_block_joins_the_evidence() -> None:
    context = (
        "# Bug del carrito\n\nEl total suma dos veces cuando hay descuento; ver app/cart.py:41."
    )
    text = (
        f"{context}\n\n=== REQUEST ===\n\nTYPE: bug\nWHAT: arreglar el total\nOUT OF SCOPE: pagos\n"
    )
    assert parse_mandate_file(text).request == MandateRequest(
        type="bug", what="arreglar el total", why=context, out_of_scope="pagos"
    )


def test_a_template_copied_without_its_fences_is_its_request_block() -> None:
    parsed = parse_mandate_file(unfenced_copy(FILLED_REQUEST))
    assert parsed.request == CONSULT_BUG
    assert parsed.labelled_fields == ("type", "what", "why", "where", "out_of_scope")


def test_notes_above_a_template_copied_without_its_fences_join_the_evidence() -> None:
    notes = "The crash started after the 2.3 deploy.\n\n"
    request = parse_mandate_file(unfenced_copy(FILLED_REQUEST, notes)).request
    assert request.why == "The crash started after the 2.3 deploy.\n\n" + CONSULT_BUG.why


def test_a_copied_request_block_drops_its_dangling_fence_and_keeps_the_text_above() -> None:
    text = (
        "Our template looks like this:\n\n=== REQUEST ===\n\n"
        "and then nothing labelled follows here.\n\n"
        "TYPE:            investigation\n"
        "WHAT:            explain the template marker\n"
        "WHY / EVIDENCE:  the log\n```\n"
    )
    request = parse_mandate_file(text).request
    assert (request.type, request.what) == ("investigation", "explain the template marker")
    assert request.why == (
        "Our template looks like this:\n\nand then nothing labelled follows here.\n\nthe log"
    )


@pytest.mark.parametrize("path", [VENDORED, FORGE / "README.md"], ids=["template", "readme"])
def test_every_placeholder_of_a_vendored_request_block_counts_as_not_stated(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    block = text[text.index("=== REQUEST ===") :]
    parsed = parse_mandate_file(block[: block.index("```")])
    assert parsed.labelled
    assert parsed.labelled_fields in ((), ("type",))
    assert (parsed.request.what, parsed.request.why, parsed.request.out_of_scope) == ("", "", "")


def test_a_value_in_angle_brackets_is_stated_unless_it_is_a_template_placeholder() -> None:
    text = (
        "TYPE: bug\nWHAT: <CartBadge /> shows 0 after a reload\n"
        "WHERE: <src/components/CartBadge.tsx>\nOUT OF SCOPE: <Checkout />\n"
    )
    request = parse_mandate_file(text).request
    assert request.what == "<CartBadge /> shows 0 after a reload"
    assert (request.where, request.out_of_scope) == (
        "<src/components/CartBadge.tsx>",
        "<Checkout />",
    )


def test_windows_line_endings_and_a_byte_order_mark_are_read() -> None:
    request = parse_mandate_file("﻿# Título\r\nCuerpo\r\n").request
    assert (request.what, request.why) == ("Título", "Cuerpo")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("feature", "feature"),
        ("Bug.", "bug"),
        ("investigación", "investigation"),
        ("Auditoría", "investigation"),
        ("refactorización", "refactor"),
        ("corrección urgente", "bug"),
        ("Nueva funcionalidad", "feature"),
        ("nueva característica", "feature"),
        ("Mejora", "feature"),
        ("Refactorizar", "refactor"),
        ("refactoring", "refactor"),
        ("Investigar", "investigation"),
        ("auditar el módulo", "investigation"),
        ("arreglar el total", "bug"),
        ("Arreglo urgente", "bug"),
        ("feature | bug | refactor | investigation", ""),
        ("bug | refactor", ""),
        ("<type>", "<type>"),
        ("the `answer` type must stay frozen", "the `answer` type must stay frozen"),
        ("chore", "chore"),
        ("", ""),
    ],
)
def test_type_values_in_english_and_spanish(value: str, expected: str) -> None:
    assert mandate_type(value) == expected


def test_a_spanish_type_phrase_in_a_file_is_its_type() -> None:
    request = parse_mandate_file("TIPO: Nueva funcionalidad\nQUÉ: añadir el badge\n").request
    assert (request.type, request.what) == ("feature", "añadir el badge")


def test_given_fields_override_the_file_and_empty_ones_keep_it() -> None:
    parsed = MandateRequest("feature", "from file", "evidence", "src", "c", "t", "o")
    given = MandateRequest(type="bug", tests="pytest", out_of_scope=" ")
    assert with_overrides(parsed, given) == MandateRequest(
        "bug", "from file", "evidence", "src", "c", "pytest", "o"
    )


def test_a_fifty_kilobyte_file_is_never_cut() -> None:
    body = "\n".join(f"línea {index} con `src/app.py:{index}`" for index in range(1_800))
    text = f"# Mandato grande\n\n{body}\n"
    assert len(text.encode("utf-8")) > 50_000
    request = parse_mandate_file(text).request
    assert request.what == "Mandato grande"
    assert request.why == body


ENCODED = {
    "utf8": SPANISH_TEXT.encode("utf-8"),
    "utf8_bom": codecs.BOM_UTF8 + SPANISH_TEXT.encode("utf-8"),
    "utf16_le": codecs.BOM_UTF16_LE + SPANISH_TEXT.encode("utf-16-le"),
    "utf16_be": codecs.BOM_UTF16_BE + SPANISH_TEXT.encode("utf-16-be"),
    "utf32_le": codecs.BOM_UTF32_LE + SPANISH_TEXT.encode("utf-32-le"),
    "utf32_be": codecs.BOM_UTF32_BE + SPANISH_TEXT.encode("utf-32-be"),
}


@pytest.mark.parametrize("data", list(ENCODED.values()), ids=list(ENCODED))
def test_utf8_and_text_with_a_byte_order_mark_decode_to_the_same_text(data: bytes) -> None:
    assert decoded_text(data, Path("mandato.md")) == SPANISH_TEXT


@pytest.mark.parametrize(
    "data",
    [
        SPANISH_TEXT.encode("cp1252"),
        SPANISH_TEXT.encode("utf-16-le"),
        "TYPE: bug\n".encode("utf-16-le"),
        b"WHAT: x\x00\n",
    ],
    ids=["ansi", "utf16_without_bom", "ascii_utf16_without_bom", "nul"],
)
def test_bytes_that_are_not_utf8_text_are_refused_with_a_hint(data: bytes) -> None:
    with pytest.raises(NotText) as caught:
        decoded_text(data, "docs/mandato.md")
    assert isinstance(caught.value, DomainFailure)
    assert caught.value.path == "docs/mandato.md"
    assert caught.value.message == "docs/mandato.md is not UTF-8 text"
    assert caught.value.hint == "save it as UTF-8"


def test_only_type_and_what_are_essential_and_the_rest_defaults_to_none_stated() -> None:
    assert ESSENTIAL_FIELDS == ("type", "what")
    assert missing_fields(MandateRequest(), ESSENTIAL_FIELDS) == ("type", "what")
    assert missing_fields(MandateRequest("feature", "w"), ESSENTIAL_FIELDS) == ()
    assert missing_fields(MandateRequest("feature", "w")) == ("tests", "out_of_scope")
    feature = with_defaults(MandateRequest("feature", "w"))
    assert (feature.why, feature.tests, feature.out_of_scope) == (NONE_STATED,) * 3
    assert NONE_STATED == "none stated"
    assert with_defaults(MandateRequest("refactor", "w")).tests == NONE_STATED
    assert with_defaults(MandateRequest("bug", "w")).tests == NONE_STATED
    pasted = with_defaults(MandateRequest("bug", "w", "Traceback"))
    assert pasted.tests == "regression fixture for the pasted error"
    report = with_defaults(MandateRequest("investigation", "w"))
    assert report.tests == "Deliverable: a written report"


def test_the_command_line_what_and_why_never_drop_the_file_text() -> None:
    parsed = MandateRequest("feature", "Título del archivo", "Cuerpo del mandato.")
    given = MandateRequest(what="Otro qué", why="Una nota corta")
    merged = with_overrides(parsed, given)
    assert merged.what == "Otro qué"
    assert merged.why == (
        "Título del archivo\n\nCuerpo del mandato.\n\nAdded on the command line:\nUna nota corta"
    )
    assert with_overrides(MandateRequest("bug", "w"), MandateRequest(why="nota")).why == "nota"
    same = with_overrides(parsed, MandateRequest(what="Título del archivo"))
    assert same.why == "Cuerpo del mandato."
