from __future__ import annotations

import hashlib
import os
import shutil
import stat
import struct
import subprocess
from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cuanta.adapters.system.index_inventory import LocalIndexInventory
from tests.real_run import REAL_RUN_NOISE, real_project_tree

GIT = shutil.which("git")
RARE_PATTERNS: dict[str, tuple[dict[str, str], tuple[str, ...]]] = {
    "anchored_doublestar_allowlist": (
        {".gitignore": "/**\n!/**/\n!/**/*.py\n"},
        ("src/a.py", "src/data.json", "src/deep/b.py", "src/deep/c.json", "top.json"),
    ),
    "anchored_doublestar_folders": (
        {".gitignore": "/**/\n!/src/\n"},
        ("src/a.py", "src/deep/a.py", "other/x.py", "top.py"),
    ),
    "nested_anchored_doublestar": (
        {"sub/.gitignore": "/**\n!/**/\n!/**/*.py\n"},
        ("sub/src/a.py", "sub/src/data.json", "sub/top.json", "keep.json"),
    ),
    "triple_star": ({".gitignore": "a/***/b\n"}, ("a/b", "a/x/b", "a/x/y/b", "c.py")),
    "doubled_trailing_slash": ({".gitignore": "docs//\n"}, ("docs/a.md", "src/b.py")),
}


def _write(root: Path, relative: str, text: str = "content\n") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def git_index(paths: Iterable[str]) -> bytes:
    names = sorted(path.encode() for path in paths)
    entries = b""
    for name in names:
        fixed = struct.pack(">10I", 0, 0, 0, 0, 0, 0, 0o100644, 0, 0, 0) + bytes(20)
        entry = fixed + struct.pack(">H", min(len(name), 0x0FFF)) + name
        entries += entry + b"\0" * (8 - len(entry) % 8)
    return struct.pack(">4sII", b"DIRC", 2, len(names)) + entries + bytes(20)


def _git(root: Path, home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = {
        **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(home),
        "USERPROFILE": str(home),
    }
    environment.pop("XDG_CONFIG_HOME", None)
    return subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        env=environment,
    )


def _listed_by_git(root: Path, home: Path) -> list[str]:
    listed = _git(root, home, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    return sorted(path for path in listed.stdout.split("\0") if path)


def _walked(root: Path, home: Path) -> list[str]:
    from cuanta.adapters.system.git_files import load_git_view, walk_visible

    view = load_git_view(root, home, os.environ)
    return sorted(
        item.path for item in walk_visible(root, view, lambda name, path: False) if not item.ignored
    )


def test_the_real_run_tree_is_indexed_at_its_own_count(tmp_path: Path) -> None:
    expected = real_project_tree(tmp_path)
    inventory = LocalIndexInventory(tmp_path)
    assert tuple(file.path for file in inventory.candidates()) == expected
    assert inventory.paths() == expected
    assert tuple(file.path for file in inventory.candidates(inventory.paths())) == expected


def test_nothing_under_the_real_run_noise_is_even_listed(
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
    LocalIndexInventory(tmp_path).candidates()
    assert "creator/routers" in listed
    ignored = [name for name in REAL_RUN_NOISE if name != ".venv_ci"]
    assert [path for path in listed if path.split("/")[0] in ignored] == []
    assert [path for path in listed if path.startswith(".venv_ci/")] == []


def test_a_virtualenv_is_left_out_by_its_marker_whatever_its_name(tmp_path: Path) -> None:
    _write(tmp_path, "app/main.py")
    _write(tmp_path, "env312/pyvenv.cfg", "home = /usr/bin\n")
    _write(tmp_path, "env312/lib/python3.12/site-packages/dep/core.py")
    _write(tmp_path, "tools/conda/conda-meta/history", "")
    _write(tmp_path, "tools/conda/lib/site.py")
    _write(tmp_path, "tools/build.py")
    inventory = LocalIndexInventory(tmp_path)
    assert inventory.paths() == ("app/main.py", "tools/build.py")


def test_nested_gitignore_info_exclude_and_global_excludes_are_honored(
    tmp_path: Path, isolated_user_dirs: Path
) -> None:
    root = tmp_path / "project"
    for name in (
        "app/main.py",
        "app/generated/model.py",
        "app/generated/README.md",
        "notes.md",
        "settings.local.py",
        "README.md",
    ):
        _write(root, name)
    _write(root, "app/.gitignore", "generated/\n")
    _write(root, ".git/HEAD", "ref: refs/heads/main\n")
    _write(root, ".git/info/exclude", "notes.md\n")
    excludes = _write(isolated_user_dirs, "my excludes", "*.local.py\n")
    _write(isolated_user_dirs, ".gitconfig", f'[core]\n\texcludesFile = "{excludes.as_posix()}"\n')
    assert LocalIndexInventory(root).paths() == ("README.md", "app/main.py")


def test_the_default_global_excludes_file_follows_xdg_config_home(
    tmp_path: Path, isolated_user_dirs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    for name in ("main.py", "scratch.draft.md", "local.secret.md"):
        _write(root, name)
    _write(isolated_user_dirs, ".config/git/ignore", "*.draft.md\n")
    assert LocalIndexInventory(root).paths() == ("local.secret.md", "main.py")
    _write(tmp_path, "xdg/git/ignore", "*.secret.md\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert LocalIndexInventory(root).paths() == ("main.py", "scratch.draft.md")


def test_files_in_the_git_index_stay_even_when_a_pattern_matches(tmp_path: Path) -> None:
    for name in (
        "lib/core.py",
        "lib/other.py",
        "lib/deep/kept.py",
        "lib/deep/dropped.py",
        "src/app.py",
        "src/schema.gen.py",
        "src/other.gen.py",
    ):
        _write(tmp_path, name)
    _write(tmp_path, ".gitignore", "lib/\n*.gen.py\n")
    (tmp_path / ".git").mkdir()
    tracked = ("lib/core.py", "lib/deep/kept.py", "src/schema.gen.py", ".gitignore")
    (tmp_path / ".git" / "index").write_bytes(git_index(tracked))
    assert LocalIndexInventory(tmp_path).paths() == (
        "lib/core.py",
        "lib/deep/kept.py",
        "src/app.py",
        "src/schema.gen.py",
    )


def test_the_folders_git_tracks_something_in_are_named(tmp_path: Path) -> None:
    for name in (".github/workflows/release.yml", ".odoo_ref/sale.py", "app.py"):
        _write(tmp_path, name)
    (tmp_path / ".git").mkdir()
    tracked = (".github/workflows/release.yml", "vendored", "app.py")
    (tmp_path / ".git" / "index").write_bytes(git_index(tracked))
    inventory = LocalIndexInventory(tmp_path)
    folders = (".github", ".odoo_ref", "vendored", "app.py", ".git")
    assert inventory.tracked_folders(folders) == frozenset({".github", "vendored", "app.py"})
    assert LocalIndexInventory(tmp_path / "missing").tracked_folders(folders) == frozenset()


def test_a_gitdir_file_and_its_common_dir_are_followed(tmp_path: Path) -> None:
    root = tmp_path / "worktree"
    store = tmp_path / "store"
    for name in ("lib/core.py", "lib/other.py", "notes.md", "main.py"):
        _write(root, name)
    _write(root, ".gitignore", "lib/\n")
    _write(root, ".git", "gitdir: ../store/worktrees/feature\n")
    _write(store, "worktrees/feature/commondir", "../..\n")
    (store / "worktrees" / "feature" / "index").write_bytes(git_index(("lib/core.py",)))
    _write(store, "info/exclude", "notes.md\n")
    assert LocalIndexInventory(root).paths() == ("lib/core.py", "main.py")


def test_ignored_rulebooks_at_visible_levels_stay(tmp_path: Path, isolated_user_dirs: Path) -> None:
    sources = real_project_tree(tmp_path)
    _write(isolated_user_dirs, ".config/git/ignore", "CLAUDE.md\n")
    assert LocalIndexInventory(tmp_path).paths() == sources


def test_index_exclusions_ignore_case_and_take_paths_and_globs(tmp_path: Path) -> None:
    for name in ("src/main.py", "Vendor_Extra/lib.py", "docs/Private.md", "src/generated/file.ts"):
        _write(tmp_path, name)
    inventory = LocalIndexInventory(
        tmp_path, frozenset({"vendor_extra/", "DOCS/private.md", "src\\generated\\*"})
    )
    assert inventory.paths() == ("src/main.py",)


@pytest.mark.skipif(GIT is None, reason="git is not installed")
def test_the_walker_lists_what_git_lists(tmp_path: Path, isolated_user_dirs: Path) -> None:
    root = tmp_path / "project"
    files = (
        "README.md",
        "pyproject.toml",
        "creator/__init__.py",
        "creator/routers/consult.py",
        "creator/generated/schema.py",
        "creator/generated/keep.py",
        "creator/notes.local.py",
        "lib/core.py",
        "lib/other.py",
        "docs/page_1.md",
        "docs/page_2.md",
        "docs/Guide.MD",
        "docs/notes.draft.md",
        "logs/run.py",
        "src/a b/space.py",
        "src/ñandú.py",
        "tests/test_consult.py",
        "out.txt",
        "keep.txt",
    )
    for name in files:
        _write(root, name)
    _write(root, ".gitignore", "lib/\n*.local.py\nlogs/**\n*.txt\n!keep.txt\n")
    _write(root, "creator/.gitignore", "generated/*\n!generated/keep.py\n")
    _write(isolated_user_dirs, ".config/git/ignore", "*.draft.md\n")
    _git(root, isolated_user_dirs, "init", "-q")
    _write(root, ".git/info/exclude", "docs/page_1*.md\n")
    _git(root, isolated_user_dirs, "add", ".")
    _git(root, isolated_user_dirs, "add", "-f", "lib/core.py")
    listed = _git(
        root,
        isolated_user_dirs,
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
    ).stdout
    truth = sorted(path for path in listed.split("\0") if path)
    assert "lib/core.py" in truth and "lib/other.py" not in truth
    assert "creator/generated/keep.py" in truth and "creator/generated/schema.py" not in truth
    assert "docs/page_2.md" in truth and "docs/page_1.md" not in truth
    assert "docs/Guide.MD" in truth and "docs/notes.draft.md" not in truth
    assert "keep.txt" in truth and "out.txt" not in truth
    from cuanta.adapters.system.git_files import load_git_view, walk_visible

    view = load_git_view(root, isolated_user_dirs, os.environ)
    walked = sorted(
        item.path for item in walk_visible(root, view, lambda name, path: False) if not item.ignored
    )
    assert walked == truth


@pytest.mark.skipif(GIT is None, reason="git is not installed")
@pytest.mark.parametrize("case", sorted(RARE_PATTERNS))
def test_rare_patterns_list_what_git_lists(
    tmp_path: Path, isolated_user_dirs: Path, case: str
) -> None:
    ignores, files = RARE_PATTERNS[case]
    root = tmp_path / "project"
    for name in files:
        _write(root, name)
    for name, text in ignores.items():
        _write(root, name, text)
    _git(root, isolated_user_dirs, "init", "-q")
    assert _walked(root, isolated_user_dirs) == _listed_by_git(root, isolated_user_dirs)


@pytest.mark.skipif(GIT is None, reason="git is not installed")
def test_an_embedded_checkout_drops_out_as_in_git_and_a_submodule_stays_walked(
    tmp_path: Path, isolated_user_dirs: Path
) -> None:
    root = tmp_path / "project"
    home = isolated_user_dirs
    for name in (
        "app.py",
        ".odoo_ref/addons/sale.py",
        ".odoo_ref/README.md",
        "vendored/s.py",
        "vendored/extra.py",
        "legacy/old.py",
        "legacy/new.py",
    ):
        _write(root, name)
    _git(root, home, "init", "-q")
    _git(root, home, "add", "app.py", "legacy/old.py")
    for nested in (".odoo_ref", "vendored", "legacy"):
        _git(root / nested, home, "init", "-q")
    _git(root, home, "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},vendored")
    truth = _listed_by_git(root, home)
    assert ".odoo_ref/" in truth and "vendored" in truth
    assert {"legacy/old.py", "legacy/new.py"} <= set(truth)
    files = {path for path in truth if path != "vendored" and not path.endswith("/")}
    assert _walked(root, home) == sorted({*files, "vendored/extra.py", "vendored/s.py"})
    assert LocalIndexInventory(root).paths() == (
        "app.py",
        "legacy/new.py",
        "legacy/old.py",
        "vendored/extra.py",
        "vendored/s.py",
    )


def test_a_copy_leaves_out_the_checkouts_its_original_leaves_out(tmp_path: Path) -> None:
    original = tmp_path / "original"
    copy = tmp_path / "copy"
    for root in (original, copy):
        for name in ("app.py", "checkout/core.py", "vendored/s.py"):
            _write(root, name)
    _write(original, ".git/HEAD", "ref: refs/heads/main\n")
    (original / ".git" / "index").write_bytes(git_index(("app.py", "vendored")))
    _write(original, "checkout/.git/HEAD", "ref: refs/heads/main\n")
    _write(original, "vendored/.git", "gitdir: ../.git/modules/vendored\n")
    from cuanta.adapters.system.git_files import load_git_view

    view = load_git_view(original, tmp_path / "home", {})
    expected = ("app.py", "vendored/s.py")
    assert LocalIndexInventory(original).paths() == expected
    assert LocalIndexInventory(copy, view=lambda: view).paths() == expected


def test_nested_checkouts_stay_walked_when_the_project_is_not_a_repository(
    tmp_path: Path,
) -> None:
    for name in ("backend/app.py", "frontend/main.ts"):
        _write(tmp_path, name)
    _write(tmp_path, "backend/.git/HEAD", "ref: refs/heads/main\n")
    _write(tmp_path, "frontend/.git/HEAD", "ref: refs/heads/main\n")
    assert LocalIndexInventory(tmp_path).paths() == ("backend/app.py", "frontend/main.ts")


def test_inventory_tracks_raw_hashes_and_removal(tmp_path: Path) -> None:
    source = _write(tmp_path, "src/main.py", "value = 1\n")
    inventory = LocalIndexInventory(tmp_path)
    first = inventory.candidates()[0]
    assert first.content_hash == hashlib.sha256(source.read_bytes()).hexdigest()
    assert first.size_bytes == len(source.read_bytes())
    assert first.language == "python"
    assert first.provenance == "inventory"
    assert first.coverage == "inventory"
    source.write_text("value = 2\n", encoding="utf-8")
    assert inventory.candidates()[0].content_hash != first.content_hash
    source.unlink()
    assert inventory.candidates() == ()


def test_inventory_includes_docs_styles_configs_and_verification_scripts(tmp_path: Path) -> None:
    names = (
        "src/page.tsx",
        "src/main.go",
        "src/global.css",
        "src/theme.scss",
        "README.md",
        "CLAUDE.md",
        "docs/feature.md",
        "package.json",
        "pyproject.toml",
        "tsconfig.json",
        "scripts/gate.cmd",
        "scripts/gate.ps1",
        "scripts/gate.sh",
        ".github/workflows/test.yml",
        "LICENSE",
    )
    for name in names:
        _write(tmp_path, name)
    inventory = LocalIndexInventory(tmp_path)
    assert tuple(file.path for file in inventory.candidates()) == tuple(sorted(names))
    assert inventory.read("src\\page.tsx") == "content\n"
    assert {file.language for file in inventory.candidates()} >= {
        "typescript",
        "go",
        "css",
        "scss",
        "markdown",
        "json",
        "toml",
        "batch",
        "powershell",
        "shell",
        "yaml",
        "text",
    }


@pytest.mark.parametrize(
    "relative",
    [
        ".cuanta/private.md",
        ".claude/settings.local.json",
        "graphify-out/graph.json",
        ".git/config.py",
        "node_modules/lib/index.ts",
        ".next/cache/state.json",
        "dist/bundle.js",
        ".venv/lib/site.py",
        "__pycache__/cache.py",
        ".ruff_cache/cache.json",
        "build/output.py",
        "src/app.min.js",
        "src/style.min.css",
        "package-lock.json",
        ".env",
        ".env.local",
        ".env.js",
        "credentials.json",
        "secrets.toml",
        "secrets/config.json",
        ".aws/config.json",
        "image.png",
    ],
)
def test_inventory_excludes_generated_private_and_binary_paths(
    tmp_path: Path, relative: str
) -> None:
    _write(tmp_path, relative)
    inventory = LocalIndexInventory(tmp_path)
    assert inventory.candidates() == ()
    assert inventory.read(relative) is None


def test_inventory_honors_name_path_and_glob_exclusions(tmp_path: Path) -> None:
    for name in ("src/main.py", "vendor_extra/lib.py", "docs/private.md", "src/generated/file.ts"):
        _write(tmp_path, name)
    inventory = LocalIndexInventory(
        tmp_path, frozenset({"vendor_extra", "docs/private.md", "src/generated/*"})
    )
    assert tuple(file.path for file in inventory.candidates()) == ("src/main.py",)


@pytest.mark.parametrize(
    "relative", ["../outside.py", "src/../../outside.py", "/outside.py", "C:\\outside.py"]
)
def test_inventory_rejects_traversal_and_absolute_paths(tmp_path: Path, relative: str) -> None:
    with pytest.raises(ValueError, match="Index paths"):
        LocalIndexInventory(tmp_path).read(relative)


def test_inventory_ignores_null_containing_binary_and_missing_files(tmp_path: Path) -> None:
    (tmp_path / "binary.json").write_bytes(b"binary\x00content")
    inventory = LocalIndexInventory(tmp_path)
    assert inventory.candidates() == ()
    assert inventory.read("binary.json") is None
    assert inventory.read("missing.py") is None


def test_inventory_does_not_follow_file_or_directory_symlinks(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    _write(outside, "private.py")
    root = tmp_path / "project"
    root.mkdir()
    _write(root, "visible.py")
    try:
        (root / "linked.py").symlink_to(outside / "private.py")
        (root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Creating symlinks is unavailable to this account: {error}")
    inventory = LocalIndexInventory(root)
    assert tuple(file.path for file in inventory.candidates()) == ("visible.py",)
    with pytest.raises(ValueError, match="links or junctions"):
        inventory.read("linked.py")
    with pytest.raises(ValueError, match="links or junctions"):
        inventory.read("linked/private.py")


def test_inventory_rejects_windows_reparse_points_without_traversal(tmp_path: Path) -> None:
    _write(tmp_path, "source.py")
    inventory = LocalIndexInventory(tmp_path)
    attributes = SimpleNamespace(
        st_mode=stat.S_IFREG, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT
    )
    with patch.object(Path, "lstat", return_value=attributes):
        assert inventory.candidates() == ()
        with pytest.raises(ValueError, match="links or junctions"):
            inventory.read("source.py")


def test_inventory_omits_unreadable_files(tmp_path: Path) -> None:
    _write(tmp_path, "source.py")
    inventory = LocalIndexInventory(tmp_path)
    with patch.object(Path, "read_bytes", side_effect=PermissionError):
        assert inventory.candidates() == ()
        assert inventory.read("source.py") is None
