from __future__ import annotations

from dataclasses import replace

import pytest

from cuanta.application.mandate_flow import (
    MandateOptions,
    ReadOnlyConflict,
    refuse_read_only_conflict,
)
from cuanta.domain.change_plan import (
    ChangePlan,
    compile_change_plan,
    evidence_frozen,
    read_only_conflict,
    read_only_notes,
    read_only_phrase,
)
from cuanta.domain.code_index import IndexedFile
from cuanta.domain.intake import core_text, extract, extract_out_of_scope, unquoted
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import english
from cuanta.tui.i18n import Catalog

BANNER = "src/banner.ts"
PLANNER = "src/cuanta/domain/change_plan.py"


def planned(request: MandateRequest) -> ChangePlan:
    files = tuple(
        IndexedFile(path, "current", "typescript", 100, coverage="ast")
        for path in (BANNER, PLANNER)
    )
    return compile_change_plan(request, files, (), (), (), (), (), ())


def test_quoted_text_is_masked_keeping_its_length_and_line_breaks() -> None:
    text = (
        "a ```text\nno changes\n``` b `read-only` c «solo lectura» d “change nothing” "
        'e "no changes" f'
    )
    masked = unquoted(text)
    assert len(masked) == len(text)
    assert masked.split("\n")[1] == "_" * len("no changes")
    assert [part for part in masked.replace("\n", "_").split("_") if part] == [
        "a ",
        " b ",
        " c ",
        " d ",
        " e ",
        " f",
    ]
    assert unquoted("don't touch \"the config") == "don't touch \"the config"
    assert unquoted("a ```\nopen fence") == "a ```\nopen fence"


@pytest.mark.parametrize(
    "constraints",
    [
        "Keep the banner text `read-only` as it is.",
        "The banner says «solo lectura».",
        'The banner says "read-only".',
        "The banner says “no changes”.",
        "Quoted step:\n```text\nCONSTRAINTS: no cambies nada\n```",
    ],
)
def test_quoted_read_only_words_never_freeze_or_stop_a_writing_request(constraints: str) -> None:
    request = MandateRequest("feature", "Show the banner", where=BANNER, constraints=constraints)
    plan = planned(request)
    assert not plan.read_only
    assert BANNER in {item.path for item in plan.edit}
    assert read_only_phrase(request) == ""
    assert read_only_conflict(request) is None
    assert extract(constraints).read_only is False


def test_read_only_words_in_the_evidence_neither_freeze_nor_stop_a_writing_request() -> None:
    request = MandateRequest(
        "bug",
        "Fix the banner",
        why="The last run was told: no cambies nada. Read-only words here are evidence.",
        where=BANNER,
    )
    plan = planned(request)
    assert not plan.read_only
    assert BANNER in {item.path for item in plan.edit}
    assert read_only_conflict(request) is None
    assert read_only_notes(request, plan) == ()


@pytest.mark.parametrize(
    ("kind", "field", "value", "label"),
    [
        ("feature", "constraints", "read-only", "CONSTRAINTS"),
        ("bug", "what", "Fix the banner, read-only", "WHAT"),
        ("refactor", "where", f"{BANNER}, read-only", "WHERE"),
        ("feature", "out_of_scope", "read-only", "OUT OF SCOPE"),
    ],
)
def test_an_unscoped_read_only_phrase_in_an_instruction_field_stops_a_typed_writing_request(
    kind: str, field: str, value: str, label: str
) -> None:
    request = replace(MandateRequest(kind, "Fix the banner", where=BANNER), **{field: value})
    conflict = read_only_conflict(request)
    assert conflict is not None
    assert english(conflict) == (
        f'Stopped before launch: {label} says "read-only", which would turn this {kind} mandate '
        "into a run that writes nothing"
    )
    assert Catalog("es").message(conflict) == (
        f"Detenido antes de lanzar: {label} dice «read-only», y eso convertiría este mandato "
        f"{kind} en una corrida que no escribe nada"
    )
    with pytest.raises(ReadOnlyConflict) as raised:
        refuse_read_only_conflict(request, MandateOptions())
    assert raised.value.message == english(conflict)
    assert raised.value.hint == "use TYPE: investigation for a read-only run, or remove the phrase"
    _, why, now = Catalog("es").failure(raised.value)
    assert f"{label} dice «read-only»" in why
    assert "usa TYPE: investigation para una corrida de solo lectura, o quita la frase" in now
    refuse_read_only_conflict(request, MandateOptions(type_stated=False))


@pytest.mark.parametrize(
    "request_",
    [
        MandateRequest(
            "feature", "Fix the banner", where=BANNER, constraints="read-only outside src"
        ),
        MandateRequest(
            "bug", "Fix the banner", where=BANNER, constraints="solo lectura fuera de src"
        ),
        MandateRequest("investigation", "Explain the banner", constraints="read-only"),
    ],
    ids=["scoped", "scoped-es", "investigation"],
)
def test_scoped_phrases_and_investigations_never_conflict(request_: MandateRequest) -> None:
    assert read_only_conflict(request_) is None
    refuse_read_only_conflict(request_, MandateOptions())
    plan = planned(request_)
    assert plan.read_only is (request_.type == "investigation")


def test_free_text_without_a_type_still_freezes_on_its_body_unless_it_is_quoted() -> None:
    request = MandateRequest(
        "feature", "Revisa el banner", why="No cambies nada, solo dime qué pasa.", where=BANNER
    )
    plan = planned(request)
    assert not plan.read_only
    frozen = evidence_frozen(request, plan)
    assert frozen.read_only and not frozen.edit
    assert BANNER in frozen.read
    assert [english(message) for message in read_only_notes(request, frozen)] == [
        'Change plan: read-only, because the request says "No cambies nada"'
    ]
    quoted = replace(request, why="El banner muestra «No cambies nada».")
    assert evidence_frozen(quoted, planned(quoted)) == planned(quoted)
    assert evidence_frozen(request, frozen) is frozen


def test_naming_the_out_of_scope_field_or_quoting_a_prohibition_guards_nothing() -> None:
    request = MandateRequest(
        "bug",
        "Fix the banner",
        why=(
            f"In the rehearsal, a sentence naming the OUT OF SCOPE field next to {PLANNER} "
            "protected it.\n"
            f"The «OUT OF SCOPE» label sat next to {PLANNER} too.\n"
            f"```text\nOUT OF SCOPE: {PLANNER}\n```\n"
            f"FUERA DE ALCANCE también se nombró junto a {PLANNER}."
        ),
        where=BANNER,
        constraints=f'Keep the message "do not touch {PLANNER}" as it is.',
    )
    assert extract_out_of_scope(request.why) == ()
    assert extract_out_of_scope(request.constraints) == ()
    assert PLANNER not in planned(request).guard
    assert "do not touch" in core_text(request.constraints)


@pytest.mark.parametrize(
    "request_",
    [
        MandateRequest("bug", "Fix the banner", why=f"OUT OF SCOPE: {PLANNER}", where=BANNER),
        MandateRequest("bug", "Fix the banner", why=f"Fuera de alcance: {PLANNER}", where=BANNER),
        MandateRequest("bug", "Fix the banner", why=f"{PLANNER} is out of scope.", where=BANNER),
        MandateRequest(
            "bug", "Fix the banner", constraints=f"No toques `{PLANNER}`.", where=BANNER
        ),
        MandateRequest("bug", "Fix the banner", where=BANNER, out_of_scope=f"`{PLANNER}`"),
    ],
    ids=["label", "label-es", "prose", "prohibition-with-a-quoted-path", "field"],
)
def test_labels_prose_and_the_out_of_scope_field_still_guard(request_: MandateRequest) -> None:
    plan = planned(request_)
    assert PLANNER in plan.guard
    assert PLANNER not in {item.path for item in plan.edit}
