from __future__ import annotations

import hashlib
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cuanta.adapters.system.index_inventory import LocalIndexInventory


def _write(root: Path, relative: str, text: str = "content\n") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


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
