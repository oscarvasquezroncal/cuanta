from __future__ import annotations

from cuanta.domain.ledger import Run, last_launched, launched_run


def run(run_id: str, parent: str = "") -> Run:
    return Run(run_id, "cross", "claude", parent_id=parent)


def lookup_in(*runs: Run) -> dict[str, Run]:
    return {item.id: item for item in runs}


def test_a_role_leads_to_the_run_a_person_launched() -> None:
    loop, fix, role = run("LOOP"), run("FIX", "LOOP"), run("ROLE", "FIX")
    known = lookup_in(loop, fix, role)
    assert launched_run(role, known.get) == loop
    assert launched_run(loop, known.get) == loop


def test_a_missing_parent_or_a_cycle_stops_at_the_last_known_run() -> None:
    orphan = run("ORPHAN", "GONE")
    assert launched_run(orphan, lookup_in(orphan).get) == orphan
    first, second = run("A", "B"), run("B", "A")
    assert launched_run(first, lookup_in(first, second).get) == second


def test_the_last_launched_run_skips_roles_and_may_be_none() -> None:
    newest_first = (run("ROLE", "ROOT"), run("ROOT"), run("OLDER"))
    assert last_launched(newest_first) == run("ROOT")
    assert last_launched(()) is None
    assert last_launched((run("ROLE", "ROOT"),)) is None
