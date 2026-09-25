from __future__ import annotations

import sys

import pytest

from cuanta.adapters.system import shell

TABLE = {10: (1, "python.exe"), 11: (10, "node.exe"), 12: (11, "sleep.exe"), 20: (1, "other.exe")}


def test_born_after_needs_both_times_in_order() -> None:
    assert shell._born_after(200, 100)
    assert shell._born_after(100, 100)
    assert not shell._born_after(50, 100)
    assert not shell._born_after(None, 100)
    assert not shell._born_after(200, None)


def test_a_given_table_is_walked_without_time_checks() -> None:
    assert shell.descendants(10, TABLE) == {11, 12}


def test_live_walk_rejects_children_older_than_their_recorded_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    born = {10: 100, 11: 150, 12: 50}
    monkeypatch.setattr(shell, "process_table", lambda: TABLE)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(shell, "_creation_time", born.get, raising=False)
    assert shell.descendants(10) == {11}
    born[12] = 160
    assert shell.descendants(10) == {11, 12}
