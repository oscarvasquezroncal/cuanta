from __future__ import annotations

import pytest

from cuanta.domain.messages import english, message_payload, msg, parse_message
from cuanta.domain.stop_reason import team_stop
from cuanta.tui.i18n import Catalog


def test_a_stored_stop_round_trips_with_nested_params() -> None:
    stop = msg("cross.blocked", role="senior", reason=msg("stop.wall_limit", minutes="30"))
    payload = message_payload(stop)
    assert payload == {
        "key": "cross.blocked",
        "params": {
            "role": "senior",
            "reason": {"key": "stop.wall_limit", "params": {"minutes": "30"}},
        },
    }
    assert parse_message(payload) == stop


@pytest.mark.parametrize(
    "stored",
    [None, "stopped", {"key": 3}, {"key": ""}, {"key": "stop.user", "params": {"n": 1}}],
)
def test_an_unreadable_stored_stop_is_ignored(stored: object) -> None:
    assert parse_message(stored) is None


def test_the_team_stop_applies_only_when_the_team_did_not_complete() -> None:
    wall = msg("stop.wall_limit", minutes="30")
    assert team_stop("partial", wall) == wall
    assert team_stop("complete", wall) is None
    assert team_stop("", None) is None
    assert english(team_stop("partial", None) or msg("stop.finished")) == (
        "the team stopped before every role finished"
    )
    spanish = Catalog("es")
    assert spanish.message(team_stop("failed", None)) == "la ejecución del equipo falló"
    assert spanish.message(team_stop("partial", None)) == (
        "el equipo se detuvo antes de que terminaran todos los roles"
    )
