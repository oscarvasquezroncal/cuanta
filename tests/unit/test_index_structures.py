from __future__ import annotations

import json
import os
from pathlib import Path

from cuanta.bootstrap import Container
from tests.fakes import FakeRunner


def _source(root: Path, path: str, text: str) -> None:
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8")


def test_incremental_structure_replaces_changed_symbols_and_removed_edges(tmp_path: Path) -> None:
    _source(
        tmp_path, "src/cart.ts", 'import { total } from "./store";\nexport function cart() {}\n'
    )
    _source(tmp_path, "src/store.ts", "export const total = () => 1;\n")
    container = Container.for_project(tmp_path)
    container.runner = FakeRunner()
    service = container.index_service()
    try:
        first = service.update()
        assert first.changed == 2
        assert any(
            row.target == "src/store.ts" for row in service.index.rows("edges", "src/cart.ts")
        )
        before = service.index.rows("symbols", "src/cart.ts")
        assert service.update().changed == 0
        assert service.index.rows("symbols", "src/cart.ts") == before
        _source(tmp_path, "src/store.ts", "export function newTotal() { return 2; }\n")
        assert service.update().changed == 1
        names = {row.text for row in service.index.rows("symbols", "src/store.ts")}
        assert "newTotal" in names and "total" not in names
        (tmp_path / "src/store.ts").unlink()
        assert service.update().removed == 1
        assert not any(row.target == "src/store.ts" for row in service.index.rows("edges"))
        _source(tmp_path, "src/store.ts", "export const total = () => 3;\n")
        assert service.update().changed == 1
        assert any(
            row.target == "src/store.ts" for row in service.index.rows("edges", "src/cart.ts")
        )
    finally:
        service.close()
        container.close()


def test_unsupported_and_invalid_sources_reduce_coverage(tmp_path: Path) -> None:
    _source(tmp_path, "valid.py", "def valid():\n    return 1\n")
    _source(tmp_path, "invalid.tsx", "export function Broken( { <\n")
    _source(tmp_path, "unsupported.rs", "fn main() {}\n")
    container = Container.for_project(tmp_path)
    container.runner = FakeRunner()
    service = container.index_service()
    try:
        report = service.update()
        assert 0 < report.coverage < 1
        assert dict(report.coverage_by_kind)["unsupported"] == 1
        coverage = {item.path: item.coverage for item in service.index.files()}
        assert coverage["invalid.tsx"] == "reduced"
        assert coverage["unsupported.rs"] == "unsupported"
    finally:
        service.close()


def test_unchanged_content_with_changed_mtime_rejects_manifest_graph_rows(tmp_path: Path) -> None:
    _source(tmp_path, "main.py", "def run():\n    return 1\n")
    source = tmp_path / "main.py"
    graph = {
        "nodes": [{"id": "function", "label": "run", "source_file": "main.py", "line": 1}],
        "links": [],
    }
    _source(tmp_path, "graphify-out/graph.json", json.dumps(graph))
    _source(
        tmp_path,
        "graphify-out/manifest.json",
        json.dumps({"main.py": {"mtime": source.stat().st_mtime}}),
    )
    container = Container.for_project(tmp_path)
    container.runner = FakeRunner()
    service = container.index_service()
    try:
        service.update()
        assert any(row.provenance.startswith("graphify:") for row in service.index.rows("symbols"))
        os.utime(source, (source.stat().st_atime, source.stat().st_mtime + 5))
        assert service.update().changed == 0
        assert not any(
            row.provenance.startswith("graphify:") for row in service.index.rows("symbols")
        )
        assert any(row.text == "run" for row in service.index.rows("symbols"))
    finally:
        service.close()
