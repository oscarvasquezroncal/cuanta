from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

TUI = Path(__file__).resolve().parents[2] / "src" / "cuanta" / "tui"
WIDGETS = frozenset(
    {
        "Button",
        "Checkbox",
        "Label",
        "RadioButton",
        "Static",
        "SystemCommand",
        "notify",
        "styled",
        "Binding",
    }
)
TEXT_KEYWORDS = frozenset({"placeholder", "prompt", "label", "description", "tooltip"})
WORD = re.compile(r"[A-Za-z]{2,}")
ALLOWED = frozenset({"cuanta"})
MODULES = sorted(TUI.rglob("*.py"))
MIRRORS = frozenset({"detail", "reason", "question", "source", "action", "text"})
TRANSLATORS = frozenset({"message", "keyed", "_question", "_answer"})
MIRROR_OWNERS = frozenset(
    {"check", "step", "status", "event", "leak", "item", "row", "use", "cost", "suggestion"}
)


def _name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _literal_words(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        words = [word for word in WORD.findall(node.value) if word.lower() not in ALLOWED]
        return node.value if words else None
    return None


def hard_coded(path: Path) -> list[str]:
    found: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        name = _name(call)
        candidates: list[ast.AST] = []
        if name in WIDGETS:
            candidates.extend(call.args[:1] if name != "Binding" else call.args[2:3])
        candidates.extend(kw.value for kw in call.keywords if kw.arg in TEXT_KEYWORDS)
        for node in candidates:
            text = _literal_words(node)
            if text is not None:
                found.append(f"{path.name}:{call.lineno} {name}({text!r})")
    return found


def english_mirrors(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    translated: set[int] = set()
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        if _name(call) in TRANSLATORS:
            translated.update(id(child) for arg in call.args for child in ast.walk(arg))
    found: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Load)
            and node.attr in MIRRORS
            and isinstance(node.value, ast.Name)
            and node.value.id in MIRROR_OWNERS
            and id(node) not in translated
        ):
            found.append(f"{path.name}:{node.lineno} {node.value.id}.{node.attr}")
    return found


@pytest.mark.parametrize("path", MODULES, ids=lambda path: str(path.relative_to(TUI)))
def test_tui_renders_messages_not_their_english_mirror(path: Path) -> None:
    assert english_mirrors(path) == []


def test_the_mirror_guard_catches_raw_detail(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        "Static(check.detail)\nStatic(t.message(check.message, check.detail))\n",
        encoding="utf-8",
    )
    assert english_mirrors(sample) == ["sample.py:1 check.detail"]


@pytest.mark.parametrize("path", MODULES, ids=lambda path: str(path.relative_to(TUI)))
def test_tui_widgets_take_text_from_the_catalog(path: Path) -> None:
    assert hard_coded(path) == []


def test_the_guard_catches_literals(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        'Button("Run tests")\nStatic("")\nInput(placeholder="Search")\nButton(t("x"))\n',
        encoding="utf-8",
    )
    assert len(hard_coded(sample)) == 2
