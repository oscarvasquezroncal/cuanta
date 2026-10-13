from __future__ import annotations

import pytest

from cuanta.domain.mandate import MandateRequest, label_field, render_mandate
from cuanta.domain.mandate_file import parse_mandate_file
from cuanta.domain.messages import english
from cuanta.domain.report import next_request, next_step
from cuanta.tui.i18n import Catalog

AUDIT_NEXT = """## SUMMARY
The version field is read twice.

NEXT STEP

REQUEST TYPE: investigation
WHAT: Confirm that the version field survives the gateway proxy unchanged, and list every place it can be overridden.
WHY / EVIDENCE: The forwarding code after app/routers/gateway.py:135 and the service enqueue path were not read.
WHERE: app/routers/gateway.py (135-end); app/services/jobs.py
CONSTRAINTS: Cite path:line for every claim.
EXPECTED TESTS: Deliverable: a written report
OUT OF SCOPE: Changing the default version or any code.
"""


def test_request_type_is_read_and_a_plain_type_is_unchanged() -> None:
    found = next_request(AUDIT_NEXT)
    assert found is not None
    assert found.type == "investigation"
    assert found.where == "app/routers/gateway.py (135-end); app/services/jobs.py"
    plain = next_request(
        AUDIT_NEXT.replace("REQUEST TYPE:", "TYPE:").replace("investigation", "bug")
    )
    assert plain is not None and plain.type == "bug"


@pytest.mark.parametrize(
    "label", ["REQUEST TYPE", "TIPO DE SOLICITUD", "Tipo de pedido", "TYPE", "tipo"]
)
def test_one_vocabulary_names_the_type(label: str) -> None:
    assert label_field(label) == "type"
    assert label_field("WHY / EVIDENCE") == "why" and label_field("nonsense") is None


def test_an_untyped_next_step_is_an_investigation_with_a_note() -> None:
    untyped = AUDIT_NEXT.replace("REQUEST TYPE: investigation\n", "")
    step = next_step(untyped)
    assert step is not None
    assert step.request.type == "investigation"
    assert [english(note) for note in step.notes] == [
        "The NEXT STEP did not state its type, so it runs as an investigation"
    ]
    assert "investigación" in Catalog("es").message(step.notes[0])
    typed = next_step(AUDIT_NEXT)
    assert typed is not None and typed.notes == ()
    assert next_step("## SUMMARY\nnothing next\n") is None


@pytest.mark.parametrize("label", ["REQUEST TYPE", "TIPO DE SOLICITUD"])
def test_the_file_parser_reads_the_request_type_labels(label: str) -> None:
    parsed = parse_mandate_file(f"{label}: investigation\nWHAT: Read the proxy\n")
    assert parsed.request.type == "investigation"
    assert parsed.request.what == "Read the proxy"


def test_the_written_next_step_reads_back_as_the_same_request() -> None:
    request = MandateRequest(
        "investigation",
        "Confirm the version field\nand list the overrides.",
        "The forwarding code was not read.\n\nWHERE: looked like a label but is evidence.",
        "app/routers/gateway.py (135-end)",
        "Cite path:line for every claim.",
        "Deliverable: a written report",
        "Changing any code.",
    )
    text = render_mandate(request)
    assert text.startswith("TYPE: investigation\nWHAT: Confirm the version field\n")
    assert parse_mandate_file(text).request == request
