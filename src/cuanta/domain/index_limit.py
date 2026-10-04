from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from cuanta.domain.errors import DomainFailure
from cuanta.domain.messages import Message, english, msg

INDEX_FILE_LIMIT = 20_000
LARGEST_FOLDERS = 5
HIDDEN_PREFIX = "."
TOML_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def toml_string(value: str) -> str:
    escaped = "".join(
        TOML_ESCAPES.get(char)
        or (f"\\u{ord(char):04X}" if ord(char) < 0x20 or ord(char) == 0x7F else char)
        for char in value
    )
    return f'"{escaped}"'


def grouped(value: int) -> str:
    return f"{value:,}"


@dataclass(frozen=True, slots=True)
class OversizedIndex:
    files: int
    limit: int
    largest: tuple[tuple[str, int], ...]
    exclude: tuple[str, ...]

    @property
    def lines(self) -> str:
        names = ", ".join(toml_string(name) for name in self.exclude)
        return f"[detect]\nexclude = [{names}]"

    @property
    def folders(self) -> Message | str:
        if not self.largest:
            return msg("index.no_folders")
        return " · ".join(f"{name} ({grouped(count)})" for name, count in self.largest)

    @property
    def reason(self) -> Message:
        return msg(
            "index.too_large",
            files=grouped(self.files),
            limit=grouped(self.limit),
            folders=self.folders,
        )

    @property
    def advice(self) -> Message:
        return msg("index.too_large_fix", lines=self.lines)


def _top_folders(paths: Sequence[str]) -> tuple[tuple[str, int], ...]:
    counts = Counter(path.split("/", 1)[0] for path in paths if "/" in path)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return tuple(ranked[:LARGEST_FOLDERS])


def oversized_index(
    paths: Sequence[str],
    configured: Sequence[str],
    limit: int,
    tracked: frozenset[str] = frozenset(),
) -> OversizedIndex | None:
    if len(paths) <= limit:
        return None
    largest = _top_folders(paths)
    taken = {value.casefold() for value in configured}
    remaining = len(paths) - sum(count for name, count in largest if name.casefold() in taken)
    chosen: list[str] = []
    noise = [
        item for item in largest if item[0].startswith(HIDDEN_PREFIX) and item[0] not in tracked
    ]
    needed = [item for item in largest if item not in noise]
    for name, count in noise:
        if name.casefold() not in taken:
            chosen.append(name)
            taken.add(name.casefold())
            remaining -= count
    for name, count in needed:
        if remaining <= limit:
            break
        if name.casefold() not in taken:
            chosen.append(name)
            taken.add(name.casefold())
            remaining -= count
    return OversizedIndex(len(paths), limit, largest, (*configured, *chosen))


class IndexTooLarge(DomainFailure):
    def __init__(self, oversized: OversizedIndex) -> None:
        self.oversized = oversized
        self.reason = oversized.reason
        self.advice = oversized.advice
        super().__init__(english(self.reason), english(self.advice))
