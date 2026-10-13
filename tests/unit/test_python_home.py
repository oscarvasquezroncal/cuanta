from __future__ import annotations

from cuanta.domain.python_home import drop_inherited_python_home, inherited_python_home

HOME = "PYTHONHOME"
MARKER = "UV_INTERNAL__PYTHONHOME"
WINDOWS_BASE = "C:\\py\\cpython-3.13-windows-x86_64-none"
POSIX_BASE = "/opt/py"


def test_a_uv_marked_home_drops_both_keys_whatever_the_base() -> None:
    marked = {HOME: "/opt/uv", MARKER: "/opt/uv"}
    assert inherited_python_home(marked, POSIX_BASE, False) == (HOME, MARKER)
    assert inherited_python_home(marked, WINDOWS_BASE, True) == (HOME, MARKER)


def test_a_marker_that_disagrees_keeps_the_home_even_on_the_base() -> None:
    environment = {HOME: POSIX_BASE, MARKER: "/opt/uv"}
    assert inherited_python_home(environment, POSIX_BASE, False) == ()


def test_on_windows_the_base_home_ignores_case_slashes_and_trailing_separator() -> None:
    environment = {HOME: "c:/PY/cpython-3.13-windows-x86_64-none/"}
    assert inherited_python_home(environment, WINDOWS_BASE, True) == (HOME,)


def test_on_posix_case_is_significant() -> None:
    assert inherited_python_home({HOME: "/OPT/py"}, POSIX_BASE, False) == ()


def test_on_posix_a_trailing_slash_still_matches_the_base() -> None:
    assert inherited_python_home({HOME: "/opt/py/"}, POSIX_BASE, False) == (HOME,)


def test_another_folder_is_a_user_choice() -> None:
    sibling = "C:\\py\\cpython-3.12-windows-x86_64-none"
    inside = "C:\\py\\cpython-3.13-windows-x86_64-none\\Lib"
    assert inherited_python_home({HOME: "/opt/other"}, POSIX_BASE, False) == ()
    assert inherited_python_home({HOME: "/opt"}, POSIX_BASE, False) == ()
    assert inherited_python_home({HOME: sibling}, WINDOWS_BASE, True) == ()
    assert inherited_python_home({HOME: inside}, WINDOWS_BASE, True) == ()
    assert inherited_python_home({HOME: "C:\\py"}, WINDOWS_BASE, True) == ()


def test_nothing_to_drop_without_a_home() -> None:
    assert inherited_python_home({}, POSIX_BASE, False) == ()
    assert inherited_python_home({HOME: ""}, POSIX_BASE, False) == ()
    assert inherited_python_home({HOME: "", MARKER: ""}, POSIX_BASE, False) == ()
    assert inherited_python_home({MARKER: "/opt/uv"}, POSIX_BASE, False) == ()


def test_dropping_removes_exactly_the_returned_keys_and_keeps_the_rest() -> None:
    environment = {
        HOME: "/opt/uv",
        MARKER: "/opt/uv",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PATH": "/usr/bin",
    }
    assert drop_inherited_python_home(environment, POSIX_BASE, False) == (HOME, MARKER)
    assert environment == {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin"}


def test_dropping_the_base_home_without_marker_keeps_the_rest() -> None:
    environment = {
        HOME: "c:/PY/cpython-3.13-windows-x86_64-none/",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    assert drop_inherited_python_home(environment, WINDOWS_BASE, True) == (HOME,)
    assert environment == {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def test_dropping_leaves_a_user_home_and_a_disagreeing_marker_alone() -> None:
    chosen = {HOME: "/srv/python", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    stale = {HOME: POSIX_BASE, MARKER: "/opt/uv", "PYTHONUTF8": "1"}
    for environment in (chosen, stale):
        before = dict(environment)
        assert drop_inherited_python_home(environment, POSIX_BASE, False) == ()
        assert environment == before
