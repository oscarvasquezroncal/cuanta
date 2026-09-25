from __future__ import annotations

from pathlib import Path

from cuanta.adapters.storage.drafts import FileDraftStore
from cuanta.application.drafts import Drafts
from cuanta.domain.drafts import DRAFT_LIMIT, LIST_LIMIT, Draft


def clocked(tmp_path: Path) -> tuple[Drafts, FileDraftStore, list[int]]:
    ticks = [0]

    def now() -> str:
        ticks[0] += 1
        return f"2026-09-24T12:00:{ticks[0]:02d}Z"

    store = FileDraftStore(tmp_path / "drafts")
    return Drafts(store, now), store, ticks


def test_autosave_lists_newest_first_and_skips_the_current(tmp_path: Path) -> None:
    drafts, _, _ = clocked(tmp_path)
    drafts.autosave("da", "first story")
    drafts.autosave("db", "second story")
    drafts.autosave("dc", "third story")
    listed = drafts.listed(current="dc")
    assert [draft.id for draft in listed] == ["db", "da"]
    assert drafts.load("da") == "first story"


def test_empty_autosave_removes_the_draft(tmp_path: Path) -> None:
    drafts, store, _ = clocked(tmp_path)
    drafts.autosave("da", "story")
    drafts.autosave("da", "   ")
    assert store.get("da") is None


def test_launch_keeps_the_last_request_and_drops_its_draft(tmp_path: Path) -> None:
    drafts, store, _ = clocked(tmp_path)
    drafts.autosave("da", "story")
    drafts.launched("da", "story")
    assert store.get("da") is None
    assert drafts.last() == "story"
    assert drafts.listed() == ()


def test_old_drafts_are_trimmed(tmp_path: Path) -> None:
    drafts, store, _ = clocked(tmp_path)
    for index in range(DRAFT_LIMIT + 3):
        drafts.autosave(f"d{index:02d}", f"story {index}")
    assert len(store.all()) == DRAFT_LIMIT
    assert store.get("d00") is None
    assert len(drafts.listed()) == LIST_LIMIT


def test_unsafe_ids_and_corrupt_files_are_ignored(tmp_path: Path) -> None:
    drafts, store, _ = clocked(tmp_path)
    drafts.autosave("../escape", "story")
    assert not (tmp_path / "escape.json").exists()
    (tmp_path / "drafts").mkdir(exist_ok=True)
    (tmp_path / "drafts" / "broken.json").write_text("{nope", encoding="utf-8")
    assert store.all() == []


def test_titles_are_single_line_and_short() -> None:
    draft = Draft("d1", "line one\nline two " + "x" * 80, "t")
    assert "\n" not in draft.title
    assert len(draft.title) <= 48
