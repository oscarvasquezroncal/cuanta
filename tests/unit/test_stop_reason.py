from __future__ import annotations

from dataclasses import replace

import pytest

from cuanta.domain.governor_report import GovernorEntry, GovernorSummary
from cuanta.domain.ledger import Run
from cuanta.domain.messages import english
from cuanta.domain.stop_reason import stop_message
from cuanta.tui.i18n import Catalog
from tests.real_run import REAL_MODEL, REAL_RUN_ID

REAL_RUN = Run(
    REAL_RUN_ID,
    "mandate",
    "claude",
    model=REAL_MODEL,
    status="failed",
    max_turns=100_000,
    cap_usd=500.0,
)
STORED: dict[str, object] = {
    "passed": False,
    "repairs": 0,
    "reason": "time_limit",
    "engine_subtype": "",
    "repair_limit": 3,
    "repair_rounds_remaining": 3,
    "time_limit_s": 900.0,
    "elapsed_s": 900.0,
    "steps": [{"title": "Implementation", "state": "running"}],
    "checks": [],
}
FAILED = Run("R", "mandate", "claude", status="failed")
FINISH = GovernorSummary(
    (GovernorEntry("finish_now", "senior", "projection", 30.0, "R", True, 1.2, 1.5, False, None),)
)


@pytest.mark.parametrize(
    ("run", "payload", "governor", "expected"),
    [
        (REAL_RUN, STORED, None, "stopped by the time limit of 900 s"),
        (
            REAL_RUN,
            {**STORED, "reason": "repair_time_limit"},
            None,
            "stopped by the repair time limit of 900 s",
        ),
        (
            replace(REAL_RUN, max_wall_s=1800.0),
            {**STORED, "reason": "wall_limit"},
            None,
            "stopped by the time limit of 30 min",
        ),
        (
            replace(FAILED, end_reason="error_max_budget_usd", cap_usd=5.0),
            None,
            None,
            "stopped at the spend cap of $5.00",
        ),
        (
            replace(FAILED, end_reason="error_cost_unknown", cap_usd=0.6),
            None,
            None,
            "stopped because the engine did not report the cost needed to enforce the $0.60 cap",
        ),
        (
            replace(FAILED, end_reason="error_max_turns", max_turns=40),
            None,
            None,
            "stopped at the turn limit of 40 turns",
        ),
        (
            replace(FAILED, end_reason="error_governor_stop", cap_usd=2.0),
            None,
            None,
            "stopped by the governor to stay within the $2.00 cap",
        ),
        (
            replace(FAILED, end_reason="error_max_wall", max_wall_s=600.0),
            None,
            None,
            "stopped by the time limit of 10 min",
        ),
        (replace(FAILED, end_reason="cancelled"), None, None, "stopped by the user"),
        (
            FAILED,
            {**STORED, "reason": "engine_exited", "exit_code": 1},
            None,
            "stopped because the engine exited with code 1 before its result",
        ),
        (FAILED, {**STORED, "reason": "stopped"}, None, "stopped by the user"),
        (FAILED, None, None, "stopped because the engine ended without a result"),
        (
            FAILED,
            {**STORED, "reason": "repair_limit"},
            None,
            "stopped after 3 repair rounds with new errors left",
        ),
        (
            FAILED,
            {
                **STORED,
                "reason": "unfinished",
                "steps": [
                    {"title": "one", "state": "green"},
                    {"title": "two", "state": "stopped"},
                ],
            },
            None,
            "stopped before cuanta could verify step 2 of 2",
        ),
        (
            FAILED,
            {**STORED, "reason": "verification_failed", "checks": [{"introduced": ["a", "b"]}]},
            None,
            "stopped because verification found 2 new errors",
        ),
        (replace(FAILED, status="interrupted"), None, None, "interrupted before the run finished"),
        (replace(FAILED, status="ok", end_reason="success"), None, None, "the agent finished"),
        (
            replace(FAILED, status="ok", end_reason="success"),
            None,
            FINISH,
            "the agent finished after the governor asked it to wrap up at $1.20 of $1.50",
        ),
        (
            replace(FAILED, end_reason="error_during_execution"),
            None,
            None,
            "stopped because the engine ended with error_during_execution",
        ),
    ],
    ids=[
        "recorded_run",
        "repair_window",
        "wall_limit",
        "spend_cap",
        "cost_unknown",
        "turn_limit",
        "governor",
        "wall_end_reason",
        "user_stop",
        "engine_exited",
        "stopped_session",
        "no_result",
        "repair_limit",
        "unfinished_step",
        "verification",
        "interrupted",
        "finished",
        "finished_on_request",
        "engine_failed",
    ],
)
def test_each_rail_is_one_line_with_its_number(
    run: Run,
    payload: dict[str, object] | None,
    governor: GovernorSummary | None,
    expected: str,
) -> None:
    assert english(stop_message(run, payload, governor)) == expected


def test_the_recorded_run_reads_in_the_user_language() -> None:
    spanish = Catalog("es")
    assert spanish.message(stop_message(REAL_RUN, STORED)) == (
        "se detuvo por el límite de tiempo de 900 s"
    )
    finished = replace(REAL_RUN, status="ok", end_reason="success")
    assert spanish.message(stop_message(finished)) == "el agente terminó"
    assert "Stopped at the spend cap" in english(
        stop_message(replace(FAILED, end_reason="error_max_budget_usd"))
    )


@pytest.mark.parametrize(
    ("run", "payload", "expected", "spanish"),
    [
        (
            FAILED,
            {**STORED, "reason": "verification_failed", "checks": [{"introduced": ["a"]}]},
            "stopped because verification found 1 new error",
            "se detuvo porque la verificación encontró 1 error nuevo",
        ),
        (
            FAILED,
            {**STORED, "reason": "repair_limit", "repair_limit": 1},
            "stopped after 1 repair round with new errors left",
            "se detuvo tras 1 ronda de reparación con errores nuevos pendientes",
        ),
        (
            replace(FAILED, end_reason="error_max_turns", max_turns=1),
            None,
            "stopped at the turn limit of 1 turn",
            "se detuvo en el límite de 1 turno",
        ),
        (
            FAILED,
            {**STORED, "reason": "turn_limit", "max_turns": 1},
            "stopped at the turn limit of 1 turn",
            "se detuvo en el límite de 1 turno",
        ),
    ],
    ids=["one_new_error", "one_repair_round", "one_turn", "one_turn_in_a_session"],
)
def test_a_count_of_one_reads_in_the_singular(
    run: Run, payload: dict[str, object] | None, expected: str, spanish: str
) -> None:
    assert english(stop_message(run, payload)) == expected
    assert Catalog("es").message(stop_message(run, payload)) == spanish
