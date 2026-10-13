from __future__ import annotations

import ntpath
import posixpath
from collections.abc import Mapping, MutableMapping

PYTHON_HOME = "PYTHONHOME"
UV_MARKER = "UV_INTERNAL__PYTHONHOME"


def inherited_python_home(
    environment: Mapping[str, str], base_prefix: str, windows: bool
) -> tuple[str, ...]:
    home = environment.get(PYTHON_HOME, "")
    if not home:
        return ()
    if UV_MARKER in environment:
        return (PYTHON_HOME, UV_MARKER) if environment[UV_MARKER] == home else ()
    return (PYTHON_HOME,) if _folder(home, windows) == _folder(base_prefix, windows) else ()


def drop_inherited_python_home(
    environment: MutableMapping[str, str], base_prefix: str, windows: bool
) -> tuple[str, ...]:
    dropped = inherited_python_home(environment, base_prefix, windows)
    for name in dropped:
        environment.pop(name, None)
    return dropped


def _folder(path: str, windows: bool) -> str:
    if windows:
        return ntpath.normcase(ntpath.normpath(path))
    return posixpath.normpath(path)
