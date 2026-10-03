from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.cross_engine import CompletionState, CrossReport, cross_metrics
from cuanta.application.run_reports import RunReports
from cuanta.domain.ledger import Run
from cuanta.domain.messages import Message, msg
from tests.fakes import FakeRunner
from tests.support import invoke

ROOT = "01JTEAMROOT"


def seed(root: Path, meta: dict[str, object]) -> None:
    (root / ".cuanta").mkdir(parents=True, exist_ok=True)
    ledger = SqliteLedger(root / ".cuanta" / "ledger.db")
    try:
        ledger.add_run(
            Run(
                ROOT,
                "cross",
                "codex",
                started_at="2026-10-02T10:00:00Z",
                ended_at="2026-10-02T10:20:00Z",
                status="ok",
                end_reason="success",
                cost_usd=0.4,
                task_type="feature",
                scope="analyst",
            )
        )
        ledger.add_run(
            Run(
                "01JTEAMSENIOR",
                "cross",
                "codex",
                started_at="2026-10-02T10:05:00Z",
                ended_at="2026-10-02T10:30:00Z",
                status="failed",
                end_reason="error_max_wall",
                cost_usd=1.8,
                parent_id=ROOT,
                scope="senior",
                partial=True,
                max_wall_s=1800.0,
            )
        )
    finally:
        ledger.close()
    RunReports(LocalWorkspace(root)).save_meta(ROOT, meta)


def team(state: CompletionState, stopped: Message | None) -> dict[str, object]:
    return cross_metrics(CrossReport((), False, 2.2, stopped, state=state, partial=True))


def shown(root: Path) -> tuple[dict[str, object], str]:
    payload = json.loads(invoke(["runs", "show", ROOT, "--json", "--project", str(root)]).stdout)
    plain = invoke(["runs", "show", ROOT, "--plain", "--project", str(root)]).stdout
    return payload, " ".join(plain.split())


@pytest.mark.parametrize(
    ("meta", "line"),
    [
        (
            team(CompletionState.PARTIAL, msg("stop.wall_limit", minutes="30")),
            "stopped by the time limit of 30 min",
        ),
        (
            team(CompletionState.FAILED, msg("cross.failed", role="senior")),
            "senior failed: the pipeline stopped",
        ),
        ({"completion": "partial"}, "the team stopped before every role finished"),
        ({"completion": "failed"}, "the team run failed"),
        (team(CompletionState.COMPLETE, None), "the agent finished"),
    ],
)
def test_runs_show_of_a_team_root_gives_the_team_stop(
    tmp_path: Path, fake_runner: FakeRunner, meta: dict[str, object], line: str
) -> None:
    seed(tmp_path, meta)
    payload, text = shown(tmp_path)
    assert payload["stop_reason"] == line
    assert f"stop reason {line}" in text
