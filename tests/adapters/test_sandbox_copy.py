from __future__ import annotations

import errno
import importlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from cuanta.adapters.system.sandbox import MARKER, LocalSandbox, fingerprint
from cuanta.domain.errors import EnvironmentFailure

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="needs unprivileged symlinks")


def _write(root: Path, relative: str, data: bytes = b"x\n") -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "shop"
    for relative in (
        "src/app.ts",
        "README.md",
        ".env.local",
        ".claude/agents/senior.md",
        "vendor/lib.go",
        "dist/bundle.js",
        ".git/HEAD",
        ".next/cache/x",
        "pkg/__pycache__/m.pyc",
        ".venv/pyvenv.cfg",
        ".cuanta/config.toml",
        ".cuanta/ledger.db",
        ".cuanta/tmp/lean.json",
        ".cuanta/runs/01/report.md",
        "node_modules/left-pad/index.js",
        "node_modules/.bin/tool.cmd",
        "packages/web/node_modules/react/index.js",
        "packages/web/src/page.tsx",
        "types.tsbuildinfo",
    ):
        _write(project, relative)
    _write(project, ".gitignore", b"dist/\n.env*\n*.tsbuildinfo\n")
    return project


def _sandbox(tmp_path: Path) -> LocalSandbox:
    return LocalSandbox(parents=(tmp_path / "temp",))


def test_copy_keeps_sources_and_skips_git_caches_and_state(tmp_path: Path) -> None:
    project = _project(tmp_path)
    copy = _sandbox(tmp_path).create(project)
    root = copy.root
    present = [
        "src/app.ts",
        "README.md",
        ".env.local",
        ".claude/agents/senior.md",
        "vendor/lib.go",
        ".cuanta/config.toml",
        "packages/web/src/page.tsx",
        "types.tsbuildinfo",
    ]
    absent = [
        ".git",
        ".next",
        "pkg/__pycache__",
        ".venv",
        ".cuanta/ledger.db",
        ".cuanta/tmp",
        "dist",
    ]
    assert all((root / item).is_file() for item in present)
    assert not any((root / item).exists() for item in absent)
    assert not (root / ".cuanta/runs").exists()
    assert root.name == "shop"
    assert (copy.slot / MARKER).is_file()
    assert set(copy.linked) == {"node_modules", "packages/web/node_modules"}
    assert copy.skipped_outputs == ("dist",)
    assert copy.linked_files == 3


def test_base_manifest_honours_gitignore_and_leaves_out_state(tmp_path: Path) -> None:
    copy = _sandbox(tmp_path).create(_project(tmp_path))
    assert set(copy.base) == {
        ".claude/agents/senior.md",
        ".gitignore",
        "README.md",
        "packages/web/src/page.tsx",
        "src/app.ts",
        "vendor/lib.go",
    }


def test_linked_node_modules_are_real_directories_of_hard_links(tmp_path: Path) -> None:
    project = _project(tmp_path)
    copy = _sandbox(tmp_path).create(project)
    linked = copy.root / "node_modules" / "left-pad" / "index.js"
    assert not os.path.islink(copy.root / "node_modules")
    assert not (copy.root / "node_modules").is_junction()
    assert linked.read_bytes() == b"x\n"
    assert os.path.samefile(linked, project / "node_modules" / "left-pad" / "index.js")


def test_new_and_deleted_files_in_the_copy_never_reach_the_original(tmp_path: Path) -> None:
    project = _project(tmp_path)
    sandbox = _sandbox(tmp_path)
    copy = sandbox.create(project)
    before = fingerprint(project, ["node_modules"])
    _write(copy.root, "node_modules/.cache/webpack/pack", b"cache")
    (copy.root / "node_modules" / ".bin" / "tool.cmd").unlink()
    assert fingerprint(project, ["node_modules"]) == before
    assert not (project / "node_modules" / ".cache").exists()
    assert sandbox.dependencies_changed(copy) == ()


def test_in_place_write_through_a_hard_link_is_detected(tmp_path: Path) -> None:
    project = _project(tmp_path)
    sandbox = _sandbox(tmp_path)
    copy = sandbox.create(project)
    with open(copy.root / "node_modules" / "left-pad" / "index.js", "ab") as handle:
        handle.write(b"tampered\n")
    assert sandbox.dependencies_changed(copy) == ("node_modules/left-pad/index.js",)


def test_manifest_sees_edits_additions_and_deletions_only_in_tracked_files(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    sandbox = _sandbox(tmp_path)
    copy = sandbox.create(project)
    _write(copy.root, "src/app.ts", b"changed\n")
    _write(copy.root, "src/new.ts", b"new\n")
    (copy.root / "README.md").unlink()
    _write(copy.root, "dist/bundle.js", b"rebuilt\n")
    _write(copy.root, "tsconfig.tsbuildinfo", b"{}")
    _write(copy.root, ".next/server/app.js", b"built")
    _write(copy.root, ".cuanta/prompts/p.md", b"prompt")
    end = sandbox.manifest(copy)
    assert "README.md" not in end
    assert end["src/new.ts"] != copy.base["src/app.ts"].sha256
    assert end["src/app.ts"] != copy.base["src/app.ts"].sha256
    assert end[".gitignore"] == copy.base[".gitignore"].sha256
    assert not {"dist/bundle.js", "tsconfig.tsbuildinfo"} & set(end)
    assert not any(path.startswith((".next", ".cuanta", "node_modules")) for path in end)
    assert (project / "src" / "app.ts").read_bytes() == b"x\n"


def test_remove_deletes_the_copy_and_keeps_the_original_intact(tmp_path: Path) -> None:
    project = _project(tmp_path)
    sandbox = _sandbox(tmp_path)
    copy = sandbox.create(project)
    before = fingerprint(project, ["node_modules", "packages/web/node_modules"])
    assert sandbox.remove(copy)
    assert not copy.slot.exists()
    assert fingerprint(project, ["node_modules", "packages/web/node_modules"]) == before
    assert (project / "src" / "app.ts").is_file()


def test_read_only_hard_link_removal_restores_the_original_attribute(tmp_path: Path) -> None:
    project = _project(tmp_path)
    locked = project / "node_modules" / "left-pad" / "index.js"
    os.chmod(locked, stat.S_IREAD)
    try:
        sandbox = _sandbox(tmp_path)
        copy = sandbox.create(project)
        assert sandbox.remove(copy)
        assert locked.is_file()
        assert not os.stat(locked).st_mode & stat.S_IWRITE
    finally:
        os.chmod(locked, stat.S_IWRITE | stat.S_IREAD)


def test_remove_refuses_a_slot_it_does_not_own(tmp_path: Path) -> None:
    project = _project(tmp_path)
    sandbox = _sandbox(tmp_path)
    copy = sandbox.create(project)
    (copy.slot / MARKER).write_text('{"origin": "elsewhere"}', encoding="utf-8")
    with pytest.raises(ValueError, match="refusing"):
        sandbox.remove(copy)
    assert copy.root.exists()


def test_second_copy_of_the_same_project_gets_its_own_slot(tmp_path: Path) -> None:
    project = _project(tmp_path)
    sandbox = _sandbox(tmp_path)
    first = sandbox.create(project)
    second = sandbox.create(project)
    assert first.slot != second.slot
    assert second.slot.name == f"{first.slot.name}-2"
    assert sandbox.remove(first)
    third = sandbox.create(project)
    assert third.slot == first.slot


def test_hard_link_failure_is_an_explicit_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)

    def refuse(*_: Any, **__: Any) -> None:
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(os, "link", refuse)
    with pytest.raises(EnvironmentFailure, match="hard-link"):
        _sandbox(tmp_path).create(project)
    assert not any((tmp_path / "temp" / "cuanta-sandbox").iterdir())


def test_no_folder_on_the_project_drive_refuses_when_node_modules_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    module: Any = importlib.import_module("cuanta.adapters.system.sandbox")
    monkeypatch.setattr(module, "_device", lambda path: 1 if path == project.resolve() else 2)
    with pytest.raises(EnvironmentFailure, match="drive"):
        _sandbox(tmp_path).create(project)


def test_projects_without_node_modules_may_use_another_drive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "plain"
    _write(project, "main.py")
    module: Any = importlib.import_module("cuanta.adapters.system.sandbox")
    monkeypatch.setattr(module, "_device", lambda path: 1 if path == project.resolve() else 2)
    copy = _sandbox(tmp_path).create(project)
    assert (copy.root / "main.py").is_file()


@windows_only
def test_junctions_inside_the_project_are_recreated_pointing_into_the_copy(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    winapi: Any = importlib.import_module("_winapi")
    winapi.CreateJunction(str(project / "src"), str(project / "linked-src"))
    sandbox = _sandbox(tmp_path)
    copy = sandbox.create(project)
    linked = copy.root / "linked-src"
    assert linked.is_junction()
    assert Path(os.path.realpath(linked)) == Path(os.path.realpath(copy.root / "src"))
    assert "linked-src/app.ts" not in sandbox.manifest(copy)
    assert sandbox.remove(copy)
    assert (project / "src" / "app.ts").is_file()


@posix_only
def test_symlinks_inside_the_project_are_recreated_not_followed(tmp_path: Path) -> None:
    project = _project(tmp_path)
    os.symlink("src", project / "linked-src", target_is_directory=True)
    sandbox = _sandbox(tmp_path)
    copy = sandbox.create(project)
    assert (copy.root / "linked-src").is_symlink()
    assert os.readlink(copy.root / "linked-src") == "src"
    assert sandbox.remove(copy)
    assert (project / "src" / "app.ts").is_file()


def test_a_home_inside_the_project_is_never_used(tmp_path: Path) -> None:
    project = _project(tmp_path)
    outside = tmp_path / "outside"
    sandbox = LocalSandbox(parents=(project / "tmp", outside))
    copy = sandbox.create(project)
    assert copy.slot.parent == (outside / "cuanta-sandbox").resolve()
    assert not (project / "tmp").exists()
    assert sandbox.remove(copy)


def _link_dir(target: Path, link: Path) -> None:
    if sys.platform == "win32":
        winapi: Any = importlib.import_module("_winapi")
        winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def test_a_linked_node_modules_is_farmed_not_linked(tmp_path: Path) -> None:
    project = tmp_path / "shop"
    _write(project, "src/app.ts")
    deps = tmp_path / "deps" / "node_modules"
    _write(deps, "left-pad/index.js", b"module.exports = 1\n")
    _link_dir(deps, project / "node_modules")
    sandbox = _sandbox(tmp_path)
    copy = sandbox.create(project)
    farmed = copy.root / "node_modules"
    assert copy.linked == ("node_modules",)
    assert not os.path.islink(farmed) and not farmed.is_junction()
    assert os.path.samefile(farmed / "left-pad" / "index.js", deps / "left-pad" / "index.js")
    _write(copy.root, "node_modules/.cache/x", b"cache")
    assert not (deps / ".cache").exists()
    with open(farmed / "left-pad" / "index.js", "ab") as handle:
        handle.write(b"// tampered\n")
    assert sandbox.dependencies_changed(copy) == ("node_modules/left-pad/index.js",)
    assert sandbox.remove(copy)
    assert (project / "node_modules").exists()


def test_links_are_remapped_inside_the_copy_and_outside_links_are_dropped(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    outside = tmp_path / "elsewhere"
    _write(outside, "shared.ts", b"shared\n")
    alias = tmp_path / "alias"
    _link_dir(tmp_path, alias)
    _link_dir(alias / "shop" / "src", project / "inner-link")
    _link_dir(outside, project / "outer-link")
    sandbox = _sandbox(tmp_path)
    copy = sandbox.create(project)
    inner = copy.root / "inner-link"
    assert Path(os.path.realpath(inner)) == Path(os.path.realpath(copy.root / "src"))
    assert not (copy.root / "outer-link").exists()
    assert "outer-link" in copy.skipped_links
    _write(inner, "new.ts", b"through the link\n")
    assert not (project / "src" / "new.ts").exists()
    assert sandbox.remove(copy)
    assert (outside / "shared.ts").is_file()


def test_read_only_restore_never_touches_an_unrelated_original(tmp_path: Path) -> None:
    project = _project(tmp_path)
    sandbox = _sandbox(tmp_path)
    copy = sandbox.create(project)
    replaced = copy.root / "node_modules" / "left-pad" / "index.js"
    replaced.unlink()
    replaced.write_bytes(b"new file\n")
    os.chmod(replaced, stat.S_IREAD)
    assert sandbox.remove(copy)
    original = project / "node_modules" / "left-pad" / "index.js"
    assert os.stat(original).st_mode & stat.S_IWRITE


def test_dependency_caches_are_neither_linked_nor_fingerprinted(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project, "node_modules/.cache/eslint/.eslintcache", b"{}")
    sandbox = _sandbox(tmp_path)
    copy = sandbox.create(project)
    assert "node_modules/.cache" in copy.skipped_caches
    assert not (copy.root / "node_modules" / ".cache").exists()
    _write(project, "node_modules/.cache/eslint/.eslintcache", b'{"rebuilt": true}')
    assert sandbox.dependencies_changed(copy) == ()
    assert sandbox.remove(copy)


def test_new_files_follow_the_copy_current_gitignore_and_known_files_stay_tracked(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    sandbox = _sandbox(tmp_path)
    copy = sandbox.create(project)
    gitignore = copy.root / ".gitignore"
    gitignore.write_bytes(gitignore.read_bytes() + b"/test-results/\nsrc/app.ts\n")
    _write(copy.root, "test-results/.last-run.json", b"{}")
    _write(copy.root, "src/app.ts", b"still tracked\n")
    _write(copy.root, "src/new.ts", b"new\n")
    end = sandbox.manifest(copy)
    assert "test-results/.last-run.json" not in end
    assert end["src/app.ts"] != copy.base["src/app.ts"].sha256
    assert "src/new.ts" in end
    assert sandbox.remove(copy)


def test_edits_to_ignored_files_are_reported(tmp_path: Path) -> None:
    project = _project(tmp_path)
    sandbox = _sandbox(tmp_path)
    copy = sandbox.create(project)
    assert ".env.local" in copy.hidden
    assert sandbox.ignored_changes(copy) == ()
    _write(copy.root, ".env.local", b"SECRET=changed\n")
    (copy.root / "types.tsbuildinfo").unlink()
    assert sandbox.ignored_changes(copy) == (".env.local", "types.tsbuildinfo")
    assert sandbox.remove(copy)


def test_a_later_parent_on_the_project_drive_is_preferred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    far = tmp_path / "far"
    near = tmp_path / "near"
    module: Any = importlib.import_module("cuanta.adapters.system.sandbox")

    def device(path: Path) -> int:
        return 2 if Path(path).resolve().is_relative_to(far.resolve()) else 1

    monkeypatch.setattr(module, "_device", device)
    copy = LocalSandbox(parents=(far, near)).create(project)
    assert copy.slot.parent == (near / "cuanta-sandbox").resolve()


@posix_only
def test_posix_homes_are_private_and_shared_homes_are_refused(tmp_path: Path) -> None:
    project = _project(tmp_path)
    shared = tmp_path / "shared"
    (shared / "cuanta-sandbox").mkdir(parents=True)
    os.chmod(shared / "cuanta-sandbox", 0o777)
    private = tmp_path / "private"
    copy = LocalSandbox(parents=(shared, private)).create(project)
    assert copy.slot.parent == (private / "cuanta-sandbox").resolve()
    assert stat.S_IMODE(os.stat(copy.slot.parent).st_mode) & 0o077 == 0
    assert stat.S_IMODE(os.stat(copy.slot).st_mode) & 0o077 == 0


@posix_only
def test_posix_special_files_are_skipped(tmp_path: Path) -> None:
    project = _project(tmp_path)
    vars(os)["mkfifo"](project / "events.fifo")
    copy = _sandbox(tmp_path).create(project)
    assert "events.fifo" in copy.skipped_links
    assert not (copy.root / "events.fifo").exists()


def test_ignored_build_outputs_are_skipped_but_tracked_ones_are_copied(tmp_path: Path) -> None:
    project = tmp_path / "rusty"
    _write(project, "src/main.rs")
    _write(project, "target/debug/app.bin", b"\x00" * 64)
    _write(project, "build/keep.txt")
    _write(project, ".gitignore", b"/target\n")
    copy = _sandbox(tmp_path).create(project)
    assert copy.skipped_outputs == ("target",)
    assert not (copy.root / "target").exists()
    assert (copy.root / "build" / "keep.txt").is_file()


def test_the_sweep_removes_stale_copies_but_never_kept_ones(tmp_path: Path) -> None:
    project = _project(tmp_path)
    sandbox = _sandbox(tmp_path)
    stale = sandbox.create(project)
    kept = sandbox.create(project)
    sandbox.keep(kept)
    for slot in (stale.slot, kept.slot):
        marker = json.loads((slot / MARKER).read_text(encoding="utf-8"))
        marker["pid"] = 2**22 + 12345
        (slot / MARKER).write_text(json.dumps(marker), encoding="utf-8")
    fresh = sandbox.create(project)
    assert kept.slot.exists()
    assert fresh.slot == stale.slot
    marker = json.loads((fresh.slot / MARKER).read_text(encoding="utf-8"))
    assert marker["pid"] == os.getpid()
    assert sandbox.remove(fresh)
    assert sandbox.remove(kept)


def test_unreadable_ignored_paths_are_skipped_and_tracked_ones_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    module: Any = importlib.import_module("cuanta.adapters.system.sandbox")
    real = module._Copier._copy

    def flaky(self: Any, entry: os.DirEntry[str], relative: str, hidden: bool) -> None:
        if relative in {".env.local", "src/app.ts"}:
            raise PermissionError(13, "Permission denied")
        real(self, entry, relative, hidden)

    monkeypatch.setattr(module._Copier, "_copy", flaky)
    with pytest.raises(EnvironmentFailure, match=r"src/app\.ts"):
        _sandbox(tmp_path).create(project)

    def ignored_only(self: Any, entry: os.DirEntry[str], relative: str, hidden: bool) -> None:
        if relative == ".env.local":
            raise PermissionError(13, "Permission denied")
        real(self, entry, relative, hidden)

    monkeypatch.setattr(module._Copier, "_copy", ignored_only)
    copy = _sandbox(tmp_path).create(project)
    assert ".env.local" in copy.unreadable
    assert ".env.local" not in copy.skipped_links


def test_posix_uses_a_private_folder_next_to_the_project_on_its_drive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name == "nt":
        pytest.skip("the project-drive folder is the drive root on Windows")
    project = _project(tmp_path)
    module: Any = importlib.import_module("cuanta.adapters.system.sandbox")
    monkeypatch.setattr(module, "default_parents", lambda: (tmp_path / "elsewhere",))
    monkeypatch.setattr(
        module,
        "_device",
        lambda path: 2 if Path(path).resolve().is_relative_to(tmp_path / "elsewhere") else 1,
    )
    copy = LocalSandbox().create(project)
    assert copy.slot.parent == (tmp_path / ".cuanta-sandbox").resolve()
    assert stat.S_IMODE(os.stat(copy.slot.parent).st_mode) & 0o077 == 0


@windows_only
def test_windows_drive_root_homes_get_a_protected_acl(tmp_path: Path) -> None:
    home = tmp_path / "shared-home"
    home.mkdir()
    module: Any = importlib.import_module("cuanta.adapters.system.sandbox")
    assert module.protect_windows_home(home)
    listed = subprocess.run(
        ["icacls", str(home)], capture_output=True, text=True, errors="replace", check=True
    ).stdout
    assert "(I)" not in listed


def test_a_partly_copied_ignored_file_is_removed_and_a_full_disk_stops_the_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    module: Any = importlib.import_module("cuanta.adapters.system.sandbox")
    real = module._Copier._copy

    def half(self: Any, entry: os.DirEntry[str], relative: str, hidden: bool) -> None:
        if relative == ".env.local":
            (self.root / relative).write_bytes(b"trunc")
            raise PermissionError(13, "Permission denied")
        real(self, entry, relative, hidden)

    monkeypatch.setattr(module._Copier, "_copy", half)
    copy = _sandbox(tmp_path).create(project)
    assert copy.unreadable == (".env.local",)
    assert not (copy.root / ".env.local").exists()

    def full(self: Any, entry: os.DirEntry[str], relative: str, hidden: bool) -> None:
        if relative == ".env.local":
            raise OSError(errno.ENOSPC, "No space left on device")
        real(self, entry, relative, hidden)

    monkeypatch.setattr(module._Copier, "_copy", full)
    with pytest.raises(EnvironmentFailure, match=r"cannot copy \.env\.local"):
        _sandbox(tmp_path).create(project)


def test_an_unreadable_gitignore_in_an_ignored_folder_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    _write(project, "pgdata/.gitignore", b"*\n")
    _write(project, "pgdata/base/1", b"row\n")
    _write(project, ".gitignore", b"dist/\n.env*\n*.tsbuildinfo\npgdata/\n")
    real = Path.is_file

    def guarded(self: Path) -> bool:
        if self.name == ".gitignore" and self.parent.name == "pgdata":
            raise PermissionError(13, "Permission denied")
        return real(self)

    monkeypatch.setattr(Path, "is_file", guarded)
    copy = _sandbox(tmp_path).create(project)
    assert "pgdata" in copy.unreadable
    assert "src/app.ts" in copy.base


def test_the_sweep_keeps_read_only_originals_read_only(tmp_path: Path) -> None:
    project = _project(tmp_path)
    locked = project / "node_modules" / "left-pad" / "index.js"
    os.chmod(locked, stat.S_IREAD)
    try:
        sandbox = _sandbox(tmp_path)
        orphan = sandbox.create(project)
        marker = json.loads((orphan.slot / MARKER).read_text(encoding="utf-8"))
        marker["pid"] = 2**22 + 12345
        (orphan.slot / MARKER).write_text(json.dumps(marker), encoding="utf-8")
        fresh = sandbox.create(project)
        assert fresh.slot == orphan.slot
        assert locked.is_file()
        assert not os.stat(locked).st_mode & stat.S_IWRITE
        assert sandbox.remove(fresh)
    finally:
        os.chmod(locked, stat.S_IWRITE | stat.S_IREAD)


def test_the_sweep_finds_slots_of_folders_with_glob_characters(tmp_path: Path) -> None:
    project = tmp_path / "shop[v2]"
    _write(project, "src/app.ts")
    sandbox = _sandbox(tmp_path)
    stale = sandbox.create(project)
    marker = json.loads((stale.slot / MARKER).read_text(encoding="utf-8"))
    marker["pid"] = 2**22 + 12345
    (stale.slot / MARKER).write_text(json.dumps(marker), encoding="utf-8")
    fresh = sandbox.create(project)
    assert fresh.slot == stale.slot
    assert sandbox.remove(fresh)


def test_copies_skip_sandbox_homes_inside_the_project(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project, ".cuanta-sandbox/other/src/app.ts")
    copy = _sandbox(tmp_path).create(project)
    assert not (copy.root / ".cuanta-sandbox").exists()
    assert not any(path.startswith(".cuanta-sandbox") for path in copy.base)


def test_git_settings_add_the_excludes_file_and_ignore_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    excludes = home / "my-ignore"
    _write(home, "my-ignore", b"*.secret\n")
    _write(home, "xdg/git/config", f"[core]\n\texcludesFile = {excludes.as_posix()}\n".encode())
    project = _project(tmp_path)
    _write(project, "keys/api.secret")
    _write(project, "Notes.TXT")
    _write(project, ".git/config", b"[core]\n\tignorecase = true\n")
    _write(project, ".gitignore", b"dist/\n.env*\n*.tsbuildinfo\nnotes.txt\n")
    copy = _sandbox(tmp_path).create(project)
    assert "keys/api.secret" not in copy.base
    assert "Notes.TXT" not in copy.base
    assert "src/app.ts" in copy.base


def test_state_guard_sees_settings_and_stored_results_change(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project, ".cuanta/config.toml", b"[test]\ncommand = 'pytest'\n")
    _write(project, ".cuanta/trials/R1/trial.json", b"{}")
    _write(project, ".cuanta/trials/R1/commit.txt", b"feat: x\n")
    sandbox = _sandbox(tmp_path)
    copy = sandbox.create(project)
    assert set(copy.state) == {
        ".cuanta/config.toml",
        ".cuanta/trials/R1/trial.json",
        ".cuanta/trials/R1/commit.txt",
    }
    assert sandbox.state_changed(copy) == ()
    _write(project, ".cuanta/ledger.db", b"busy")
    _write(project, ".cuanta/runs/02/report.md", b"new")
    _write(project, ".cuanta/trials/R1/applied.json", b"{}")
    _write(project, ".cuanta/trials/R1/paths.nul", b"src/app.ts\x00")
    _write(project, ".cuanta/trials/R1/files/src/app.ts", b"checked against trial.json")
    _write(
        project,
        ".cuanta/config.toml",
        b"[test]\ncommand = 'pytest'\n\n[ui]\nmandate_layout = 'wide'\n",
    )
    assert sandbox.state_changed(copy) == ()
    _write(project, ".cuanta/config.toml", b"[test]\ncommand = 'evil'\n")
    _write(project, ".cuanta/trials/R1/trial.json", b'{"changes": []}')
    _write(project, ".cuanta/trials/R2/trial.json", b"{}")
    assert sandbox.state_changed(copy) == (
        ".cuanta/config.toml",
        ".cuanta/trials/R1/trial.json",
        ".cuanta/trials/R2/trial.json",
    )
    assert sandbox.remove(copy)


@posix_only
def test_mode_changes_are_seen_in_the_copy(tmp_path: Path) -> None:
    project = _project(tmp_path)
    tool = _write(project, "bin/tool.sh", b"echo\n")
    os.chmod(tool, 0o755)
    sandbox = _sandbox(tmp_path)
    copy = sandbox.create(project)
    assert copy.base["bin/tool.sh"].executable
    assert not copy.base["src/app.ts"].executable
    os.chmod(copy.root / "src" / "app.ts", 0o755)
    os.chmod(copy.root / "bin" / "tool.sh", 0o644)
    assert sandbox.mode_changes(copy) == ("bin/tool.sh", "src/app.ts")
    assert sandbox.remove(copy)


def test_a_node_modules_folder_that_cannot_be_linked_stops_the_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    _write(project, ".gitignore", b"node_modules\n")
    module: Any = importlib.import_module("cuanta.adapters.system.sandbox")

    def denied(self: Any, relative: str) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(module._Copier, "_farm", denied)
    with pytest.raises(EnvironmentFailure, match="cannot link node_modules"):
        _sandbox(tmp_path).create(project)


def test_a_file_that_vanishes_during_the_copy_is_left_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    module: Any = importlib.import_module("cuanta.adapters.system.sandbox")
    real = module._Copier._copy

    def vanished(self: Any, entry: os.DirEntry[str], relative: str, hidden: bool) -> None:
        if relative == "src/app.ts":
            raise FileNotFoundError(2, "No such file or directory")
        real(self, entry, relative, hidden)

    monkeypatch.setattr(module._Copier, "_copy", vanished)
    copy = _sandbox(tmp_path).create(project)
    assert "src/app.ts" not in copy.base
    assert "README.md" in copy.base


@posix_only
def test_read_only_folders_in_the_copy_are_removed(tmp_path: Path) -> None:
    project = _project(tmp_path)
    sandbox = _sandbox(tmp_path)
    copy = sandbox.create(project)
    locked = copy.root / "vendor"
    os.chmod(locked, 0o555)
    assert sandbox.remove(copy)
    assert not copy.slot.exists()
