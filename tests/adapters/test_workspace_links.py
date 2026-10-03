from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from cuanta.adapters.system.workspace import LocalWorkspace


def linked_folder(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        winapi: Any = importlib.import_module("_winapi")
        winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def test_plain_paths_inside_the_project_are_not_redirected(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "notes.md").write_text("x", encoding="utf-8")
    workspace = LocalWorkspace(root)
    assert not workspace.redirected("docs/notes.md")
    assert not workspace.redirected("docs/missing.md")
    assert not workspace.redirected("absent/deeper/file.md")
    (root / "notes").write_text("a file, not a folder", encoding="utf-8")
    assert not workspace.redirected("notes/MANDATE_TEMPLATE.md")


def test_a_folder_link_inside_the_project_is_followed_and_one_outside_is_not(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    (root / "documentation").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_folder(root / "docs", root / "documentation")
    linked_folder(root / "shared", outside)
    workspace = LocalWorkspace(root)
    assert not workspace.redirected("docs/MANDATE_TEMPLATE.md")
    assert workspace.redirected("shared/MANDATE_TEMPLATE.md")
    assert workspace.redirected("shared")
    assert workspace.redirected("../outside/MANDATE_TEMPLATE.md")


def test_a_file_link_is_redirected_even_when_it_points_inside_or_nowhere(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    (root / "docs").mkdir(parents=True)
    (root / "real.md").write_text("x", encoding="utf-8")
    try:
        os.symlink(root / "real.md", root / "docs" / "inside.md")
        os.symlink(tmp_path / "nowhere.md", root / "docs" / "dangling.md")
    except OSError as error:
        pytest.skip(f"symbolic links are not available here: {error}")
    workspace = LocalWorkspace(root)
    assert workspace.redirected("docs/inside.md")
    assert workspace.redirected("docs/dangling.md")
    assert not (tmp_path / "nowhere.md").exists()
