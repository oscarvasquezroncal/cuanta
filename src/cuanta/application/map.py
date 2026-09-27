from __future__ import annotations

from dataclasses import dataclass

from cuanta.application.index_read import IndexRead
from cuanta.domain.code_index import HandlingCard, IndexRow, IndexStatus, SearchHit, index_path


@dataclass(frozen=True, slots=True)
class MapStatus:
    status: IndexStatus
    stale_facts: int
    semantic: bool = False


@dataclass(frozen=True, slots=True)
class MapFile:
    card: HandlingCard
    impact: tuple[IndexRow, ...]
    facts: tuple[IndexRow, ...]
    stale: tuple[IndexRow, ...]


class MapQuery:
    def __init__(self, reader: IndexRead) -> None:
        self.reader = reader

    def status(self) -> MapStatus:
        status = self.reader.service.update()
        return MapStatus(status, len(self.reader.facts(stale=True)))

    def search(self, query: str) -> tuple[SearchHit, ...]:
        self.reader.update()
        return self.reader.find(query)

    def file(self, path: str) -> MapFile:
        path = index_path(path)
        self.reader.update()
        neighbours = {other for other, _ in self.reader.impact(path)}
        files = {file.path: file for file in self.reader.service.index.files()}
        impact = tuple(
            row
            for row in self.reader.service.index.rows("edges")
            if not row.stale
            and row.path in files
            and row.source_hash == files[row.path].content_hash
            and (
                (row.path == path and row.target in neighbours)
                or (row.target == path and row.path in neighbours)
            )
        )
        return MapFile(
            self.reader.card(path), impact, self.reader.facts(path), self.reader.facts(path, True)
        )

    def revalidate(self) -> MapStatus:
        return self.status()

    def close(self) -> None:
        self.reader.close()
