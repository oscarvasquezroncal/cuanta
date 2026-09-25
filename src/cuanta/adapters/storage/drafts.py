from __future__ import annotations

import json
import re
from pathlib import Path

from cuanta.domain.drafts import Draft

SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class FileDraftStore:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def _path(self, draft_id: str) -> Path | None:
        if not SAFE_ID.match(draft_id):
            return None
        return self._directory / f"{draft_id}.json"

    def _read(self, path: Path) -> Draft | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        story, updated = data.get("story"), data.get("updated_at")
        if not isinstance(story, str) or not isinstance(updated, str):
            return None
        return Draft(path.stem, story, updated)

    def all(self) -> list[Draft]:
        if not self._directory.is_dir():
            return []
        found = (self._read(path) for path in sorted(self._directory.glob("*.json")))
        return [draft for draft in found if draft is not None]

    def get(self, draft_id: str) -> Draft | None:
        path = self._path(draft_id)
        return self._read(path) if path is not None and path.is_file() else None

    def put(self, draft: Draft) -> None:
        path = self._path(draft.id)
        if path is None:
            return
        self._directory.mkdir(parents=True, exist_ok=True)
        payload = {"story": draft.story, "updated_at": draft.updated_at}
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def remove(self, draft_id: str) -> None:
        path = self._path(draft_id)
        if path is not None:
            path.unlink(missing_ok=True)
