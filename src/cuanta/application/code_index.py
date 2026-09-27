from __future__ import annotations

import hashlib
import time
from collections.abc import Callable

from cuanta.domain.code_index import INDEX_TABLES, IndexedFile, IndexStatus
from cuanta.ports.code_index import CodeIndex, IndexInventory


class IndexService:
    def __init__(
        self,
        index: CodeIndex,
        inventory: IndexInventory,
        now: Callable[[], str],
        recovered: bool = False,
    ) -> None:
        self.index = index
        self.inventory = inventory
        self._now = now
        self._recovered = recovered

    def update(self) -> IndexStatus:
        started = time.perf_counter()
        before = {item.path: item for item in self.index.files()}
        candidates = self.inventory.candidates()
        current = {item.path: item for item in candidates}
        changed = tuple(
            item
            for item in candidates
            if item.path not in before or before[item.path].content_hash != item.content_hash
        )
        removed = tuple(sorted(before.keys() - current.keys()))
        fingerprint = inventory_hash(candidates)
        if changed or removed or self.index.meta().get("content_hash") != fingerprint:
            self.index.replace_files(changed, removed)
            self.index.set_meta({"updated_at": self._now(), "content_hash": fingerprint})
        status = self.status()
        return IndexStatus(
            status.files,
            len(changed),
            len(removed),
            status.counts,
            status.coverage,
            status.updated_at,
            time.perf_counter() - started,
            self._recovered,
            status.content_hash,
        )

    def status(self) -> IndexStatus:
        files = self.index.files()
        meta = self.index.meta()
        return IndexStatus(
            files=len(files),
            counts=tuple((table, len(self.index.rows(table))) for table in INDEX_TABLES),
            coverage=1.0 if files else 0.0,
            updated_at=meta.get("updated_at", ""),
            recovered=self._recovered,
            content_hash=meta.get("content_hash", ""),
        )

    def close(self) -> None:
        self.index.close()


def inventory_hash(files: tuple[IndexedFile, ...]) -> str:
    value = "\n".join(
        f"{item.path}\0{item.content_hash}" for item in sorted(files, key=lambda f: f.path)
    )
    return hashlib.sha256(value.encode()).hexdigest()
