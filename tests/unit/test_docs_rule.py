from __future__ import annotations

import time
from dataclasses import replace

import pytest

from cuanta.domain.agents import ROLE_NAMES
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import english
from cuanta.domain.routing import Role
from cuanta.domain.scout import (
    DOCS_AGENT,
    TERM_LIMIT,
    DocsChoice,
    DocsMode,
    DocsReason,
    asks_for_docs,
    docs_choice,
    docs_refusal,
    docs_setting,
)
from tests.real_run import phased_mandate

REAL_RUN = MandateRequest(
    type="feature",
    what="Ejecuta el mandato por fases",
    why=phased_mandate(),
    tests="pytest en verde",
    out_of_scope="docs del cliente",
)
PLAIN = MandateRequest("feature", "Fix the rounding of totals", "Totals are off", out_of_scope="z")
AGENT_LINE = "Docs: on, the request names the docs-updater agent in the evidence"


def test_the_docs_rule_reads_the_evidence_of_a_long_mandate() -> None:
    assert asks_for_docs(REAL_RUN)
    choice = docs_choice(DocsMode.AUTO, REAL_RUN, False)
    assert choice == DocsChoice(True, DocsReason.AGENT, "why", "docs-updater")
    assert english(choice.message) == AGENT_LINE
    blind = replace(REAL_RUN, why="")
    assert docs_choice(DocsMode.AUTO, blind, False) == DocsChoice(False, DocsReason.NOT_REQUESTED)
    assert ROLE_NAMES[DOCS_AGENT] is Role.DOCS


@pytest.mark.parametrize(
    ("asked", "rule", "field", "term"),
    [
        (
            MandateRequest("feature", "x", "Fase 4: escribe la auditoría en docs/audits/x.md."),
            DocsReason.PATH,
            "why",
            "docs/audits/x.md",
        ),
        (
            MandateRequest("feature", "x", "## Fase 5 — Documentación del endpoint"),
            DocsReason.REQUESTED,
            "why",
            "Documentación",
        ),
        (
            MandateRequest("feature", "x", "Fase 5: Documentación"),
            DocsReason.REQUESTED,
            "why",
            "Documentación",
        ),
        (
            MandateRequest("feature", "x", "y", "docs/setup.md"),
            DocsReason.PATH,
            "where",
            "docs/setup.md",
        ),
        (
            MandateRequest("feature", "x", "y", "packages/web/README.md"),
            DocsReason.PATH,
            "where",
            "packages/web/README.md",
        ),
        (
            MandateRequest("feature", "x", "y", tests="CHANGELOG.md lists the flag"),
            DocsReason.PATH,
            "tests",
            "CHANGELOG.md",
        ),
        (
            MandateRequest("feature", "Update the README with the new flag", "y"),
            DocsReason.REQUESTED,
            "what",
            "README",
        ),
        (
            MandateRequest("feature", "Add a CHANGELOG entry", "Luego el docs-updater cierra"),
            DocsReason.REQUESTED,
            "what",
            "CHANGELOG",
        ),
        (
            MandateRequest(
                "feature", "x", "Ver docs/guide.md primero\nAl final, el `docs-updater`."
            ),
            DocsReason.AGENT,
            "why",
            "docs-updater",
        ),
    ],
)
def test_each_docs_rule_reports_its_term_and_field(
    asked: MandateRequest, rule: DocsReason, field: str, term: str
) -> None:
    assert docs_choice(DocsMode.AUTO, asked, False) == DocsChoice(True, rule, field, term)


def test_the_term_is_shown_as_written_and_capped() -> None:
    long = MandateRequest("feature", "x", "y", "docs/" + "a" * 80 + ".md")
    choice = docs_choice(DocsMode.AUTO, long, False)
    assert (choice.reason, choice.field) == (DocsReason.PATH, "where")
    assert len(choice.term) == TERM_LIMIT and choice.term.endswith("…")
    assert choice.term.startswith("docs/aaa")
    shown = english(docs_choice(DocsMode.AUTO, REAL_RUN, False, flag=True).message)
    assert shown == AGENT_LINE
    words = docs_choice(DocsMode.AUTO, MandateRequest("feature", "Escribe la Guía", "y"), False)
    assert english(words.message) == "Docs: on, the request asks for docs in the description (Guía)"
    path = docs_choice(DocsMode.AUTO, MandateRequest("feature", "x", "y", "doc/api.md"), False)
    assert (
        english(path.message)
        == "Docs: on, the request names the docs path doc/api.md in the location"
    )


@pytest.mark.parametrize(
    "asked",
    [
        replace(PLAIN, out_of_scope="docs/, README.md and the docs-updater"),
        replace(PLAIN, constraints="No comments / no docstrings"),
        replace(PLAIN, why="Ver https://docs.python.org/3/library/re.html"),
        replace(
            PLAIN, why="-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html"
        ),
        replace(PLAIN, why="See www.docs.example.invalid/setup for the error"),
        replace(PLAIN, what="Use document.querySelector in pydoc/x.py"),
        replace(PLAIN, where="src/docs_updater.py and src/readme_parser.py"),
    ],
)
def test_negative_fields_and_links_never_turn_docs_on(asked: MandateRequest) -> None:
    assert not asks_for_docs(asked)
    assert docs_choice(DocsMode.AUTO, asked, False) == DocsChoice(False, DocsReason.NOT_REQUESTED)


def test_the_docs_option_wins_over_runs_docs_and_names_itself() -> None:
    assert docs_setting("", DocsMode.OFF) == (DocsMode.OFF, False)
    assert docs_setting("on", DocsMode.OFF) == (DocsMode.ON, True)
    assert docs_setting("auto", DocsMode.ON) == (DocsMode.AUTO, True)
    off = docs_choice(DocsMode.OFF, REAL_RUN, False, pinned=True, flag=True)
    assert off == DocsChoice(False, DocsReason.FLAG_OFF)
    assert docs_choice(DocsMode.ON, PLAIN, True, flag=True) == DocsChoice(True, DocsReason.FLAG_ON)
    assert docs_choice(DocsMode.ON, PLAIN, False).reason is DocsReason.FORCED_ON
    assert docs_choice(DocsMode.AUTO, PLAIN, True, flag=True).reason is DocsReason.TRIAL
    assert english(DocsChoice(True, DocsReason.FLAG_ON).message) == "Docs: on (--docs on)"
    assert english(DocsChoice(False, DocsReason.FLAG_OFF).message) == "Docs: off (--docs off)"
    assert english(DocsChoice(True, DocsReason.AGENT).message) == (
        "Docs: on, the request names the docs agent"
    )


@pytest.mark.parametrize(
    ("docs", "task_type", "flags", "refused"),
    [
        ("off", "feature", {"classic": True}, "--classic runs with docs on"),
        ("auto", "bug", {"classic": True}, "--classic runs with docs on"),
        ("off", "feature", {"pinned": True}, "--docs off and a docs pin disagree"),
        ("on", "feature", {"simple": True}, "simple mode runs without the docs role"),
        ("on", "investigation", {}, "investigations run without the docs role"),
        ("on", "bug", {"fast": True}, "fast implementation runs one writer without the docs role"),
        ("on", "feature", {"classic": True, "pinned": True}, ""),
        ("auto", "feature", {"fast": True, "pinned": True}, ""),
        ("off", "investigation", {"simple": True, "fast": True}, ""),
        ("", "feature", {"classic": True, "pinned": True, "fast": True}, ""),
    ],
)
def test_contradicting_docs_choices_are_refused(
    docs: str, task_type: str, flags: dict[str, bool], refused: str
) -> None:
    found = docs_refusal(docs, task_type, **flags)
    if not refused:
        assert found is None
        return
    assert found is not None
    message, hint = found
    assert english(message) == refused and hint


PYTEST_WARNING = (
    "================= warnings summary =================\n"
    "app/models.py:12\n"
    "  /srv/proj/.venv/lib/python3.12/site-packages/pydantic/_internal/_config.py:323: "
    "PydanticDeprecatedSince20: Support for class-based `config` is deprecated. See Pydantic V2 "
    "Migration Guide at https://errors.pydantic.dev/2.11/migration/\n"
    "    warnings.warn(DEPRECATION_MESSAGE, DeprecationWarning)\n"
    "-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html\n"
    "FAILED tests/test_models.py::test_config - AssertionError: docs\n"
)


@pytest.mark.parametrize(
    "asked",
    [
        replace(PLAIN, why=PYTEST_WARNING),
        replace(PLAIN, why="Usa como guía el módulo de pagos"),
        replace(PLAIN, why="No actualices la documentación ni el CHANGELOG en esta entrega."),
        replace(PLAIN, why="error: Module 'app.docs' has no attribute 'render'"),
        replace(PLAIN, why='File "/srv/app/docs/conf.py", line 3'),
        replace(PLAIN, why='  File "/srv/agents/docs-updater/run.py", line 3, in <module>'),
        replace(PLAIN, why="Traceback (most recent call last): docs/conf.py"),
        replace(PLAIN, why="    at render (docs/site/app.js:12:5)"),
        replace(PLAIN, why="E       AssertionError: README.md is stale"),
        replace(PLAIN, tests="pytest tests/doc/test_parser.py"),
        replace(PLAIN, tests="pytest tests/docs/test_parser.py"),
        replace(PLAIN, where="packages/web/docs/api.md"),
        replace(PLAIN, what="Fix the parser; don't update the docs"),
        replace(PLAIN, what="Corrige el parser, sin tocar la documentación"),
        replace(PLAIN, why="No invoques al docs-updater en este cambio."),
        replace(PLAIN, why="Do not touch anything under docs/ for this fix."),
        replace(PLAIN, why="Out of scope: docs/ and the README."),
    ],
)
def test_logs_negations_and_prose_in_the_evidence_never_turn_docs_on(
    asked: MandateRequest,
) -> None:
    assert docs_choice(DocsMode.AUTO, asked, False) == DocsChoice(False, DocsReason.NOT_REQUESTED)


@pytest.mark.parametrize(
    ("asked", "rule", "field", "term"),
    [
        (
            replace(PLAIN, why="**Documentación**\nTodo el detalle."),
            DocsReason.REQUESTED,
            "why",
            "Documentación",
        ),
        (replace(PLAIN, why="- Paso 3: actualizar la guía"), DocsReason.REQUESTED, "why", "guía"),
        (replace(PLAIN, why="### Docs"), DocsReason.REQUESTED, "why", "Docs"),
        (
            replace(PLAIN, why="Si no existe docs/setup.md, créalo."),
            DocsReason.PATH,
            "why",
            "docs/setup.md",
        ),
        (
            replace(PLAIN, why="Fix the bug, then have the docs-updater close"),
            DocsReason.AGENT,
            "why",
            "docs-updater",
        ),
        (
            replace(PLAIN, what="No olvides actualizar el CHANGELOG"),
            DocsReason.REQUESTED,
            "what",
            "CHANGELOG",
        ),
        (
            replace(PLAIN, what="No cambies la API y actualiza la documentación"),
            DocsReason.REQUESTED,
            "what",
            "documentación",
        ),
        (replace(PLAIN, where="./docs/setup.md"), DocsReason.PATH, "where", "./docs/setup.md"),
        (
            replace(PLAIN, what="Update guide.md with the new flag"),
            DocsReason.REQUESTED,
            "what",
            "guide",
        ),
    ],
)
def test_headings_paths_and_affirmative_requests_still_turn_docs_on(
    asked: MandateRequest, rule: DocsReason, field: str, term: str
) -> None:
    assert docs_choice(DocsMode.AUTO, asked, False) == DocsChoice(True, rule, field, term)


@pytest.mark.parametrize(
    ("what", "term"),
    [
        ("Ensure no regression and update the README", "README"),
        ("The project has no README yet; add one with setup steps", "README"),
        ("There is no documentation for the export endpoint yet; write it", "documentation"),
        ("Add docs for the endpoint that has no auth", "docs"),
        ("Don't change the API and update the docs", "docs"),
        ("Do not add new endpoints, only update the guide", "guide"),
        ("Don't forget to update the CHANGELOG", "CHANGELOG"),
        ("No hay guía del endpoint; crea una", "guía"),
        ("El proyecto no tiene README; crea uno con los pasos", "README"),
        ("Corrige el cálculo sin romper nada y actualiza el CHANGELOG", "CHANGELOG"),
        ("No hemos visto la documentación nueva, revísala", "documentación"),
        ("Sin dependencias nuevas y actualiza la guía", "guía"),
    ],
)
def test_a_negation_that_does_not_refuse_the_docs_leaves_them_on(what: str, term: str) -> None:
    asked = replace(PLAIN, what=what)
    assert docs_choice(DocsMode.AUTO, asked, False) == DocsChoice(
        True, DocsReason.REQUESTED, "what", term
    )


@pytest.mark.parametrize(
    "what",
    [
        "Fix the crash without touching the README",
        "Ship the fix without docs",
        "Ship the fix without any new docs",
        "Entrega el arreglo sin más documentación",
        "Do not modify or touch the docs",
        "Never update the CHANGELOG for this fix",
        "There is no need to update the docs",
        "Don't make changes to the guide",
        "Corrige el error sin documentación",
        "No modifiques ni toques el README",
        "No hace falta actualizar la guía",
        "Nunca toques la documentación",
        "No se debe tocar el CHANGELOG",
        "No agregues nada al README",
    ],
)
def test_a_negated_change_or_a_without_before_the_docs_term_keeps_them_off(what: str) -> None:
    asked = replace(PLAIN, what=what)
    assert docs_choice(DocsMode.AUTO, asked, False) == DocsChoice(False, DocsReason.NOT_REQUESTED)


@pytest.mark.parametrize(
    ("asked", "rule"),
    [
        (replace(PLAIN, why="Do not run the docs-updater for this fix."), None),
        (replace(PLAIN, why="No ejecutes el docs-updater."), None),
        (replace(PLAIN, why="The docs-updater has no access to secrets."), DocsReason.AGENT),
        (replace(PLAIN, where="docs/api.md, which has no examples"), DocsReason.PATH),
        (replace(PLAIN, where="sin tocar docs/api.md"), None),
    ],
)
def test_the_agent_and_path_rules_read_negations_the_same_way(
    asked: MandateRequest, rule: DocsReason | None
) -> None:
    found = docs_choice(DocsMode.AUTO, asked, False)
    assert found.on is (rule is not None)
    assert found.reason is (rule or DocsReason.NOT_REQUESTED)


DOCS_SCAN_BYTES = 200_000
DOCS_SCAN_BUDGET_S = 0.5


@pytest.mark.perf
@pytest.mark.parametrize(
    ("field", "unit"),
    [
        ("why", "no toques docs/x "),
        ("why", "no invoques al docs-updater "),
        ("why", "out of scope: docs/x and docs/y, "),
        ("what", "no actualices la documentación "),
        ("where", "leave docs/x alone "),
    ],
    ids=["negated-path", "negated-agent", "scope-label", "negated-word", "left-alone"],
)
def test_the_docs_rule_reads_a_200_kb_line_of_refused_mentions_within_budget(
    field: str, unit: str
) -> None:
    text = (unit * (DOCS_SCAN_BYTES // len(unit) + 1))[:DOCS_SCAN_BYTES]
    asked = replace(PLAIN, **{field: text})
    started = time.perf_counter()
    found = docs_choice(DocsMode.AUTO, asked, False)
    assert time.perf_counter() - started < DOCS_SCAN_BUDGET_S
    assert found == DocsChoice(False, DocsReason.NOT_REQUESTED)
