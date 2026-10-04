from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, replace

from cuanta.domain import index_limit
from cuanta.domain.code_index import INDEX_TABLES, IndexedFile, IndexRow, IndexStatus, IndexTable
from cuanta.domain.detection import is_source_file
from cuanta.domain.index_facts import agent_note, history_row, report_facts, revalidate_fact
from cuanta.domain.index_limit import IndexTooLarge, oversized_index
from cuanta.domain.index_rules import scoped_rules, test_links
from cuanta.ports.code_index import (
    CodeIndex,
    IndexExtractor,
    IndexGraph,
    IndexInventory,
    IndexKnowledge,
)


def indexable_paths(
    inventory: IndexInventory, exclusions: Sequence[str], limit: int | None = None
) -> tuple[str, ...]:
    paths = inventory.paths()
    ceiling = index_limit.INDEX_FILE_LIMIT if limit is None else limit
    oversized = oversized_index(paths, exclusions, ceiling)
    if oversized is None:
        return paths
    tracked = inventory.tracked_folders(tuple(name for name, _ in oversized.largest))
    raise IndexTooLarge(oversized_index(paths, exclusions, ceiling, tracked) or oversized)


class IndexService:
    def __init__(
        self,
        index: CodeIndex,
        inventory: IndexInventory,
        now: Callable[[], str],
        recovered: bool = False,
        extractor: IndexExtractor | None = None,
        graph: IndexGraph | None = None,
        knowledge: IndexKnowledge | None = None,
        verify_commands: Callable[[], tuple[str, ...]] | None = None,
        close_knowledge: Callable[[], None] | None = None,
        exclusions: tuple[str, ...] = (),
        limit: int | None = None,
        reclaimed: Callable[[], int] | None = None,
    ) -> None:
        self.index = index
        self.inventory = inventory
        self._now = now
        self._recovered = recovered
        self._extractor = extractor
        self._graph = graph
        self._knowledge = knowledge
        self._verify_commands = verify_commands
        self._close_knowledge = close_knowledge
        self._exclusions = exclusions
        self._limit = limit
        self._reclaimed = reclaimed

    def update(self) -> IndexStatus:
        started = time.perf_counter()
        paths = indexable_paths(self.inventory, self._exclusions, self._limit)
        before = {item.path: item for item in self.index.files()}
        candidates = self.inventory.candidates(paths)
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
        self._knowledge_rows(candidates, changed, removed)
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
            history_status=meta.get("history_status", ""),
            reclaimed_bytes=self._reclaimed() if self._reclaimed is not None else 0,
        )

    def close(self) -> None:
        try:
            self.index.close()
        finally:
            if self._close_knowledge is not None:
                self._close_knowledge()

    def note(self, path: str, note: str, line: int = 0, end_line: int = 0) -> IndexRow:
        file = next((item for item in self.index.files() if item.path == path), None)
        text = self.inventory.read(path) if file else None
        if file is None or text is None:
            raise ValueError("Notes require an indexed source file")
        row = agent_note(file, text, note, line, end_line)
        self.index.put_rows("notes", (row,))
        return row

    def _knowledge_rows(
        self,
        files: tuple[IndexedFile, ...],
        changed: tuple[IndexedFile, ...],
        removed: tuple[str, ...],
    ) -> None:
        meta = self.index.meta()
        first = meta.get("knowledge_version") != "1"
        selected = files if first else changed
        current = {item.path: item for item in files}
        for file in selected:
            text = self.inventory.read(file.path)
            if text is not None and hashlib.sha256(text.encode()).hexdigest() == file.content_hash:
                self.index.replace_rows("rules", file.path, scoped_rules(file, text))
        command_json = meta.get("verify_commands", "[]")
        if self._verify_commands and (
            first
            or any(
                item.path.rsplit("/", 1)[-1]
                in {"package.json", "pyproject.toml", "go.mod", "Cargo.toml"}
                for item in changed
            )
            or any(
                path.rsplit("/", 1)[-1]
                in {"package.json", "pyproject.toml", "go.mod", "Cargo.toml"}
                for path in removed
            )
        ):
            command_json = json.dumps(self._verify_commands())
            self.index.set_meta({"verify_commands": command_json})
        commands = tuple(str(item) for item in json.loads(command_json))
        if selected or removed:
            links = test_links(files, self.index.rows("edges"), commands)
            grouped: dict[str, list[IndexRow]] = {}
            for row in links:
                grouped.setdefault(row.path, []).append(row)
            previous = {row.path for row in self.index.rows("test_links")}
            for path in sorted(previous | grouped.keys()):
                self.index.replace_rows("test_links", path, grouped.get(path, ()))
        if self._knowledge is not None:
            reports, history = self._knowledge.reports(), self._knowledge.history()
            fingerprint = hashlib.sha256(
                json.dumps(
                    [
                        tuple(asdict(report) for report in reports),
                        tuple(asdict(item) for item in history),
                    ],
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            if meta.get("knowledge_hash") != fingerprint:
                previous_notes = {row.id: row for row in self.index.rows("notes")}
                imported = tuple(row for report in reports for row in report_facts(report))
                self.index.put_rows(
                    "notes",
                    tuple(
                        previous_notes[row.id]
                        if row.id in previous_notes
                        and previous_notes[row.id].target.startswith("anchor:")
                        else row
                        for row in imported
                    ),
                )
                self.index.put_rows(
                    "history",
                    tuple(
                        history_row(
                            item,
                            current[item.path].content_hash
                            if item.path in current
                            else "unverified",
                        )
                        for item in history
                    ),
                )
                self.index.set_meta({"knowledge_hash": fingerprint})
        notes = self.index.rows("notes")
        texts: dict[str, str | None] = {}
        changed_paths = {item.path for item in changed}
        updates: list[IndexRow] = []
        for row in notes:
            if not row.target.startswith("anchor:"):
                if not row.stale:
                    updates.append(replace(row, stale=True))
            elif row.stale or first or row.path in changed_paths:
                if row.path not in texts:
                    texts[row.path] = self.inventory.read(row.path) if row.path in current else None
                validated = revalidate_fact(row, current.get(row.path), texts[row.path])
                if validated != row:
                    updates.append(validated)
        if updates:
            self.index.put_rows("notes", updates)
        self.index.set_meta({"knowledge_version": "1"})

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
