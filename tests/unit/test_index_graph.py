from __future__ import annotations

import json
import os
import subprocess
import time
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from cuanta.adapters.graph import index_graph
from cuanta.adapters.graph.file_graph import load_index_graph
from cuanta.adapters.graph.index_graph import LocalIndexGraph, run_worker
from cuanta.adapters.system.index_inventory import LocalIndexInventory
from cuanta.domain.code_index import IndexedFile
from cuanta.ports.system import Completed
from tests.fakes import FakeRunner


def _file(root: Path, relative: str, text: str = "def shared(): pass\n") -> IndexedFile:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    payload = path.read_bytes()
    return IndexedFile(relative, sha256(payload).hexdigest(), "python", len(payload))


def _write_graph(
    root: Path, files: tuple[IndexedFile, ...], key: str = "links", hashes: bool = False
) -> dict[str, object]:
    folder = root / "graphify-out"
    folder.mkdir(exist_ok=True)
    nodes: list[dict[str, object]] = [
        {
            "id": f"n{number}",
            "label": "shared",
            "source_file": file.path,
            "source_location": "L2-L4",
            "_origin": "ast",
            **({"source_hash": file.content_hash} if hashes else {}),
        }
        for number, file in enumerate(files[:2])
    ]
    edge = {
        "source": "n0",
        "target": "n1",
        "source_file": files[0].path,
        "source_location": "L7",
        "relation": "imports",
        "confidence": "INFERRED",
        "confidence_score": 0.65,
    }
    data: dict[str, object] = {"nodes": nodes, key: [edge], "directed": False}
    (folder / "graph.json").write_text(json.dumps(data), encoding="utf-8")
    manifest = {file.path: {"mtime": (root / file.path).stat().st_mtime} for file in files}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return data


@pytest.mark.parametrize("key", ["links", "edges"])
def test_graph_import_preserves_duplicates_direction_lines_and_provenance(
    tmp_path: Path, key: str
) -> None:
    files = (_file(tmp_path, "a.py"), _file(tmp_path, "b.py"))
    _write_graph(tmp_path, files, key)
    records = load_index_graph(tmp_path, files)
    assert len(records.symbols) == 2
    assert {row.path for row in records.symbols} == {"a.py", "b.py"}
    assert {row.text for row in records.symbols} == {"shared"}
    assert len({row.id for row in records.symbols}) == 2
    assert all((row.line, row.end_line) == (2, 4) for row in records.symbols)
    assert len(records.edges) == 1
    edge = records.edges[0]
    assert (edge.path, edge.target, edge.relation, edge.line) == ("a.py", "b.py", "imports", 7)
    assert edge.confidence == 0.65
    assert edge.source_hash == files[0].content_hash
    assert edge.provenance.startswith("graphify:")
    assert edge.text == "n0 -> n1"


def test_hash_proof_overrides_mtime_but_rejects_conflicting_hash(tmp_path: Path) -> None:
    files = (_file(tmp_path, "a.py"), _file(tmp_path, "b.py"))
    data = _write_graph(tmp_path, files, hashes=True)
    (tmp_path / "graphify-out/manifest.json").unlink()
    records = load_index_graph(tmp_path, files)
    assert len(records.symbols) == 2
    assert len(records.edges) == 1
    nodes = cast(list[dict[str, object]], data["nodes"])
    nodes[1]["source_hash"] = "bad"
    (tmp_path / "graphify-out/graph.json").write_text(json.dumps(data), encoding="utf-8")
    records = load_index_graph(tmp_path, files)
    assert len(records.symbols) == 1
    assert records.edges == ()


@pytest.mark.parametrize("change", ["stale", "deleted", "missing_manifest", "inventory_changed"])
def test_graph_import_rejects_unproven_or_changed_source(tmp_path: Path, change: str) -> None:
    files = (_file(tmp_path, "a.py"), _file(tmp_path, "b.py"))
    _write_graph(tmp_path, files)
    if change == "stale":
        path = tmp_path / "b.py"
        os.utime(path, (time.time() + 5, time.time() + 5))
    elif change == "deleted":
        (tmp_path / "b.py").unlink()
    elif change == "missing_manifest":
        (tmp_path / "graphify-out/manifest.json").unlink()
    else:
        (tmp_path / "b.py").write_text("def changed(): pass\n", encoding="utf-8")
    records = load_index_graph(tmp_path, files)
    assert len(records.symbols) == (0 if change == "missing_manifest" else 1)
    assert records.edges == ()


@pytest.mark.parametrize("path", ["../outside.py", "/absolute.py", "C:/outside.py", "missing.py"])
def test_graph_import_rejects_unindexed_or_escaping_nodes(tmp_path: Path, path: str) -> None:
    files = (_file(tmp_path, "a.py"), _file(tmp_path, "b.py"))
    data = _write_graph(tmp_path, files, hashes=True)
    nodes = cast(list[dict[str, object]], data["nodes"])
    nodes[1]["source_file"] = path
    (tmp_path / "graphify-out/graph.json").write_text(json.dumps(data), encoding="utf-8")
    records = load_index_graph(tmp_path, files)
    assert len(records.symbols) == 1
    assert records.edges == ()


def test_graph_import_rejects_symlink_endpoint(tmp_path: Path) -> None:
    files = (_file(tmp_path, "a.py"), _file(tmp_path, "b.py"))
    _write_graph(tmp_path, files, hashes=True)
    (tmp_path / "b.py").unlink()
    (tmp_path / "b.py").symlink_to(tmp_path / "a.py")
    records = load_index_graph(tmp_path, files)
    assert len(records.symbols) == 1
    assert records.edges == ()


@pytest.mark.parametrize("content", ["not json", "[]", '{"nodes":1,"links":{}}'])
def test_graph_missing_or_malformed_never_invents_structure(tmp_path: Path, content: str) -> None:
    files = (_file(tmp_path, "a.py"),)
    assert load_index_graph(tmp_path, files).symbols == ()
    folder = tmp_path / "graphify-out"
    folder.mkdir()
    (folder / "graph.json").write_text(content, encoding="utf-8")
    assert load_index_graph(tmp_path, files).symbols == ()


def test_fingerprint_changes_with_graph_or_manifest_bytes(tmp_path: Path) -> None:
    graph = LocalIndexGraph(tmp_path, FakeRunner())
    missing = graph.fingerprint()
    files = (_file(tmp_path, "a.py"), _file(tmp_path, "b.py"))
    _write_graph(tmp_path, files)
    present = graph.fingerprint()
    assert present != missing
    (tmp_path / "graphify-out/manifest.json").write_text("{}", encoding="utf-8")
    assert graph.fingerprint() != present
    assert len(graph.records(files).symbols) == 0


def test_same_content_mtime_change_updates_graph_freshness_fingerprint(tmp_path: Path) -> None:
    files = (_file(tmp_path, "a.py"), _file(tmp_path, "b.py"))
    _write_graph(tmp_path, files)
    graph = LocalIndexGraph(tmp_path, FakeRunner())
    before = graph.fingerprint()
    path = tmp_path / "b.py"
    newer = path.stat().st_mtime + 5
    os.utime(path, (newer, newer))
    assert graph.fingerprint() != before
    assert len(graph.records(files).symbols) == 1


def _medium(root: Path, count: int = 100) -> tuple[IndexedFile, ...]:
    files = tuple(_file(root, f"src/f{number}.py") for number in range(count))
    _write_graph(root, files)
    graph = root / "graphify-out/graph.json"
    before = time.time() - 20
    os.utime(graph, (before, before))
    return files


def _capture_spawn(monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, dict[str, object]]]:
    calls: list[tuple[object, dict[str, object]]] = []

    def spawn(command: object, **kwargs: object) -> subprocess.Popen[bytes]:
        calls.append((command, kwargs))
        return cast("subprocess.Popen[bytes]", SimpleNamespace(pid=999999))

    monkeypatch.setattr("cuanta.adapters.graph.index_graph.subprocess.Popen", spawn)
    return calls


@pytest.mark.parametrize("reason", ["small", "missing", "fresh", "no_binary"])
def test_refresh_gate_refuses_ineligible_background_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    files = _medium(tmp_path, 99 if reason == "small" else 100)
    if reason == "missing":
        (tmp_path / "graphify-out/graph.json").unlink()
    elif reason == "fresh":
        graph = tmp_path / "graphify-out/graph.json"
        newer = time.time() + 20
        os.utime(graph, (newer, newer))
    runner = FakeRunner(binaries={} if reason == "no_binary" else {"graphify": "fake"})
    calls = _capture_spawn(monkeypatch)
    LocalIndexGraph(tmp_path, runner).request_refresh(files)
    assert calls == []
    assert runner.calls == []


def test_refresh_spawns_detached_worker_once_and_records_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = _medium(tmp_path)
    runner = FakeRunner(binaries={"graphify": "fake"})
    calls = _capture_spawn(monkeypatch)
    graph = LocalIndexGraph(tmp_path, runner)
    graph.request_refresh(files)
    graph.request_refresh(files)
    assert len(calls) == 1
    command, options = calls[0]
    assert "cuanta.adapters.graph.index_graph" in cast(list[str], command)
    assert options["stdout"] == subprocess.DEVNULL
    assert options["stderr"] == subprocess.DEVNULL
    assert options["start_new_session"] is True
    assert options["creationflags"] == index_graph._detached_flags()
    lease = json.loads((tmp_path / ".cuanta/graph-refresh.lock").read_text(encoding="utf-8"))
    assert lease["pid"] == 999999
    assert len(lease["token"]) == 32
    assert runner.calls == []


def test_manifest_mismatch_requests_refresh_even_when_graph_mtime_is_newer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = _medium(tmp_path)
    graph = tmp_path / "graphify-out/graph.json"
    newer = time.time() + 20
    os.utime(graph, (newer, newer))
    changed = _file(tmp_path, "src/f0.py", "def changed(): pass\n")
    files = (changed, *files[1:])
    calls = _capture_spawn(monkeypatch)
    LocalIndexGraph(tmp_path, FakeRunner(binaries={"graphify": "fake"})).request_refresh(files)
    assert len(calls) == 1


def test_expired_lease_can_be_replaced_without_killing_a_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = _medium(tmp_path)
    state = tmp_path / ".cuanta"
    state.mkdir()
    (state / "graph-refresh.lock").write_text(
        json.dumps({"token": "old", "pid": 123, "expires": time.time() - 1}), encoding="utf-8"
    )
    calls = _capture_spawn(monkeypatch)
    LocalIndexGraph(tmp_path, FakeRunner(binaries={"graphify": "fake"})).request_refresh(files)
    assert len(calls) == 1
    assert json.loads((state / "graph-refresh.lock").read_text(encoding="utf-8"))["token"] != "old"


@pytest.mark.parametrize("healthy", [False, True])
def test_worker_verifies_executable_health_before_update_and_cleans_owned_lease(
    tmp_path: Path, healthy: bool
) -> None:
    _medium(tmp_path)
    state = tmp_path / ".cuanta"
    state.mkdir()
    token = "a" * 32
    (state / "graph-refresh.lock").write_text(
        json.dumps({"token": token, "expires": time.time() + 1200}), encoding="utf-8"
    )
    runner = FakeRunner(
        binaries={"graphify": "fake"},
        responses={"graphify --help": Completed(0 if healthy else 1, "", "broken launcher")},
    )
    run_worker(tmp_path, token, runner)
    assert (("graphify", "update", ".") in runner.calls) is healthy
    assert not (state / "graph-refresh.lock").exists()
    status = json.loads((state / "graph-refresh.log").read_text(encoding="utf-8"))
    assert status["status"] == ("updated" if healthy else "broken")
    assert runner.calls[0] == ("graphify", "--help")


def test_worker_refuses_replaced_token_and_retains_other_lease(tmp_path: Path) -> None:
    _medium(tmp_path)
    state = tmp_path / ".cuanta"
    state.mkdir()
    (state / "graph-refresh.lock").write_text('{"token":"new"}', encoding="utf-8")
    runner = FakeRunner(binaries={"graphify": "fake"})
    run_worker(tmp_path, "old", runner)
    assert runner.calls == []
    assert json.loads((state / "graph-refresh.lock").read_text(encoding="utf-8"))["token"] == "new"


def test_worker_refuses_expired_lease_without_starting_graphify(tmp_path: Path) -> None:
    _medium(tmp_path)
    state = tmp_path / ".cuanta"
    state.mkdir()
    (state / "graph-refresh.lock").write_text(
        json.dumps({"token": "expired", "expires": time.time() - 1}), encoding="utf-8"
    )
    runner = FakeRunner(binaries={"graphify": "fake"})
    run_worker(tmp_path, "expired", runner)
    assert runner.calls == []
    assert not (state / "graph-refresh.lock").exists()
    assert (
        json.loads((state / "graph-refresh.log").read_text(encoding="utf-8"))["status"] == "expired"
    )


def test_fresh_malformed_lease_does_not_duplicate_an_unproven_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = _medium(tmp_path)
    state = tmp_path / ".cuanta"
    state.mkdir()
    lock = state / "graph-refresh.lock"
    lock.write_text("not json", encoding="utf-8")
    calls = _capture_spawn(monkeypatch)
    graph = LocalIndexGraph(tmp_path, FakeRunner(binaries={"graphify": "fake"}))
    graph.request_refresh(files)
    assert calls == []
    old = time.time() - 1201
    os.utime(lock, (old, old))
    graph.request_refresh(files)
    assert len(calls) == 1


def test_refresh_rejects_symlink_state_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = _medium(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / ".cuanta").symlink_to(elsewhere, target_is_directory=True)
    calls = _capture_spawn(monkeypatch)
    graph = LocalIndexGraph(tmp_path, FakeRunner(binaries={"graphify": "fake"}))
    graph.request_refresh(files)
    assert calls == []
    assert tuple(elsewhere.iterdir()) == ()


@pytest.mark.parametrize("name", ["graph-refresh.log", "graph-refresh.lock"])
def test_refresh_state_links_never_modify_external_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    files = _medium(tmp_path)
    outside = tmp_path.parent / f"outside-{tmp_path.name}.txt"
    original = '{"token":"external","expires":0}'
    outside.write_text(original, encoding="utf-8")
    state = tmp_path / ".cuanta"
    state.mkdir()
    linked = state / name
    linked.symlink_to(outside)
    calls = _capture_spawn(monkeypatch)
    graph = LocalIndexGraph(tmp_path, FakeRunner(binaries={"graphify": "fake"}))
    graph.request_refresh(files)
    assert outside.read_text(encoding="utf-8") == original
    assert linked.is_symlink()
    if name.endswith("lock"):
        assert calls == []
        assert index_graph._read_lease(state) == {}
    else:
        assert len(calls) == 1


def test_worker_status_link_is_preserved_and_external_content_unchanged(tmp_path: Path) -> None:
    _medium(tmp_path)
    state = tmp_path / ".cuanta"
    state.mkdir()
    outside = tmp_path.parent / f"outside-{tmp_path.name}.log"
    outside.write_text("private external content", encoding="utf-8")
    (state / "graph-refresh.log").symlink_to(outside)
    token = "a" * 32
    (state / "graph-refresh.lock").write_text(
        json.dumps({"token": token, "expires": time.time() + 1200}), encoding="utf-8"
    )
    runner = FakeRunner(binaries={"graphify": "fake"})
    run_worker(tmp_path, token, runner)
    assert ("graphify", "update", ".") in runner.calls
    assert outside.read_text(encoding="utf-8") == "private external content"
    assert (state / "graph-refresh.log").is_symlink()
    assert not (state / "graph-refresh.lock").exists()


def test_worker_inventory_rechecks_tree_before_graphify(tmp_path: Path) -> None:
    files = _medium(tmp_path)
    state = tmp_path / ".cuanta"
    state.mkdir()
    (state / "graph-refresh.lock").write_text(
        json.dumps({"token": "owned", "expires": time.time() + 1200}), encoding="utf-8"
    )
    for file in files[99:]:
        (tmp_path / file.path).unlink()
    assert len(LocalIndexInventory(tmp_path).candidates()) >= 99
    runner = FakeRunner(binaries={"graphify": "fake"})
    run_worker(tmp_path, "owned", runner)
    assert runner.calls == []
    assert not (state / "graph-refresh.lock").exists()


def test_spawn_failure_releases_owned_lock_and_keeps_index_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = _medium(tmp_path)

    def fail(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        raise OSError("process unavailable")

    monkeypatch.setattr("cuanta.adapters.graph.index_graph.subprocess.Popen", fail)
    graph = LocalIndexGraph(tmp_path, FakeRunner(binaries={"graphify": "fake"}))
    graph.request_refresh(files)
    assert not (tmp_path / ".cuanta/graph-refresh.lock").exists()
    assert len(graph.records(files).symbols) == 2
