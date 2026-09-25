from __future__ import annotations

import json
from pathlib import Path

from cuanta.domain.affected import file_neighbours

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
