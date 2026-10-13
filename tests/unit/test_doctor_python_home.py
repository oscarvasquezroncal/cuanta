from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from cuanta.application import doctor
from cuanta.application.doctor import Check, CheckResult, python_home_check
from cuanta.bootstrap import Container
from cuanta.domain.detection import Detection
from cuanta.domain.messages import msg
from cuanta.domain.progress import Status
from cuanta.tui.i18n import Catalog
from tests.tui.fakes import DETECTION

HOME = "PYTHONHOME"
MARKER = "UV_INTERNAL__PYTHONHOME"
WINDOWS_BASE = "C:\\py\\cpython-3.13-windows-x86_64-none"


def rows(
    environment: Mapping[str, str], base_prefix: str, windows: bool = False
) -> Sequence[CheckResult]:
    return python_home_check(environment, base_prefix, windows)(DETECTION)


def test_no_home_means_no_row() -> None:
    assert list(rows({}, "/usr")) == []
    assert list(rows({HOME: ""}, "/usr")) == []


def test_a_user_home_is_one_warning_that_names_the_path() -> None:
    found = rows({HOME: "/opt/py"}, "/usr")
    assert [(row.name, row.status, row.fix) for row in found] == [(HOME, Status.WARN, "")]
    assert found[0].message == msg("doctor.python_home.set", path="/opt/py")
    assert "/opt/py" in found[0].detail
    assert HOME in found[0].detail


def test_a_home_with_a_disagreeing_marker_is_still_a_warning() -> None:
    found = rows({HOME: "/opt/py", MARKER: "/opt/uv"}, "/usr")
    assert [(row.name, row.status) for row in found] == [(HOME, Status.WARN)]


def test_a_uv_set_home_stays_silent() -> None:
    marked = {HOME: "C:\\uv\\python", MARKER: "C:\\uv\\python"}
    assert list(rows({HOME: "/opt/uv", MARKER: "/opt/uv"}, "/usr")) == []
    assert list(rows(marked, WINDOWS_BASE, True)) == []


def test_the_base_home_without_marker_stays_silent() -> None:
    inherited = {HOME: "c:/PY/cpython-3.13-windows-x86_64-none/"}
    assert list(rows({HOME: "/usr"}, "/usr")) == []
    assert list(rows(inherited, WINDOWS_BASE, True)) == []


def test_the_warning_reads_in_spanish_with_the_path() -> None:
    found = rows({HOME: "/opt/py"}, "/usr")
    spanish = Catalog("es").message(found[0].message)
    assert "/opt/py" in spanish
    assert HOME in spanish
    assert spanish != found[0].detail


def test_the_doctor_runs_the_check_on_the_process_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[dict[str, str], str, bool]] = []
    built: list[Sequence[Check]] = []

    def silent(detection: Detection) -> Sequence[CheckResult]:
        return []

    def factory(environment: Mapping[str, str], base_prefix: str, windows: bool) -> Check:
        calls.append((dict(environment), base_prefix, windows))
        return silent

    class Recorder:
        def __init__(self, detector: object, checks: Sequence[Check]) -> None:
            built.append(checks)

    monkeypatch.setattr(doctor, "python_home_check", factory)
    monkeypatch.setattr(doctor, "Doctor", Recorder)
    container = Container.for_project(tmp_path)
    try:
        container.doctor()
    finally:
        container.close()
    assert calls == [(dict(os.environ), sys.base_prefix, os.name == "nt")]
    assert len(built) == 1
    assert silent in built[0]
