from __future__ import annotations

from dataclasses import dataclass

DRAFT_LIMIT = 10
LIST_LIMIT = 5
TITLE_LIMIT = 48
LAST = "last"


@dataclass(frozen=True, slots=True)
class Draft:
    id: str
    story: str
    updated_at: str

    @property
    def title(self) -> str:
        line = " ".join(self.story.split())
        return line if len(line) <= TITLE_LIMIT else line[: TITLE_LIMIT - 1] + "…"


def newest(drafts: list[Draft], exclude: str = "", limit: int = LIST_LIMIT) -> tuple[Draft, ...]:
    kept = [draft for draft in drafts if draft.id not in {exclude, LAST} and draft.story.strip()]
    kept.sort(key=lambda draft: (draft.updated_at, draft.id), reverse=True)
    return tuple(kept[:limit])


def overflow(drafts: list[Draft], limit: int = DRAFT_LIMIT) -> tuple[str, ...]:
    ordered = sorted(
        (draft for draft in drafts if draft.id != LAST),
        key=lambda draft: (draft.updated_at, draft.id),
        reverse=True,
    )
    return tuple(draft.id for draft in ordered[limit:])
