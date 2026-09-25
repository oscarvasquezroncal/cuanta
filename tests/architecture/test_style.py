from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCES = sorted(
    path
    for folder in (ROOT / "src" / "cuanta", ROOT / "tests", ROOT / "scripts" / "git")
    for path in folder.rglob("*.py")
    if not {"assets", "fixtures"} & set(path.relative_to(ROOT).parts)
)


def _comment_lines(source: str) -> list[int]:
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    return [token.start[0] for token in tokens if token.type == tokenize.COMMENT]


def _docstring_owners(source: str) -> list[str]:
    tree = ast.parse(source)
    owners: list[str] = []
    candidates: list[ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef] = [tree]
    candidates.extend(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    )
    for node in candidates:
        if ast.get_docstring(node, clean=False) is not None:
            owners.append(getattr(node, "name", "<module>"))
    return owners


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: str(path.relative_to(ROOT)))
def test_no_comments(path: Path) -> None:
    assert _comment_lines(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: str(path.relative_to(ROOT)))
def test_no_docstrings(path: Path) -> None:
    assert _docstring_owners(path.read_text(encoding="utf-8")) == []


def test_detector_catches_comment_and_docstring() -> None:
    sample = 'def f():\n    "doc"\n    return 1\n' + "x = 1  " + chr(35) + " note\n"
    assert _comment_lines(sample) == [4]
    assert _docstring_owners(sample) == ["f"]
