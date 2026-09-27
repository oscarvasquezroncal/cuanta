from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.bootstrap import Container
from cuanta.domain.code_index import IndexedFile, IndexHistory, IndexReport
from cuanta.domain.index_facts import (
    agent_note,
    history_prior,
    history_row,
    report_facts,
    revalidate_fact,
)
from cuanta.domain.ledger import Run, Snapshot


def _file(text: str, path: str = "globals.css") -> IndexedFile:
    return IndexedFile(path, hashlib.sha256(text.encode()).hexdigest(), "css", len(text))


def test_original_line_range_survives_unrelated_changes_but_not_changed_anchor() -> None:
    text = "".join(f"line {number}\n" for number in range(1, 1301))
    report = IndexReport(
        "run/report.md", "Fix globals.css:1190-1225 before motion.", (("globals.css", text),)
    )
    (row,) = report_facts(report)
    assert not row.stale and row.line == 1190 and row.end_line == 1225
    unrelated = text.replace("line 10\n", "other content\n")
    validated = revalidate_fact(row, _file(unrelated), unrelated)
    assert not validated.stale and validated.source_hash != row.source_hash
    assert validated.target == row.target and validated.provenance == row.provenance
    changed = unrelated.replace("line 1200\n", "new motion\n")
    stale = revalidate_fact(validated, _file(changed), changed)
    assert stale.stale and stale.target == row.target and stale.text == row.text


def test_report_without_original_source_cannot_become_current_fact() -> None:
    (row,) = report_facts(IndexReport("old.md", "src/cart.ts:2\u20133 failed."))
    text = "a\nb\nc\n"
    assert row.stale and row.target == ""
    assert revalidate_fact(row, _file(text, "src/cart.ts"), text).stale
    assert report_facts(IndexReport("bad.md", "../cart.ts:2 and cart.ts:3-2")) == ()


def test_notes_require_valid_current_anchor_and_preserve_provenance() -> None:
    text = "value = 1\nother = 2\n"
    row = agent_note(_file(text, "cart.py"), text, "Owns totals", 1)
    assert row.relation == "note" and row.end_line == 1
    assert not revalidate_fact(row, _file(text, "cart.py"), text).stale
    assert revalidate_fact(row, None, None).stale
    assert revalidate_fact(row, _file("changed", "cart.py"), text).stale
    with pytest.raises(ValueError, match="current source and a valid anchor"):
        agent_note(_file(text), "different", "note", 1)
    with pytest.raises(ValueError, match="current source and a valid anchor"):
        agent_note(_file(text), text, "note", 3)
    with pytest.raises(ValueError, match="current source and a valid anchor"):
        agent_note(_file(text), text, " ")


def test_history_prior_decays_and_deduplicates_actions_by_run() -> None:
    record = IndexHistory("cart.py", "run", "bug", "read", "2026-01-01T00:00:00Z", "accepted", 1)
    rows = (history_row(record, "hash"), history_row(replace(record, action="edit"), "hash"))
    assert history_prior(rows, "bug", "2026-01-01T00:00:00Z") == 0.75
    assert history_prior(rows, "bug", "2026-01-31T00:00:00Z") == 0.375
    assert history_prior(rows, "feature", "2026-01-01T00:00:00Z") == 0
    assert history_prior(rows, "bug", "unknown") == 0
    assert history_prior((replace(rows[0], text="invalid"),), "bug", "2026-01-01") == 0


def test_index_persists_and_revalidates_notes_on_update(tmp_path: Path) -> None:
    path = tmp_path / "cart.py"
    path.write_text("total = 1\nother = 2\n", encoding="utf-8")
    container = Container.for_project(tmp_path)
    service = container.index_service()
    try:
        service.update()
        note = service.note("cart.py", "Total source", 1)
        path.write_text("total = 1\nother = 3\n", encoding="utf-8")
        service.update()
        (current,) = service.index.rows("notes")
        assert not current.stale and current.target == note.target
        path.write_text("total = 4\nother = 3\n", encoding="utf-8")
        service.update()
        (stale,) = service.index.rows("notes")
        assert stale.stale and stale.provenance == note.provenance
        with pytest.raises(ValueError, match="indexed source file"):
            service.note("missing.py", "No source")
    finally:
        service.close()
        container.close()


def test_unverified_stored_report_is_retained_stale_without_modifying_it(tmp_path: Path) -> None:
    (tmp_path / "cart.py").write_text("total = 1\n", encoding="utf-8")
    report = tmp_path / ".cuanta" / "runs" / "old-run" / "report.md"
    report.parent.mkdir(parents=True)
    report.write_text("Finding cart.py:1 requires revalidation.\n", encoding="utf-8")
    original = report.read_bytes()
    container = Container.for_project(tmp_path)
    service = container.index_service()
    try:
        service.update()
        (row,) = service.index.rows("notes")
        assert row.stale and row.source_hash == "unverified" and row.target == ""
        service.update()
        assert service.index.rows("notes") == (row,)
        assert report.read_bytes() == original
        assert not (tmp_path / ".cuanta" / "ledger.db").exists()
    finally:
        service.close()
        container.close()


def test_index_does_not_migrate_an_old_ledger(tmp_path: Path) -> None:
    (tmp_path / "cart.py").write_text("total = 1\n", encoding="utf-8")
    state = tmp_path / ".cuanta"
    state.mkdir()
    ledger = state / "ledger.db"
    connection = sqlite3.connect(ledger)
    connection.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    connection.execute("INSERT INTO schema_version VALUES (1)")
    connection.commit()
    connection.close()
    before = ledger.read_bytes()
    container = Container.for_project(tmp_path)
    service = container.index_service()
    try:
        status = service.update()
        assert status.files == 1 and status.history_status == "unavailable:ValueError"
        assert ledger.read_bytes() == before
    finally:
        service.close()
        container.close()


def test_reimport_without_available_source_preserves_original_report_anchor(tmp_path: Path) -> None:
    path = tmp_path / "cart.py"
    path.write_bytes(b"total = 1\nother = 2\n")
    state = tmp_path / ".cuanta"
    with closing(SqliteLedger(state / "ledger.db")) as ledger:
        ledger.add_run(Run("R", "mandate", task_type="bug"))
        ledger.add_snapshots(
            (Snapshot("R", "end", "cart.py", hashlib.sha256(path.read_bytes()).hexdigest()),)
        )
    report = state / "runs" / "R" / "report.md"
    report.parent.mkdir(parents=True)
    report.write_bytes(b"cart.py:1 defines the total.\n")
    container = Container.for_project(tmp_path)
    service = container.index_service()
    try:
        service.update()
        (original,) = service.index.rows("notes")
        assert original.target.startswith("anchor:") and not original.stale
        path.write_bytes(b"total = 1\nother = 3\n")
        service.update()
        (unrelated,) = service.index.rows("notes")
        assert unrelated.target == original.target and not unrelated.stale
        path.write_bytes(b"total = 4\nother = 3\n")
        service.update()
        (changed,) = service.index.rows("notes")
        assert changed.target == original.target and changed.stale
        assert changed.provenance == original.provenance
    finally:
        service.close()
        container.close()


def test_moderate_manifest_verification_commands_include_tsc_lint_build(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"dependencies":{"typescript":"5.0"},"scripts":{"lint":"eslint .","build":"next build","test":"vitest"}}',
        encoding="utf-8",
    )
    (tmp_path / "cart.ts").write_text("export const total = 1;\n", encoding="utf-8")
    (tmp_path / "cart.test.ts").write_text("import { total } from './cart';\n", encoding="utf-8")
    container = Container.for_project(tmp_path)
    service = container.index_service()
    try:
        service.update()
        (link,) = service.index.rows("test_links")
        assert link.target == "cart.ts"
        assert "tsc" in link.text and "npm run lint" in link.text and "npm run build" in link.text
    finally:
        service.close()
        container.close()
