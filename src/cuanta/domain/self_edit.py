from __future__ import annotations

import ntpath
import posixpath

from cuanta.domain.messages import Message, msg

INSTALLED = "site-packages"


def self_edit_note(package_dir: str, project: str, windows: bool) -> Message | None:
    paths = ntpath if windows else posixpath
    package = paths.normcase(paths.normpath(package_dir))
    root = paths.normcase(paths.normpath(project))
    try:
        inside = paths.commonpath([package, root]) == root
    except ValueError:
        return None
    parts = package.replace("\\", "/").split("/")
    if not inside or INSTALLED in parts:
        return None
    return msg("launch.self_edit")
