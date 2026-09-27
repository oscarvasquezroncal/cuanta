from __future__ import annotations

import hashlib
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import replace

from cuanta.domain.code_index import INDEX_TABLES, IndexedFile, IndexRow, IndexStatus, IndexTable
from cuanta.domain.detection import is_source_file
from cuanta.ports.code_index import CodeIndex, IndexExtractor, IndexGraph, IndexInventory


class IndexService:
    def __init__(
        self,
        index: CodeIndex,
        inventory: IndexInventory,
        now: Callable[[], str],
        recovered: bool = False,
        extractor: IndexExtractor | None = None,
        graph: IndexGraph | None = None,
    ) -> None:
        self.index = index
        self.inventory = inventory
        self._now = now
        self._recovered = recovered
        self._extractor = extractor
        self._graph = graph

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
        self._structures(candidates, changed, removed)
        status = self.status()
        return replace(
            status,
            changed=len(changed),
            removed=len(removed),
            elapsed_s=time.perf_counter() - started,
        )

    def status(self) -> IndexStatus:
        files = self.index.files()
        meta = self.index.meta()
        return IndexStatus(
            files=len(files),
            counts=tuple((table, len(self.index.rows(table))) for table in INDEX_TABLES),
            coverage=(
                sum(item.coverage not in {"unsupported", "reduced"} for item in files) / len(files)
                if files
                else 0.0
            ),
            updated_at=meta.get("updated_at", ""),
            recovered=self._recovered,
            content_hash=meta.get("content_hash", ""),
            coverage_by_kind=tuple(sorted(Counter(item.coverage for item in files).items())),
        )

    def close(self) -> None:
        self.index.close()

    def _structures(
        self,
        files: tuple[IndexedFile, ...],
        changed: tuple[IndexedFile, ...],
        removed: tuple[str, ...],
    ) -> None:
        if self._extractor is None:
            return
        selected = files if self.index.meta().get("extractor_version") != "2" else changed
        paths = tuple(item.path for item in files)
        for file in selected:
            self._extract(file, paths)
        if removed or selected:
            self._relink(paths)
        self.index.set_meta({"extractor_version": "2"})
        if self._graph is not None:
            fingerprint = self._graph.fingerprint()
            if selected or removed or self.index.meta().get("graph_hash") != fingerprint:
                self._import_graph(files)
                self.index.set_meta({"graph_hash": fingerprint})
            self._graph.request_refresh(files)

    def _relink(self, paths: tuple[str, ...]) -> None:
        extractor = self._extractor
        if extractor is None:
            return
        declarations: dict[str, list[IndexRow]] = {}
        for symbol in self.index.rows("symbols"):
            if symbol.relation in {"import", "reexport"} and not symbol.stale:
                declarations.setdefault(symbol.path, []).append(symbol)
        for path, imports in declarations.items():
            modules = {row.text for row in imports}
            retained = tuple(
                row
                for row in self.index.rows("edges", path)
                if row.provenance != "ast"
                or row.text not in modules
                or row.relation not in {"imports", "exports"}
            )
            resolved = tuple(
                replace(
                    row,
                    id=f"{row.id}:resolved",
                    target=extractor.resolve(path, row.text, paths),
                    relation="exports" if row.relation == "reexport" else "imports",
                )
                for row in imports
            )
            previous = self.index.rows("edges", path)
            if set(previous) != {*retained, *resolved}:
                self.index.replace_rows("edges", path, (*retained, *resolved))

    def _extract(self, file: IndexedFile, paths: tuple[str, ...]) -> None:
        extractor = self._extractor
        if extractor is None:
            return
        text = self.inventory.read(file.path)
        if text is None or hashlib.sha256(text.encode()).hexdigest() != file.content_hash:
            self.index.replace_files((replace(file, coverage="reduced"),), ())
            return
        structure = extractor.extract(file, text, paths)
        self.index.replace_rows("symbols", file.path, structure.symbols)
        self.index.replace_rows("edges", file.path, structure.edges)
        coverage = structure.coverage
        if coverage == "unsupported" and not is_source_file(file.path):
            coverage = "input"
        self.index.replace_files((replace(file, coverage=coverage),), ())

    def _import_graph(self, files: tuple[IndexedFile, ...]) -> None:
        graph = self._graph
        if graph is None:
            return
        structure = graph.records(files)
        tables: tuple[tuple[IndexTable, tuple[IndexRow, ...]], ...] = (
            ("symbols", structure.symbols),
            ("edges", structure.edges),
        )
        for table, rows in tables:
            existing = self.index.rows(table)
            affected = {row.path for row in rows} | {
                row.path for row in existing if row.provenance.startswith("graphify:")
            }
            grouped: dict[str, list[IndexRow]] = {}
            for row in existing:
                if row.path in affected and not row.provenance.startswith("graphify:"):
                    grouped.setdefault(row.path, []).append(row)
            for row in rows:
                grouped.setdefault(row.path, []).append(row)
            for file in files:
                if file.path in affected:
                    records = {row.id: row for row in grouped.get(file.path, [])}
                    self.index.replace_rows(table, file.path, tuple(records.values()))


def inventory_hash(files: tuple[IndexedFile, ...]) -> str:
    value = "\n".join(
        f"{item.path}\0{item.content_hash}" for item in sorted(files, key=lambda f: f.path)
    )
    return hashlib.sha256(value.encode()).hexdigest()
