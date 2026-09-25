from __future__ import annotations

from collections.abc import Iterable

CUANTA_GITIGNORE = "*\n!config.toml\n"


def _normalized(entry: str) -> str:
    return entry.strip().lstrip("/").rstrip("/")


def missing_entries(existing: str, entries: Iterable[str]) -> tuple[str, ...]:
    present = {_normalized(line) for line in existing.splitlines() if line.strip()}
    return tuple(entry for entry in entries if _normalized(entry) not in present)


def with_entries(existing: str, entries: Iterable[str]) -> str | None:
    missing = missing_entries(existing, entries)
    if not missing:
        return None
    prefix = existing
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    return prefix + "".join(f"{entry}\n" for entry in missing)
