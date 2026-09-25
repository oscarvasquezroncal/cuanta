from __future__ import annotations

import tomllib
from collections.abc import Sequence
from pathlib import Path

import tomlkit

from cuanta.adapters.system.platform import config_dir
from cuanta.domain.config import REPLACED_KEYS

PROJECT_DIR = ".cuanta"
CONFIG_NAME = "config.toml"


def read_table(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            return dict(tomllib.load(handle))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def global_config_path() -> Path:
    return config_dir() / CONFIG_NAME


def project_config_path(project: Path) -> Path:
    return project / PROJECT_DIR / CONFIG_NAME


def set_value(path: Path, dotted: str, value: object) -> None:
    set_path(path, tuple(dotted.split(".")), value)
    legacy = REPLACED_KEYS.get(dotted)
    if legacy is not None:
        remove_path(path, tuple(legacy.split(".")))


def remove_path(path: Path, parts: Sequence[str]) -> None:
    if not path.is_file():
        return
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    table: tomlkit.TOMLDocument | tomlkit.items.Table = document
    for part in parts[:-1]:
        existing = table.get(part)
        if not isinstance(existing, tomlkit.items.Table):
            return
        table = existing
    if parts[-1] in table:
        del table[parts[-1]]
        path.write_text(tomlkit.dumps(document), encoding="utf-8")


def set_path(path: Path, parts: Sequence[str], value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = (
        tomlkit.parse(path.read_text(encoding="utf-8")) if path.is_file() else tomlkit.document()
    )
    table: tomlkit.TOMLDocument | tomlkit.items.Table = document
    for part in parts[:-1]:
        existing = table.get(part)
        if not isinstance(existing, tomlkit.items.Table):
            existing = tomlkit.table()
            table[part] = existing
        table = existing
    table[parts[-1]] = value
    path.write_text(tomlkit.dumps(document), encoding="utf-8")
