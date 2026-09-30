from __future__ import annotations

from cuanta.application.scout import senior_rules
from cuanta.domain.scout import SCOUT_BODY


def test_the_senior_is_told_the_pack_replaces_the_analyst_plan() -> None:
    rules = senior_rules(4)
    assert "replace the analyst's plan JSON" in rules
    assert "Do not stop or report blocked because a plan JSON is missing" in rules
    assert "about 4 file reads" in rules


def test_the_scout_treats_card_flags_as_hints_and_blocks_only_impossible_requests() -> None:
    assert "hints from cuanta's index, not locks" in SCOUT_BODY
    assert "put it in the edit set and note the flag as a risk" in SCOUT_BODY
    assert "blocked only when the request cannot be done at all" in SCOUT_BODY
