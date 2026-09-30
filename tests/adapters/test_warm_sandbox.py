from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from cuanta.adapters.system.process_tree import CREATE_BREAKAWAY_FROM_JOB
from cuanta.adapters.system.sandbox import MARKER, LocalSandbox
from cuanta.adapters.system.sandbox_cleanup import (
    BackgroundCleanup,
    await_cleanup,
    finish_cleanup,
)
from cuanta.adapters.system.warm_sandbox import WarmSandbox


def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text("original", encoding="utf-8")
    (root / "package-lock.json").write_text("locked", encoding="utf-8")
    modules = root / "node_modules"
    modules.mkdir()
    (modules / "module.js").write_text("dependency", encoding="utf-8")
    return root


def test_warm_copy_reuses_dependencies_and_syncs_source_additions_removals_and_locks(
    tmp_path: Path,
) -> None:
    root = project(tmp_path)
    sandbox = WarmSandbox(parents=(tmp_path / "temp",))
    first = sandbox.create(root)
    (first.root / "app.py").write_text("candidate", encoding="utf-8")
    assert sandbox.release(root, first.slot)
    (root / "app.py").unlink()
    (root / "new.py").write_text("new", encoding="utf-8")
    (root / "package-lock.json").write_text("changed", encoding="utf-8")
    second = sandbox.create(root)
    assert second.reused and second.root == first.root
    assert second.copied_files == 2
    assert not (second.root / "app.py").exists()
    assert (second.root / "new.py").read_text(encoding="utf-8") == "new"
    assert (second.root / "package-lock.json").read_text(encoding="utf-8") == "changed"
    assert os.path.samefile(root / "node_modules/module.js", second.root / "node_modules/module.js")
    assert sandbox.manifest(second) == second.hashes()


def test_warm_drift_with_preserved_size_and_timestamp_falls_back_to_fresh_copy(
    tmp_path: Path,
) -> None:
    root = project(tmp_path)
    sandbox = WarmSandbox(parents=(tmp_path / "temp",))
    first = sandbox.create(root)
    assert sandbox.release(root, first.slot)
    target = first.root / "app.py"
    stamp = target.stat()
    target.write_text("tampered", encoding="utf-8")
    os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    second = sandbox.create(root)
    assert not second.reused
    assert (second.root / "app.py").read_text(encoding="utf-8") == "original"
    assert (root / "app.py").read_text(encoding="utf-8") == "original"


def test_busy_and_kept_copies_are_never_reused(tmp_path: Path) -> None:
    root = project(tmp_path)
    sandbox = WarmSandbox(parents=(tmp_path / "temp",))
    first = sandbox.create(root)
    assert sandbox.release(root, first.slot)
    active = sandbox.create(root)
    concurrent = sandbox.create(root)
    assert active.root != concurrent.root and not concurrent.reused
    sandbox.keep(active)
    assert sandbox.create(root).root != active.root
    assert active.root.exists()


def test_stale_lease_is_preserved_and_forces_a_fresh_copy(tmp_path: Path) -> None:
    root = project(tmp_path)
    sandbox = WarmSandbox(parents=(tmp_path / "temp",))
    first = sandbox.create(root)
    assert sandbox.release(root, first.slot)
    lease = first.slot / "lease.json"
    stale = json.dumps({"pid": 0}).encode()
    lease.write_bytes(stale)
    second = sandbox.create(root)
    assert not second.reused and second.root != first.root
    assert lease.read_bytes() == stale and first.root.is_dir()
    assert (root / "app.py").read_text(encoding="utf-8") == "original"


def test_stale_observation_cannot_reclaim_a_replacement_live_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cuanta.adapters.system import warm_sandbox

    root = project(tmp_path)
    sandbox = WarmSandbox(parents=(tmp_path / "temp",))
    first = sandbox.create(root)
    assert sandbox.release(root, first.slot)
    image = root / ".cuanta/trials/RUN/files/app.py"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"captured candidate")
    lease = first.slot / "lease.json"
    lease.write_text(json.dumps({"pid": 0}), encoding="utf-8")
    old_json = warm_sandbox._json
    first_observation = old_json(lease)
    second_observation = old_json(lease)
    assert first_observation == second_observation == {"pid": 0}
    lease.unlink()
    assert sandbox._lease(first.slot)
    live_lease = lease.read_bytes()

    def observed(path: Path) -> dict[str, object]:
        return second_observation if path == lease else old_json(path)

    monkeypatch.setattr(warm_sandbox, "_json", observed)
    second = sandbox.create(root)
    assert not second.reused and second.root != first.root
    assert lease.read_bytes() == live_lease and first.root.is_dir()
    assert (root / "app.py").read_text(encoding="utf-8") == "original"
    assert image.read_bytes() == b"captured candidate"


def test_background_cleanup_waits_for_display_and_preserves_saved_after_images(
    tmp_path: Path,
) -> None:
    root = project(tmp_path)
    sandbox = WarmSandbox(parents=(tmp_path / "temp",))
    copy = sandbox.create(root)
    (copy.root / "app.py").write_text("candidate", encoding="utf-8")
    state = root / ".cuanta"
    image = state / "trials" / "RUN" / "files" / "app.py"
    image.parent.mkdir(parents=True)
    image.write_text("candidate", encoding="utf-8")
    BackgroundCleanup(copy, "RUN", state, (tmp_path / "temp",))
    path = state / "trials" / "RUN" / "cleanup.json"
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "pending"
    assert (copy.root / "app.py").read_text(encoding="utf-8") == "candidate"
    assert not await_cleanup(root, "RUN", timeout=0)
    assert finish_cleanup(path)
    assert await_cleanup(root, "RUN", timeout=0)
    assert image.read_text(encoding="utf-8") == "candidate"
    assert sandbox.create(root).reused


def test_cleanup_failure_is_durable_and_does_not_touch_unowned_paths(tmp_path: Path) -> None:
    root = project(tmp_path)
    sandbox = WarmSandbox(parents=(tmp_path / "temp",))
    copy = sandbox.create(root)
    BackgroundCleanup(copy, "RUN", root / ".cuanta", (tmp_path / "temp",))
    path = root / ".cuanta" / "trials" / "RUN" / "cleanup.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["slot"] = str(root)
    path.write_text(json.dumps(state), encoding="utf-8")
    assert not finish_cleanup(path)
    assert not await_cleanup(root, "RUN", timeout=0)
    assert (root / "app.py").is_file()
    assert copy.root.is_dir()


def test_changed_dependencies_remove_copy_instead_of_recycling(tmp_path: Path) -> None:
    root = project(tmp_path)
    sandbox = WarmSandbox(parents=(tmp_path / "temp",))
    copy = sandbox.create(root)
    BackgroundCleanup(copy, "RUN", root / ".cuanta", (tmp_path / "temp",))
    (root / "node_modules/module.js").write_text("changed dependency", encoding="utf-8")
    path = root / ".cuanta" / "trials" / "RUN" / "cleanup.json"
    assert finish_cleanup(path)
    assert not copy.slot.exists()


def test_warm_invalid_pointer_does_not_escape_owned_sandbox(tmp_path: Path) -> None:
    root = project(tmp_path)
    sandbox = WarmSandbox(parents=(tmp_path / "temp",))
    sandbox._pointer(root).write_text(json.dumps({"slot": str(root)}), encoding="utf-8")
    assert sandbox.create(root).root != root
    with pytest.raises(ValueError, match="unowned"):
        sandbox.release(root, root)


def test_cleanup_launch_is_detached_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    copy = WarmSandbox(parents=(tmp_path / "temp",)).create(root)
    queued: list[tuple[Sequence[str], dict[str, object]]] = []

    class Child:
        pid = 4242

        def poll(self) -> int | None:
            return None

    def spawn(args: Sequence[str], **kwargs: object) -> Child:
        queued.append((args, kwargs))
        return Child()

    monkeypatch.setattr(subprocess, "Popen", spawn)
    cleanup = BackgroundCleanup(copy, "RUN", root / ".cuanta")
    assert queued == []
    cleanup()
    cleanup()
    assert len(queued) == 1
    kwargs = queued[0][1]
    assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["close_fds"] is True
    assert json.loads((copy.slot / MARKER).read_text(encoding="utf-8"))["pid"] == 4242
    flags = kwargs["creationflags"]
    assert isinstance(flags, int)
    if os.name == "nt":
        assert flags & CREATE_BREAKAWAY_FROM_JOB
    else:
        assert kwargs["start_new_session"]


def test_a_copy_being_recycled_survives_the_sweep_of_a_new_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    copy = WarmSandbox(parents=(tmp_path / "temp",)).create(root)
    finished = subprocess.Popen([sys.executable, "-c", ""])
    finished.wait()
    marker = copy.slot / MARKER
    state = json.loads(marker.read_text(encoding="utf-8"))
    marker.write_text(json.dumps({**state, "pid": finished.pid}), encoding="utf-8")

    class Worker:
        pid = os.getpid()

        def poll(self) -> int | None:
            return None

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: Worker())
    BackgroundCleanup(copy, "RUN", root / ".cuanta", (tmp_path / "temp",))()
    other = LocalSandbox(parents=(tmp_path / "temp",)).create(root)
    assert other.slot != copy.slot
    assert copy.slot.is_dir() and (copy.root / "app.py").is_file()


def test_cleanup_spawn_failure_records_error_and_preserves_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    copy = WarmSandbox(parents=(tmp_path / "temp",)).create(root)

    def spawn(*args: object, **kwargs: object) -> None:
        raise OSError("cannot start cleanup")

    monkeypatch.setattr(subprocess, "Popen", spawn)
    BackgroundCleanup(copy, "RUN", root / ".cuanta")()
    state = json.loads((root / ".cuanta/trials/RUN/cleanup.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed" and state["error"] == "OSError"
    assert copy.root.is_dir() and (root / "app.py").read_text(encoding="utf-8") == "original"


def test_recycling_unlinks_candidate_links_before_dependency_synchronization(
    tmp_path: Path,
) -> None:
    root = project(tmp_path)
    sandbox = WarmSandbox(parents=(tmp_path / "temp",))
    dependency = root / "node_modules" / "package"
    dependency.mkdir()
    (dependency / "module.js").write_text("dependency", encoding="utf-8")
    copy = sandbox.create(root)
    target = copy.root / "node_modules" / "package"
    (target / "module.js").unlink()
    target.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "module.js").write_text("private", encoding="utf-8")
    if os.name == "nt":
        from cuanta.adapters.system.sandbox import _junction

        _junction(str(outside), str(target))
    else:
        target.symlink_to(outside, target_is_directory=True)
    assert sandbox.release(root, copy.slot)
    assert (outside / "module.js").read_text(encoding="utf-8") == "private"
    assert (target / "module.js").read_text(encoding="utf-8") == "dependency"
