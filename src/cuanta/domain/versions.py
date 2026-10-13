from __future__ import annotations

import re

LEADING = re.compile(r"\s*v?(\d+(?:\.\d+)*)")


def leading_version(text: str) -> tuple[int, ...] | None:
    found = LEADING.match(text)
    if found is None:
        return None
    return tuple(int(part) for part in found.group(1).split("."))


def at_least(version: str, minimum: str, unknown: bool = True) -> bool:
    installed = leading_version(version)
    needed = leading_version(minimum)
    if needed is None:
        return True
    if installed is None:
        return unknown
    return installed >= needed
