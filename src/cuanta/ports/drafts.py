from __future__ import annotations

from typing import Protocol

from cuanta.domain.drafts import Draft


class DraftStore(Protocol):
    def all(self) -> list[Draft]: ...

    def get(self, draft_id: str) -> Draft | None: ...

    def put(self, draft: Draft) -> None: ...

    def remove(self, draft_id: str) -> None: ...
