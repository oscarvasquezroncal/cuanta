from __future__ import annotations

from pathlib import Path

import pytest

from cuanta.bootstrap import Container
from cuanta.domain.code_index import IndexedFile
from cuanta.domain.index_facts import agent_note, anchor_hash
from cuanta.domain.index_rebuild import IndexBusy, carried, rebuild_backup, rebuild_leftover
from cuanta.tui.i18n import Catalog

SOURCE = "value = 1\nother = 2\n"


@pytest.mark.parametrize(
    ("name", "leftover"),
    [
        ("index.db-journal", True),
        ("index.db-wal", True),
        ("index.db-shm", True),
        ("index.db.rebuild-0123.bak", True),
        ("index.db.rebuild-0123.bak-journal", True),
        ("index.db.corrupt-0123.bak", True),
        ("index.db.new-0123", True),
        ("index.db.new-0123-journal", True),
        ("index.db", False),
        ("index.db.keep", False),
        ("index.dbx", False),
        ("ledger.db", False),
        ("ledger.db-wal", False),
        ("config.toml", False),
    ],
)
def test_an_explicit_rebuild_deletes_only_index_leftovers(name: str, leftover: bool) -> None:
    assert rebuild_leftover("index.db", name) is leftover


@pytest.mark.parametrize(
    ("name", "backup"),
    [
        ("index.db.corrupt-0123.bak", True),
        ("index.db.rebuild-0123.bak", True),
        ("index.db.corrupt-0123.bak-journal", False),
        ("index.db.rebuild-0123.bak-wal", False),
        ("index.db.new-0123", False),
        ("index.db", False),
        ("index.db-journal", False),
        ("ledger.db.corrupt-0123.bak", False),
    ],
)
def test_only_old_index_backups_are_read_for_notes(name: str, backup: bool) -> None:
    assert rebuild_backup("index.db", name) is backup


def test_agent_notes_and_economy_summaries_are_carried_and_derived_facts_are_not() -> None:
    file = IndexedFile("main.py", anchor_hash(SOURCE), "python", len(SOURCE))
    assert carried(agent_note(file, SOURCE, "keep me", 1).provenance)
    assert carried("economy-summary:engine:economy")
    assert not carried("ledger:R")
    assert not carried("run-report:R#digest")
    assert not carried("ast")


def test_a_rebuilt_index_keeps_agent_notes_fresh_after_the_first_update(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(SOURCE, encoding="utf-8")
    container = Container.for_project(tmp_path)
    service = container.index_service()
    try:
        service.update()
        note = service.note("main.py", "keep me", 1)
    finally:
        service.close()
    database = tmp_path / ".cuanta" / "index.db"
    before = database.stat().st_size
    rebuilt = container.index_service(rebuild=True)
    try:
        status = rebuilt.update()
        assert rebuilt.index.rows("notes") == (note,)
        assert status.reclaimed_bytes == max(0, before - database.stat().st_size)
        assert rebuilt.status().reclaimed_bytes == status.reclaimed_bytes
    finally:
        rebuilt.close()
        container.close()
    names = (item.name for item in (tmp_path / ".cuanta").iterdir())
    assert sorted(name for name in names if name != "index.db.hold") == ["index.db"]


def test_a_clean_rebuild_reports_only_the_space_it_returned(tmp_path: Path) -> None:
    for number in range(20):
        source = f"def run_{number}() -> int:\n    return {number}\n"
        (tmp_path / f"module_{number}.py").write_text(source, encoding="utf-8")
    container = Container.for_project(tmp_path)
    service = container.index_service()
    try:
        service.update()
    finally:
        service.close()
    database = tmp_path / ".cuanta" / "index.db"
    before = database.stat().st_size
    rebuilt = container.index_service(rebuild=True)
    try:
        status = rebuilt.update()
        after = database.stat().st_size
        assert status.reclaimed_bytes == max(0, before - after)
        assert status.reclaimed_bytes < before
    finally:
        rebuilt.close()
        container.close()


def test_the_busy_refusal_reads_in_both_languages() -> None:
    refused = IndexBusy(".cuanta/index.db")
    assert refused.message == (
        ".cuanta/index.db is open in another process, so it was not rebuilt; the index is unchanged"
    )
    assert refused.hint == (
        "finish or stop the cuanta run that uses it (its index tools keep it open),"
        " then run cuanta index --rebuild again"
    )
    spanish = Catalog("es")
    assert spanish.message(refused.reason) == (
        ".cuanta/index.db está abierto en otro proceso, así que no se reconstruyó;"
        " el índice no cambió"
    )
    assert spanish.message(refused.advice) == (
        "termina o detén la ejecución de cuanta que lo usa (sus herramientas de índice lo"
        " mantienen abierto) y vuelve a ejecutar cuanta index --rebuild"
    )
