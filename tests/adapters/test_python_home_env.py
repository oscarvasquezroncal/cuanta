from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from cuanta.adapters.system.process_runner import SubprocessRunner
from cuanta.domain.python_home import drop_inherited_python_home

HOME = "PYTHONHOME"
MARKER = "UV_INTERNAL__PYTHONHOME"
PROBE = (
    "import json, os; "
    "print(json.dumps({name: os.environ.get(name) for name in "
    "('PYTHONHOME', 'UV_INTERNAL__PYTHONHOME')}))"
)


def started_child(
    monkeypatch: pytest.MonkeyPatch, values: dict[str, str], isolated: bool
) -> tuple[tuple[str, ...], dict[str, str | None]]:
    monkeypatch.delenv(HOME, raising=False)
    monkeypatch.delenv(MARKER, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    dropped = drop_inherited_python_home(os.environ, sys.base_prefix, os.name == "nt")
    flags: tuple[str, ...] = ("-E",) if isolated else ()
    completed = SubprocessRunner().run((sys.executable, *flags, "-c", PROBE), timeout=60)
    assert completed.returncode == 0, completed.stderr
    seen: dict[str, str | None] = json.loads(completed.stdout)
    return dropped, seen


def test_a_uv_set_home_never_reaches_a_child_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    injected = str(tmp_path / "uv-python")
    dropped, seen = started_child(monkeypatch, {HOME: injected, MARKER: injected}, False)
    assert dropped == (HOME, MARKER)
    assert seen == {HOME: None, MARKER: None}


def test_the_base_home_without_marker_never_reaches_a_child_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dropped, seen = started_child(monkeypatch, {HOME: sys.base_prefix}, False)
    assert dropped == (HOME,)
    assert seen == {HOME: None, MARKER: None}


def test_a_user_home_reaches_a_child_python_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chosen = str(tmp_path / "user-python")
    dropped, seen = started_child(monkeypatch, {HOME: chosen}, True)
    assert dropped == ()
    assert seen == {HOME: chosen, MARKER: None}


def test_a_home_with_a_disagreeing_marker_reaches_a_child_python_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chosen = str(tmp_path / "user-python")
    stale = str(tmp_path / "uv-python")
    dropped, seen = started_child(monkeypatch, {HOME: chosen, MARKER: stale}, True)
    assert dropped == ()
    assert seen == {HOME: chosen, MARKER: stale}
