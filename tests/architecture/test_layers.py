from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "cuanta"

ALLOWED: dict[str, frozenset[str]] = {
    "domain": frozenset({"domain"}),
    "ports": frozenset({"domain", "ports"}),
    "application": frozenset({"domain", "ports", "application"}),
    "adapters": frozenset({"domain", "ports", "adapters"}),
    "cli": frozenset({"cli", "application", "domain", "bootstrap", "tui"}),
    "tui": frozenset({"tui", "application", "domain", "bootstrap"}),
}


def _layer_of(module_path: Path) -> str | None:
    parts = module_path.relative_to(PACKAGE).parts
    if len(parts) < 2:
        return None
    return parts[0] if parts[0] in ALLOWED else None


def _imported_layers(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    layers: set[str] = set()
    for name in names:
        parts = name.split(".")
        if parts[0] != "cuanta" or len(parts) < 2:
            continue
        layers.add(parts[1])
    return layers


MODULES = sorted(
    path
    for path in PACKAGE.rglob("*.py")
    if "assets" not in path.relative_to(PACKAGE).parts and _layer_of(path) is not None
)


@pytest.mark.parametrize("path", MODULES, ids=lambda path: str(path.relative_to(PACKAGE)))
def test_dependency_rule(path: Path) -> None:
    layer = _layer_of(path)
    assert layer is not None
    forbidden = _imported_layers(path) - ALLOWED[layer]
    assert forbidden == set(), f"{layer} must not import {sorted(forbidden)}"


def test_domain_has_no_io_modules() -> None:
    io_modules = {"subprocess", "sqlite3", "socket", "http", "httpx", "shutil"}
    for path in (PACKAGE / "domain").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = {node.module.split(".")[0]}
            else:
                continue
            assert roots.isdisjoint(io_modules), f"{path.name} imports {roots & io_modules}"
