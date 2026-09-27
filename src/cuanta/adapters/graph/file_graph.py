from __future__ import annotations

import json
import math
import re
import stat
from hashlib import sha256
from pathlib import Path
from typing import cast

from cuanta.domain.affected import file_neighbours
from cuanta.domain.code_index import IndexedFile, IndexRow, IndexStructure, index_path

GRAPH_FILE = "graphify-out/graph.json"


def load_neighbours(root: Path) -> dict[str, frozenset[str]]:
    try:
        data = json.loads((root / GRAPH_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    files: dict[str, str] = {}
    for node in data.get("nodes") or []:
        if isinstance(node, dict) and isinstance(node.get("source_file"), str):
            files[str(node.get("id"))] = node["source_file"]
    pairs: list[tuple[str, str]] = []
    for link in data.get("links") or []:
        if not isinstance(link, dict):
            continue
        left = files.get(str(link.get("source")), "")
        right = files.get(str(link.get("target")), "")
        if left and right:
            pairs.append((left, right))
    return file_neighbours(pairs)


def load_symbols(root: Path) -> dict[str, str]:
    try:
        data = json.loads((root / GRAPH_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    found: dict[str, str] = {}
    for node in data.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        label, path = node.get("label"), node.get("source_file")
        if isinstance(label, str) and isinstance(path, str) and label.isidentifier():
            found.setdefault(label, path)
    return found


def graph_object(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def graph_path(root: Path, relative: str) -> Path | None:
    try:
        normalized = index_path(relative)
        resolved_root = root.resolve()
        path = resolved_root
        for part in normalized.split("/"):
            path /= part
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or (
                getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                return None
        return path if path.is_file() and path.resolve().is_relative_to(resolved_root) else None
    except (OSError, ValueError):
        return None


def graph_document(root: Path, relative: str) -> dict[str, object]:
    path = graph_path(root, relative)
    if path is None:
        return {}
    try:
        return graph_object(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def graph_fresh(
    root: Path,
    file: IndexedFile,
    entry: dict[str, object],
    manifest: dict[str, object],
) -> bool:
    path = graph_path(root, file.path)
    if path is None:
        return False
    try:
        if sha256(path.read_bytes()).hexdigest() != file.content_hash:
            return False
        source = graph_object(manifest.get(file.path))
        for data in (entry, source):
            for name in ("source_hash", "content_hash", "file_hash", "sha256"):
                supplied = data.get(name)
                if isinstance(supplied, str) and supplied:
                    return supplied == file.content_hash
        mtime = source.get("mtime")
        return (
            isinstance(mtime, (int, float))
            and not isinstance(mtime, bool)
            and math.isfinite(mtime)
            and path.stat().st_mtime == mtime
        )
    except OSError:
        return False


def _graph_items(value: object) -> tuple[dict[str, object], ...]:
    return tuple(graph_object(item) for item in value) if isinstance(value, list) else ()


def _graph_position(entry: dict[str, object]) -> tuple[int, int]:
    location = entry.get("source_location", entry.get("line", 0))
    if isinstance(location, int) and not isinstance(location, bool):
        line = max(0, location)
        end = entry.get("end_line", line)
        return line, max(line, end) if isinstance(end, int) else line
    if isinstance(location, str):
        matched = re.fullmatch(r"L?(\d+)(?:[-:]L?(\d+))?", location)
        if matched:
            line = int(matched[1])
            return line, max(line, int(matched[2] or line))
    return 0, 0


def _graph_confidence(entry: dict[str, object]) -> float:
    value = entry.get("confidence_score", entry.get("confidence", 1.0))
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return min(1.0, max(0.0, float(value)))
    return {"EXTRACTED": 1.0, "INFERRED": 0.7, "AMBIGUOUS": 0.3}.get(str(value), 0.0)


def _graph_identifier(value: object) -> str:
    return str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


def _cached_graph_fresh(
    root: Path,
    file: IndexedFile,
    entry: dict[str, object],
    manifest: dict[str, object],
    cache: dict[tuple[str, str], bool],
) -> bool:
    supplied = tuple(
        entry.get(name) for name in ("source_hash", "content_hash", "file_hash", "sha256")
    )
    key = file.path, repr(supplied)
    if key not in cache:
        cache[key] = graph_fresh(root, file, entry, manifest)
    return cache[key]


def load_index_graph(root: Path, files: tuple[IndexedFile, ...]) -> IndexStructure:
    data = graph_document(root, GRAPH_FILE)
    manifest = graph_document(root, "graphify-out/manifest.json")
    current = {file.path: file for file in files}
    nodes: dict[str, IndexRow] = {}
    fresh: dict[tuple[str, str], bool] = {}
    for entry in _graph_items(data.get("nodes")):
        identifier = _graph_identifier(entry.get("id"))
        path = entry.get("source_file")
        label = entry.get("label", entry.get("name"))
        if not identifier or not isinstance(path, str) or not isinstance(label, str):
            continue
        try:
            normalized = index_path(path)
        except ValueError:
            continue
        file = current.get(normalized)
        if file is None or not _cached_graph_fresh(root, file, entry, manifest, fresh):
            continue
        line, end = _graph_position(entry)
        nodes[identifier] = IndexRow(
            id="graphify:" + identifier,
            path=normalized,
            source_hash=file.content_hash,
            provenance="graphify:" + str(entry.get("_origin", "graph")),
            text=label,
            line=line,
            end_line=end,
            relation=str(entry.get("node_kind", entry.get("type", "symbol"))),
            confidence=_graph_confidence(entry),
        )
    edges: dict[str, IndexRow] = {}
    for key in ("links", "edges"):
        for entry in _graph_items(data.get(key)):
            source = _graph_identifier(entry.get("source"))
            target = _graph_identifier(entry.get("target"))
            left, right = nodes.get(source), nodes.get(target)
            if left is None or right is None:
                continue
            origin = entry.get("source_file", left.path)
            if not isinstance(origin, str):
                continue
            try:
                origin = index_path(origin)
            except ValueError:
                continue
            proof = entry
            if not any(
                isinstance(entry.get(name), str) and entry[name]
                for name in ("source_hash", "content_hash", "file_hash", "sha256")
            ):
                proof = {**entry, "source_hash": left.source_hash}
            if origin != left.path or not _cached_graph_fresh(
                root, current[origin], proof, manifest, fresh
            ):
                continue
            relation = str(entry.get("relation", "related"))
            line, end = _graph_position(entry)
            identity = f"{source}\0{target}\0{relation}\0{line}\0{end}"
            identifier = "graphify:" + sha256(identity.encode()).hexdigest()
            edges[identifier] = IndexRow(
                id=identifier,
                path=left.path,
                source_hash=left.source_hash,
                provenance="graphify:" + str(entry.get("_origin", "graph")),
                text=f"{source} -> {target}",
                line=line,
                end_line=end,
                target=right.path,
                relation=relation,
                confidence=_graph_confidence(entry),
            )
    return IndexStructure(
        tuple(sorted(nodes.values(), key=lambda row: row.id)),
        tuple(sorted(edges.values(), key=lambda row: row.id)),
        "graphify" if nodes else "unsupported",
    )
