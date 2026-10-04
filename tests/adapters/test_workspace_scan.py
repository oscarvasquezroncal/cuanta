from __future__ import annotations

import os
from pathlib import Path

import pytest

from cuanta.adapters.instinct.heuristic import HeuristicInstinct
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.instinct import DecisionMaker
from cuanta.application.mandate import MandateService
from tests.adapters.test_workspace_links import linked_folder
from tests.real_run import REAL_RUN_GITIGNORE, REAL_RUN_NOISE, real_project_tree


def _write(root: Path, relative: str, text: str = "content\n") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def noisy(paths: tuple[str, ...]) -> list[str]:
    return [path for path in paths if path.split("/")[0] in REAL_RUN_NOISE]


def test_snapshot_scan_prunes_ignored_folders_and_keeps_ignored_files(tmp_path: Path) -> None:
    sources = real_project_tree(tmp_path)
    _write(tmp_path, ".gitignore", REAL_RUN_GITIGNORE + ".env\n")
    _write(tmp_path, ".env", "SECRET=1\n")
    scan = LocalWorkspace(tmp_path).scan(frozenset(), collect_files=True, all_files=True)
    assert noisy(scan.files) == []
    assert set(sources) | {".env", ".gitignore"} == set(scan.files)
    assert scan.ignored == frozenset({".env"})
    assert set(scan.modes) == set(scan.files)


def test_snapshot_scan_leaves_out_an_embedded_checkout_git_does_not_track(
    tmp_path: Path,
) -> None:
    sources = real_project_tree(tmp_path)
    _write(tmp_path, ".gitignore", ".venv312/\n.deploy-backups/\ntmp/\n")
    _write(tmp_path, ".git/HEAD", "ref: refs/heads/main\n")
    scan = LocalWorkspace(tmp_path).scan(frozenset(), collect_files=True, all_files=True)
    assert [path for path in scan.files if path.startswith(".odoo_ref/")] == []
    assert set(sources) | {".gitignore"} == set(scan.files)


def test_snapshot_scan_never_lists_inside_ignored_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_project_tree(tmp_path)
    root = tmp_path.resolve()
    listed: list[str] = []
    original = os.scandir

    def recording(path: str) -> object:
        listed.append(Path(path).resolve().relative_to(root).as_posix())
        return original(path)

    monkeypatch.setattr(os, "scandir", recording)
    LocalWorkspace(tmp_path).scan(frozenset(), collect_files=True, all_files=True)
    assert "creator/routers" in listed
    ignored = [name for name in REAL_RUN_NOISE if name != ".venv_ci"]
    assert [path for path in listed if path.split("/")[0] in ignored] == []
    assert [path for path in listed if path.startswith(".venv_ci/")] == []


def test_detection_scan_counts_only_what_git_keeps_and_still_finds_rulebooks(
    tmp_path: Path,
) -> None:
    real_project_tree(tmp_path)
    _write(tmp_path, "data/.gitignore", "*\n!.gitignore\n")
    _write(tmp_path, "data/export.py")
    _write(tmp_path, "creator/.gitignore", "CLAUDE.md\n")
    scan = LocalWorkspace(tmp_path).scan(frozenset(), collect_files=True)
    assert scan.file_count == 4
    assert scan.files == (
        "creator/__init__.py",
        "creator/routers/consult.py",
        "creator/services/opus_interpreter.py",
        "tests/test_consult.py",
    )
    assert scan.nested_claude_md == ("creator/CLAUDE.md",)
    assert scan.test_files.get("python") == 1


def test_scan_exclusions_ignore_case_and_take_paths_and_globs(tmp_path: Path) -> None:
    for name in ("src/a.py", "Vendor_Extra/lib.py", "docs/private.py", "src/generated/file.py"):
        _write(tmp_path, name)
    exclusions = frozenset({"vendor_extra", "DOCS/Private.py", "src/generated/*"})
    scan = LocalWorkspace(tmp_path).scan(exclusions, collect_files=True)
    assert scan.files == ("src/a.py",)
    assert scan.file_count == 1
    snapshot = LocalWorkspace(tmp_path).scan(exclusions, collect_files=True, all_files=True)
    assert snapshot.files == ("src/a.py",)


def test_detection_scan_never_follows_a_folder_link(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _write(outside, "secret.py")
    root = tmp_path / "project"
    _write(root, "app.py")
    try:
        linked_folder(root / "linked", outside)
    except OSError as error:
        pytest.skip(f"folder links are unavailable to this account: {error}")
    scan = LocalWorkspace(root).scan(frozenset(), collect_files=True)
    assert scan.files == ("app.py",)
    assert scan.file_count == 1


def test_the_git_view_comes_from_the_original_project_for_a_copy(tmp_path: Path) -> None:
    original = tmp_path / "original"
    copy = tmp_path / "copy"
    for root in (original, copy):
        _write(root, "app.py")
        _write(root, "notes.py")
    _write(original, ".git/info/exclude", "notes.py\n")
    assert LocalWorkspace(copy).scan(frozenset(), collect_files=True).files == (
        "app.py",
        "notes.py",
    )
    scan = LocalWorkspace(copy, original).scan(frozenset(), collect_files=True)
    assert scan.files == ("app.py",)


def test_run_start_keeps_no_copy_of_files_git_ignores(tmp_path: Path) -> None:
    _write(tmp_path, ".gitignore", ".env\n")
    _write(tmp_path, ".env", "SECRET=1\n")
    _write(tmp_path, "app.py", "value = 1\n")
    ledger = MemoryLedger()
    clock = FixedClock()
    decisions = DecisionMaker(HeuristicInstinct(), ledger, clock.now_iso)
    service = MandateService(LocalWorkspace(tmp_path), ledger, decisions, clock.now_iso)
    hashes = service.snapshot("RUN", "start")
    assert set(hashes) == {".env", ".gitignore", "app.py"}
    blobs = tmp_path / ".cuanta" / "blobs"
    assert (blobs / hashes["app.py"]).is_file()
    assert not (blobs / hashes[".env"]).exists()
