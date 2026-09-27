from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from cuanta.domain.anatomy import Phase
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.spectrum import LeakKind, analyze
from tests.unit.test_anatomy import api, end, pipeline, start


def test_investigation_reports_observed_context_leaks_and_single_context_advice() -> None:
    report = analyze("investigation", pipeline(), task_type="investigation")
    leaks = {item.kind: item for item in report.leaks}
    assert leaks[LeakKind.SECOND_CONTEXT].tokens == 10
    assert leaks[LeakKind.RE_SUMMARY].tokens == 1250
    assert any("single context" in item.action for item in report.suggestions)
    assert report.anatomy.totals.total == report.totals.total
    assert report.anatomy.totals.cost_usd == pytest.approx(report.totals.cost_usd)


def test_service_name_without_matched_delegation_is_not_a_second_context() -> None:
    events = [api(0), api(1, "generate_session_title"), api(2, "analyst")]
    report = analyze("selection", events, task_type="investigation")
    assert not {LeakKind.SECOND_CONTEXT, LeakKind.RE_SUMMARY} & {item.kind for item in report.leaks}


def test_unclosed_or_overlapping_delegations_do_not_claim_extra_context() -> None:
    events = [api(0), start(1), start(2, "tester"), api(3, "analyst"), end(4)]
    assert LeakKind.SECOND_CONTEXT not in {item.kind for item in analyze("selection", events).leaks}


def test_title_request_inside_a_child_window_is_not_a_second_context() -> None:
    events = [
        api(0),
        start(1),
        replace(api(2, "analyst"), query_source="generate_session_title"),
        end(3),
    ]
    assert LeakKind.SECOND_CONTEXT not in {item.kind for item in analyze("selection", events).leaks}


def test_real_capture_has_exact_exclusive_anatomy_without_raw_payloads() -> None:
    source = Path(__file__).parents[1] / "fixtures/telemetry/real_investigation.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    events = [LedgerEvent(**item) for item in data["events"]]
    report = analyze("real capture", events, task_type="investigation")
    assert [item.totals.requests for item in report.anatomy.phases] == [2, 8, 1, 1]
    assert [item.totals.total for item in report.anatomy.phases] == [65648, 367203, 59855, 49255]
    assert report.anatomy.totals.total == report.totals.total == 541961
    assert report.anatomy.totals.cost_usd == pytest.approx(0.54546)
    assert report.anatomy.phases[-1].phase is Phase.HANDOFF
    assert {LeakKind.SECOND_CONTEXT, LeakKind.RE_SUMMARY} <= {item.kind for item in report.leaks}
    assert "raw" not in json.dumps(asdict(report.anatomy))
    assert "tool_parameters" not in json.dumps(asdict(report.anatomy))
