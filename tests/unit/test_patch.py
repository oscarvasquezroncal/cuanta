from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from cuanta.domain.gitindex import blob_id, parse_index
from cuanta.domain.patch import file_patch, is_binary, join_patches, quote_path, split_lines

GIT = shutil.which("git")
CASES: dict[str, tuple[bytes | None, bytes | None]] = {
    "crlf.txt": (b"one\r\ntwo\r\nthree\r\n", b"one\r\nTWO\r\nthree\r\n"),
    "tail.txt": (b"alpha\nbeta", b"alpha\nbeta\ngamma"),
    "cut.txt": (b"alpha\nbeta\n", b"alpha\nbeta"),
    "latin.txt": ("caf\u00e9\n".encode("latin-1"), "caf\u00e9s\n".encode("latin-1")),
    "gone.txt": (b"bye\n", None),
    "new.txt": (None, b"hello\nworld\n"),
    "empty-new.txt": (None, b""),
    "empty-gone.txt": (b"", None),
    "dir with space/file.md": (b"x\n", b"y\n"),
    "caf\u00e9.md": (b"a\n", b"b\n"),
    "logo.png": (b"\x89PNG\x00old" * 40, b"\x89PNG\x00new" * 41),
    "new.bin": (None, bytes(range(256)) * 3),
    "gone.bin": (b"\x00\x01\x02", None),
    "big.json": (None, b'{"k": "' + b"v" * 1_000_100 + b'"}\n'),
}


def _write(root: Path, relative: str, data: bytes | None) -> None:
    target = root / relative
    if data is None:
        target.unlink(missing_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
    )


def test_split_lines_splits_on_newline_only() -> None:
    assert split_lines("a\x0bb\x85c\r\nd") == ["a\x0bb\x85c\r\n", "d"]
    assert split_lines("") == []
    assert split_lines("x\n") == ["x\n"]


def test_modified_text_keeps_crlf_and_counts_lines() -> None:
    patch = file_patch("crlf.txt", *CASES["crlf.txt"])
    assert patch.added == 1
    assert patch.removed == 1
    assert "-two\r\n+TWO\r\n" in patch.text
    assert patch.text.startswith("diff --git a/crlf.txt b/crlf.txt\n--- a/crlf.txt\n")


def test_missing_final_newline_gets_the_git_marker() -> None:
    patch = file_patch("tail.txt", *CASES["tail.txt"])
    assert "-beta\n\\ No newline at end of file\n+beta\n+gamma\n\\ No newline" in patch.text


def test_added_and_deleted_files_use_dev_null_and_mode_lines() -> None:
    added = file_patch("new.txt", None, b"hello\n")
    assert "new file mode 100644\n--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1 @@\n+hello\n" in (
        added.text
    )
    deleted = file_patch("gone.txt", b"bye\n", None)
    assert "deleted file mode 100644\n--- a/gone.txt\n+++ /dev/null\n" in deleted.text


def test_empty_added_file_is_a_header_only_patch() -> None:
    assert file_patch("e.txt", None, b"").text == (
        "diff --git a/e.txt b/e.txt\nnew file mode 100644\n"
    )


def test_binary_and_oversized_files_are_marked_binary() -> None:
    patch = file_patch("img.png", b"\x89PNG\x00\x01", b"\x89PNG\x00\x02")
    assert patch.binary
    old, new = blob_id(b"\x89PNG\x00\x01"), blob_id(b"\x89PNG\x00\x02")
    assert f"index {old}..{new} 100644\nGIT binary patch\nliteral 6\n" in patch.text
    assert patch.text.endswith("\n\n")
    added = file_patch("new.bin", None, b"\x00")
    created = blob_id(b"\x00")
    assert f"index {'0' * 40}..{created}\n" in added.text
    assert is_binary(b"a" * 1_000_001)
    assert not is_binary(b"plain text\n")


def test_paths_are_quoted_like_git() -> None:
    assert quote_path("a/plain name.txt") == "a/plain name.txt"
    assert quote_path("a/caf\u00e9.md") == '"a/caf\\303\\251.md"'
    assert quote_path('a/q"uote') == '"a/q\\"uote"'
    spaced = file_patch("dir with space/file.md", b"x\n", b"y\n").text
    assert "--- a/dir with space/file.md\t\n" in spaced


@pytest.mark.skipif(GIT is None, reason="git is not installed")
def test_patch_round_trips_through_git_apply(tmp_path: Path) -> None:
    before_root = tmp_path / "repo"
    before_root.mkdir()
    for relative, (before, _) in CASES.items():
        _write(before_root, relative, before)
    assert _git(before_root, "init", "-q").returncode == 0
    patch = join_patches(file_patch(relative, *pair) for relative, pair in CASES.items())
    patch_file = tmp_path / "change.patch"
    patch_file.write_bytes(patch.encode("utf-8", errors="surrogateescape"))
    checked = _git(before_root, "apply", "--check", str(patch_file))
    assert checked.returncode == 0, checked.stderr
    applied = _git(before_root, "apply", str(patch_file))
    assert applied.returncode == 0, applied.stderr
    for relative, (_, after) in CASES.items():
        target = before_root / relative
        if after is None:
            assert not target.exists(), relative
        else:
            assert target.read_bytes() == after, relative


def test_blob_ids_match_git() -> None:
    assert blob_id(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert blob_id(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_malformed_indexes_are_unknown() -> None:
    assert parse_index(b"") is None
    assert parse_index(b"XXXX" + bytes(8)) is None
    assert parse_index(b"DIRC\x00\x00\x00\x09\x00\x00\x00\x00") is None
    assert parse_index(b"DIRC\x00\x00\x00\x02\x00\x00\x00\x01" + bytes(10)) is None


@pytest.mark.skipif(GIT is None, reason="git is not installed")
@pytest.mark.parametrize("version", ["2", "3", "4"])
def test_index_entries_match_git(tmp_path: Path, version: str) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    files = {
        "a.txt": b"alpha\n",
        "dir/b.txt": b"beta\n",
        "dir/deeper/c.txt": b"gamma\n",
        "caf\u00e9.md": b"cafe\n",
    }
    for relative, data in files.items():
        _write(root, relative, data)
    assert _git(root, "init", "-q").returncode == 0
    assert _git(root, "config", "index.version", version).returncode == 0
    assert _git(root, "add", "-A").returncode == 0
    entries = parse_index((root / ".git" / "index").read_bytes())
    assert entries is not None
    assert {path: entry.sha for path, entry in entries.items()} == {
        relative: blob_id(data) for relative, data in files.items()
    }
    stamp = (root / "a.txt").stat()
    assert entries["a.txt"].size == stamp.st_size
    assert entries["a.txt"].mtime_ns // 10**9 == stamp.st_mtime_ns // 10**9


def test_mode_changes_follow_git_format() -> None:
    only = file_patch("bin/run.sh", b"echo\n", b"echo\n", True, False)
    assert only.text == ("diff --git a/bin/run.sh b/bin/run.sh\nold mode 100644\nnew mode 100755\n")
    both = file_patch("bin/run.sh", b"echo\n", b"echo hi\n", False, True).text
    assert "old mode 100755\nnew mode 100644\n--- a/bin/run.sh\n" in both
    gone = file_patch("bin/run.sh", b"echo\n", None, False, True).text
    assert "deleted file mode 100755\n" in gone
    binary = file_patch("bin/tool", b"\x00a", b"\x00b", True, False).text
    assert "old mode 100644\nnew mode 100755\n" in binary
    index = next(line for line in binary.splitlines() if line.startswith("index "))
    assert not index.endswith(("100644", "100755"))


@pytest.mark.skipif(GIT is None or os.name == "nt", reason="needs git and POSIX file modes")
def test_mode_patches_apply_with_git(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root, "run.sh", b"echo\n")
    _write(root, "old.sh", b"old\n")
    os.chmod(root / "old.sh", 0o755)
    assert _git(root, "init", "-q").returncode == 0
    patch = join_patches(
        (
            file_patch("run.sh", b"echo\n", b"echo\n", True, False),
            file_patch("old.sh", b"old\n", None, False, True),
        )
    )
    patch_file = tmp_path / "change.patch"
    patch_file.write_text(patch, encoding="utf-8")
    applied = _git(root, "apply", str(patch_file))
    assert applied.returncode == 0, applied.stderr
    assert os.access(root / "run.sh", os.X_OK)
    assert not (root / "old.sh").exists()
