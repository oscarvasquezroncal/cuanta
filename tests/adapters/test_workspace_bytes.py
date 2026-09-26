from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from cuanta.adapters.system.workspace import LocalWorkspace


def test_read_only_files_are_replaced_and_stay_read_only(tmp_path: Path) -> None:
    target = tmp_path / "src" / "locked.ts"
    target.parent.mkdir()
    target.write_bytes(b"old\n")
    os.chmod(target, stat.S_IREAD)
    try:
        LocalWorkspace(tmp_path).write_bytes("src/locked.ts", b"new\n")
        assert target.read_bytes() == b"new\n"
        assert not os.stat(target).st_mode & stat.S_IWRITE
    finally:
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE)


def test_read_only_files_can_be_removed(tmp_path: Path) -> None:
    target = tmp_path / "locked.ts"
    target.write_bytes(b"old\n")
    os.chmod(target, stat.S_IREAD)
    LocalWorkspace(tmp_path).remove("locked.ts")
    assert not target.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_write_bytes_sets_or_clears_the_executable_bit(tmp_path: Path) -> None:
    workspace = LocalWorkspace(tmp_path)
    workspace.write_bytes("run.sh", b"echo\n", True)
    assert os.access(tmp_path / "run.sh", os.X_OK)
    workspace.write_bytes("run.sh", b"echo hi\n")
    assert os.access(tmp_path / "run.sh", os.X_OK)
    workspace.write_bytes("run.sh", b"echo hi\n", False)
    assert not os.access(tmp_path / "run.sh", os.X_OK)


def test_a_failed_replace_leaves_no_staged_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "locked.ts"
    target.write_bytes(b"old\n")
    os.chmod(target, stat.S_IREAD)

    def refused(source: object, destination: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "replace", refused)
    try:
        with pytest.raises(PermissionError):
            LocalWorkspace(tmp_path).write_bytes("locked.ts", b"new\n")
        assert sorted(path.name for path in tmp_path.iterdir()) == ["locked.ts"]
        assert target.read_bytes() == b"old\n"
        assert not os.stat(target).st_mode & stat.S_IWRITE
    finally:
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
