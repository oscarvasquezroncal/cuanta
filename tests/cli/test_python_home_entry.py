from __future__ import annotations

import ast
import importlib
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from cuanta.cli import hooks

HOME = "PYTHONHOME"
MARKER = "UV_INTERNAL__PYTHONHOME"
ROOT = Path(__file__).resolve().parents[2]
ENTRY_POINTS = [
    ("src/cuanta/cli/app.py", "main"),
    ("src/cuanta/cli/hooks.py", "main"),
    ("src/cuanta/adapters/graph/index_graph.py", "__main__"),
    ("src/cuanta/adapters/system/sandbox_cleanup.py", "__main__"),
]


def entry_body(tree: ast.Module, owner: str) -> list[ast.stmt]:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == owner:
            return node.body
        if isinstance(node, ast.If) and owner == "__main__" and owner in ast.unparse(node.test):
            return node.body
    raise AssertionError(f"no {owner} entry")


def leading_call(body: list[ast.stmt]) -> str:
    statement = next(item for item in body if not isinstance(item, ast.Import | ast.ImportFrom))
    value = statement.value if isinstance(statement, ast.Expr | ast.Assign) else None
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
        return value.func.id
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute):
        return value.func.attr
    return ast.unparse(statement)


def dispatched_environment(monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:
    module = importlib.import_module("cuanta.cli.app")
    seen: list[dict[str, str | None]] = []

    def dispatch(args: Sequence[str], prog_name: str) -> None:
        seen.append({HOME: os.environ.get(HOME), MARKER: os.environ.get(MARKER)})

    monkeypatch.setattr(module, "app", dispatch)
    monkeypatch.setattr(module, "_prefer_utf8_when_piped", lambda: None)
    monkeypatch.setattr(sys, "argv", ["cuanta", "--version"])
    module.main()
    assert len(seen) == 1
    return seen[0]


@pytest.mark.parametrize(("relative", "owner"), ENTRY_POINTS)
def test_every_entry_point_drops_the_inherited_home_first(relative: str, owner: str) -> None:
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    assert leading_call(entry_body(tree, owner)) == "drop_inherited_python_home"


def test_the_hook_entry_drops_a_uv_set_home_before_anything_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    injected = str(tmp_path / "uv-python")
    monkeypatch.setenv(HOME, injected)
    monkeypatch.setenv(MARKER, injected)
    monkeypatch.setattr(sys, "argv", ["cuanta-hook"])
    assert hooks.main() == 2
    assert HOME not in os.environ
    assert MARKER not in os.environ


def test_the_hook_entry_keeps_a_home_the_user_chose_for_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chosen = str(tmp_path / "user-python")
    monkeypatch.delenv(MARKER, raising=False)
    monkeypatch.setenv(HOME, chosen)
    monkeypatch.setattr(sys, "argv", ["cuanta-hook"])
    assert hooks.main() == 2
    assert os.environ.get(HOME) == chosen


def test_the_cli_entry_drops_a_uv_set_home_before_any_command_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    injected = str(tmp_path / "uv-python")
    monkeypatch.setenv(HOME, injected)
    monkeypatch.setenv(MARKER, injected)
    assert dispatched_environment(monkeypatch) == {HOME: None, MARKER: None}


def test_the_cli_entry_hands_a_user_home_to_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chosen = str(tmp_path / "user-python")
    monkeypatch.delenv(MARKER, raising=False)
    monkeypatch.setenv(HOME, chosen)
    assert dispatched_environment(monkeypatch) == {HOME: chosen, MARKER: None}
