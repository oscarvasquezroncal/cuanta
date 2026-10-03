from __future__ import annotations

import pytest

from cuanta.domain.anchors import (
    AnchorCheck,
    AnchorState,
    RequestAnchor,
    anchor_candidates,
    anchor_notes,
    anchor_windows,
    extract_anchors,
    packed_state,
    request_anchors,
)
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import english

PATHS = ("app/services/interpreter.py", "app/routers/consult.py", "tests/test_consult.py")


def test_request_anchors_come_from_every_field_in_order_with_windows_forms() -> None:
    request = MandateRequest(
        "feature",
        "Implementar el mandato adjunto",
        why=(
            "## Fase 2\n"
            "Corregir `app/services/interpreter.py:287-289` y app\\routers\\consult.py:67.\n"
            "Ver C:\\proj\\app\\services\\interpreter.py:300 y app/routers/consult.py#L70-L72.\n"
            "Reunión 10:30, Python 3.12: https://example.com:8080/x, Fase 1: auditar.\n"
            "Otra vez app/services/interpreter.py:287–289."
        ),
        tests="tests/test_consult.py:12 cubre el caso",
        out_of_scope="no tocar app/legacy.py:1-50",
    )
    assert request_anchors(request) == (
        RequestAnchor("app/services/interpreter.py", 287, 289, "why"),
        RequestAnchor("app/routers/consult.py", 67, 67, "why"),
        RequestAnchor("C:/proj/app/services/interpreter.py", 300, 300, "why"),
        RequestAnchor("app/routers/consult.py", 70, 72, "why"),
        RequestAnchor("tests/test_consult.py", 12, 12, "tests"),
    )
    assert anchor_candidates("C:/proj/app/services/interpreter.py", PATHS) == (
        "app/services/interpreter.py",
    )
    assert anchor_candidates("APP/Routers/consult.py", PATHS) == ("app/routers/consult.py",)
    assert anchor_candidates("/srv/proj/app/routers/consult.py", PATHS) == (
        "app/routers/consult.py",
    )
    assert anchor_candidates(
        "services/interpreter.py", (*PATHS, "lib/services/interpreter.py")
    ) == (
        "app/services/interpreter.py",
        "lib/services/interpreter.py",
    )
    assert anchor_candidates("app/models/legacy.py", PATHS) == ()
    assert RequestAnchor("app/x.py", 3, 3).label == "app/x.py:3"
    assert RequestAnchor("app/x.py", 3, 9).label == "app/x.py:3-9"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("src/app.ts:12:5", (("src/app.ts", 12, 12),)),
        ("src/app.ts:L4", (("src/app.ts", 4, 4),)),
        ("src/app.ts#L4", (("src/app.ts", 4, 4),)),
        ("src/app.ts:4 — 9", (("src/app.ts", 4, 9),)),
        ("./src/app.ts:4-L9", (("src/app.ts", 4, 9),)),
        ("README.md:3", (("README.md", 3, 3),)),
        ("`/srv/proj/app/main.py:8`", (("/srv/proj/app/main.py", 8, 8),)),
        ("src/app.ts:40-12", (("src/app.ts", 40, 40),)),
        ("src/app.ts:12\n- 3 casos", (("src/app.ts", 12, 12),)),
        ("src/app.ts:0", ()),
        ("a las 10:30, Python 3.12: lista", ()),
        ("https://example.com:8080/app/main.py:3 y http://host/x.py:1", ()),
        ("Fase 1: auditar, Paso 2.1: corregir, v1.2.3:4", ()),
    ],
)
def test_anchor_syntax_reads_file_references_and_ignores_times_versions_and_urls(
    text: str, expected: tuple[tuple[str, int, int], ...]
) -> None:
    found = extract_anchors(text, "why")
    assert tuple((anchor.path, anchor.start, anchor.end) for anchor in found) == expected
    assert all(anchor.field == "why" for anchor in found)


def test_anchor_windows_merge_overlapping_ranges_and_set_aside_lines_past_the_end() -> None:
    anchors = (
        RequestAnchor("C:/proj/app/x.py", 12, 12, "why"),
        RequestAnchor("app/x.py", 10, 11, "why"),
        RequestAnchor("app/x.py", 12, 12, "tests"),
        RequestAnchor("app/x.py", 60, 64, "why"),
        RequestAnchor("app/x.py", 95, 95, "why"),
        RequestAnchor("app/x.py", 1, 500, "where"),
    )
    windows, beyond = anchor_windows("app/x.py", anchors, 90, 12)
    assert [(item.anchor.label, item.first, item.last) for item in windows] == [
        ("app/x.py:1-500", 1, 12),
        ("app/x.py:10-12", 7, 15),
        ("app/x.py:60-64", 57, 67),
    ]
    assert windows[1].members == (anchors[1], anchors[0], anchors[2])
    assert beyond == (anchors[4],)


def test_anchor_notes_name_each_problem_once_in_a_fixed_order_and_skip_hostnames() -> None:
    missing = tuple(
        AnchorCheck(
            RequestAnchor(f"app/gone_{index}.py", index + 1, index + 1), "", AnchorState.MISSING
        )
        for index in range(10)
    )
    checks = (
        AnchorCheck(RequestAnchor("api.example.com", 443, 443), "", AnchorState.MISSING),
        AnchorCheck(
            RequestAnchor("node_modules/react-dom/index.js", 9, 9), "", AnchorState.MISSING
        ),
        AnchorCheck(
            RequestAnchor("/srv/env/lib/site-packages/_pytest/main.py", 3, 3),
            "",
            AnchorState.MISSING,
        ),
        AnchorCheck(RequestAnchor("app/x.py", 2, 2), "app/x.py", AnchorState.PACKED),
        AnchorCheck(
            RequestAnchor("C:/proj/app/x.py", 900, 900), "app/x.py", AnchorState.OUT_OF_RANGE
        ),
        AnchorCheck(RequestAnchor("app/y.py", 5, 9), "app/y.py", AnchorState.OMITTED),
        AnchorCheck(RequestAnchor("app/z.py", 1, 1), "app/z.py", AnchorState.PROTECTED),
        AnchorCheck(RequestAnchor("services/w.py", 4, 4), "", AnchorState.AMBIGUOUS),
        AnchorCheck(RequestAnchor("app/v.py", 7, 7), "app/v.py", AnchorState.STALE),
        *missing,
    )
    assert [english(message) for message in anchor_notes(checks, 6000)] == [
        "File:line references not found among the indexed files: app/gone_0.py:1, "
        "app/gone_1.py:2, app/gone_2.py:3, app/gone_3.py:4, app/gone_4.py:5, app/gone_5.py:6, "
        "app/gone_6.py:7, app/gone_7.py:8 +2",
        "File:line references that match more than one indexed file: services/w.py:4",
        "File:line references past the last line of their file: app/x.py:900",
        "Referenced files changed after indexing and were left out of the pack: app/v.py:7",
        "Referenced ranges left out by the 6,000-token pack budget: app/y.py:5-9",
        "Referenced files the change plan protects (context only, not editable): app/z.py:1",
    ]
    assert anchor_notes(checks[:4], 6000) == ()


ROOTED = ("main.py", "conftest.py", "README.md", "backend/app/main.py", "backend/tests/conftest.py")


@pytest.mark.parametrize(
    ("mention", "expected"),
    [
        ("app/main.py", ("backend/app/main.py",)),
        ("tests/conftest.py", ("backend/tests/conftest.py",)),
        ("App/main.py", ("backend/app/main.py",)),
        ("proj/backend/app/main.py", ("backend/app/main.py",)),
        ("C:/proj/backend/tests/conftest.py", ("backend/tests/conftest.py",)),
        ("/srv/proj/main.py", ("main.py",)),
        ("x/README.md", ("README.md",)),
        ("main.py", ("main.py",)),
    ],
)
def test_a_relative_anchor_resolves_to_the_deeper_file_it_names_before_a_root_file(
    mention: str, expected: tuple[str, ...]
) -> None:
    assert anchor_candidates(mention, ROOTED) == expected


def test_a_relative_anchor_that_names_two_deeper_files_stays_ambiguous() -> None:
    paths = (*ROOTED, "frontend/tests/conftest.py")
    assert anchor_candidates("tests/conftest.py", paths) == (
        "backend/tests/conftest.py",
        "frontend/tests/conftest.py",
    )
    assert anchor_candidates("Tests/Conftest.py", paths) == (
        "backend/tests/conftest.py",
        "frontend/tests/conftest.py",
    )


def test_a_range_longer_than_the_window_is_packed_in_part_and_the_note_names_the_lines() -> None:
    long = RequestAnchor("app/x.py", 100, 200, "why")
    short = RequestAnchor("app/x.py", 10, 12, "why")
    past = RequestAnchor("app/x.py", 1, 500, "where")
    windows, _ = anchor_windows("app/x.py", (long, short, past), 320, 12)
    spans = {member: window for window in windows for member in window.members}
    assert packed_state(long, spans[long], 320) == (AnchorState.PARTIAL, 100, 108)
    assert packed_state(short, spans[short], 320) == (AnchorState.PACKED, 10, 12)
    assert packed_state(past, spans[past], 20) == (AnchorState.PARTIAL, 1, 12)
    whole, _ = anchor_windows("app/x.py", (past,), 9, 12)
    assert packed_state(past, whole[0], 9) == (AnchorState.PACKED, 1, 9)
    checks = (
        AnchorCheck(long, "app/x.py", AnchorState.PARTIAL, 100, 108),
        AnchorCheck(short, "app/x.py", AnchorState.PACKED, 10, 12),
        AnchorCheck(RequestAnchor("app/y.py", 5, 9), "app/y.py", AnchorState.OMITTED),
    )
    assert [english(message) for message in anchor_notes(checks, 6000)] == [
        "Referenced ranges longer than the pack window, packed in part (range → packed lines): "
        "app/x.py:100-200 → 100-108",
        "Referenced ranges left out by the 6,000-token pack budget: app/y.py:5-9",
    ]
