from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "docs" / "FLAGS.md"
KEY = re.compile(r"`([A-Z][A-Z0-9_]+)`")
CUANTA_LITERAL = re.compile(r"[\"'](CUANTA_[A-Z0-9_]+)[\"']")


def _registry_keys() -> list[str]:
    return [
        key
        for line in REGISTRY.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `")
        for key in KEY.findall(line.split("|", maxsplit=2)[1])
    ]


def test_flags_registry_has_no_duplicate_keys() -> None:
    keys = _registry_keys()
    assert len(keys) == len(set(keys))


def test_every_cuanta_env_literal_in_src_is_documented() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src" / "cuanta").rglob("*.py")
        if "assets" not in path.parts
    )
    assert set(CUANTA_LITERAL.findall(source)) <= set(_registry_keys())


def test_every_documented_cuanta_env_is_read_somewhere() -> None:
    files = (
        *((ROOT / "src" / "cuanta").rglob("*.py")),
        *(path for path in (ROOT / "tests").rglob("*.py") if "fixtures" not in path.parts),
        ROOT / ".github" / "workflows" / "ci.yml",
    )
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert all(key in text for key in _registry_keys() if key.startswith("CUANTA_"))
