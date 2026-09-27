from __future__ import annotations

import pytest

from cuanta.application.cross_engine import CompletionState, CrossReport, CrossStep
from cuanta.domain.errors import NotAvailable
from cuanta.domain.ledger import Run
from cuanta.domain.messages import msg
from cuanta.domain.routing import Role
from cuanta.tui.services import cross_report


def test_a_cross_run_reaches_the_pipeline_screen_as_a_mandate_report() -> None:
    steps = (
        CrossStep(Role.ANALYST, "claude", "sonnet", "R1", True, 0.2, "found it"),
        CrossStep(Role.SENIOR, "codex", "gpt-6-sol", "R2", True, 0.5, "edited"),
    )
    report = CrossReport(
        steps,
        False,
        0.7,
        msg("cross.role_budget", role="tester"),
        changed_files=("src/a.ts",),
        state=CompletionState.PARTIAL,
    )
    root = Run("R1", "cross", engine="claude", cost_usd=0.2, task_type="feature")
    converted = cross_report(root, report)
    assert converted.run.id == "R1" and converted.run.cost_usd == 0.7
    assert not converted.ok
    assert converted.changed_files == ("src/a.ts",)
    assert converted.tests == "partial"
    assert converted.handoffs == ("found it", "edited")
    assert converted.text == "tester has no remaining reserved budget"
    assert converted.task_type == "feature"
    with pytest.raises(NotAvailable):
        cross_report(None, report)
