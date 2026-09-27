from __future__ import annotations

from hashlib import sha256

from cuanta.application.code_index import IndexService
from cuanta.application.run_reports import RunReports
from cuanta.domain.code_index import IndexRow
from cuanta.domain.index_facts import revalidate_fact
from cuanta.ports.workspace import Workspace


class IndexLearning:
    def __init__(self, service: IndexService, workspace: Workspace) -> None:
        self._service = service
        self._reports = RunReports(workspace)
        self._workspace = workspace

    def run(
        self,
        run_id: str,
        notes: tuple[IndexRow, ...] = (),
        report_ids: tuple[str, ...] = (),
    ) -> dict[str, object]:
        service = self._service
        service.update()
        current = {item.path: item for item in service.index.files()}
        before = {row.id for row in service.index.rows("notes")}
        imported = tuple(
            revalidate_fact(row, current.get(row.path), service.inventory.read(row.path))
            for row in notes
            if row.provenance.startswith("agent-note:") and row.target.startswith("anchor:")
        )
        if imported:
            service.index.put_rows("notes", imported)
        rows = service.index.rows("notes")
        digests = {
            "#" + sha256(payload).hexdigest()
            for identifier in {run_id, *report_ids}
            for path in (
                self._reports.report_path(identifier),
                f".cuanta/trials/{identifier}/report.md",
            )
            if (payload := self._workspace.read_bytes(path))
        }
        saved = len(
            {
                (row.path, row.line, row.end_line, row.text, row.target)
                for row in rows
                if row.relation == "finding"
                and any(row.provenance.endswith(digest) for digest in digests)
            }
        )
        result: dict[str, object] = {
            "findings_saved": saved,
            "stale_facts": sum(row.stale for row in rows),
            "notes_saved": len({row.id for row in imported} - before),
        }
        existing = self._reports.meta(run_id) or {}
        existing.pop("index_learning_error", None)
        self._reports.save_meta(run_id, {**existing, "index_learning": result})
        return result
