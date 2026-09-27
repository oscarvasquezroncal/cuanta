from __future__ import annotations

import math
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Check:
    file: str
    regex: str


@dataclass(frozen=True)
class Trial:
    name: str
    type: str
    what: str
    why: str
    tests: str
    out_of_scope: str
    depth: str
    engine: str
    cap: float
    shape: str = ""
    model: str = ""
    cross_engine: bool = False
    role_models: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    checks: tuple[Check, ...] = ()
    recovery_note: str = ""


@dataclass(frozen=True)
class Spec:
    project: Path
    total_cap: float
    trials: tuple[Trial, ...]


def amount(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("Caps must be finite positive numbers")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError("Caps must be finite positive numbers")
    return result


def text(values: dict[str, Any], name: str, default: str | None = None) -> str:
    value = values.get(name, default)
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError(f"Missing or invalid {name}")
    return value


def strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() or "\0" in item for item in value
    ):
        raise ValueError("Expected a list of nonempty strings")
    return tuple(value)


def relative(value: str) -> str:
    path = Path(value)
    parts = value.replace("\\", "/").split("/")
    if (
        path.is_absolute()
        or ":" in value
        or any(part in {"", ".", ".."} for part in parts)
        or any(part.casefold() in {".git", ".cuanta", "node_modules"} for part in parts)
    ):
        raise ValueError("Expected a relative path outside protected directories")
    return "/".join(parts)


def parse_trial(raw: Any) -> Trial:
    if not isinstance(raw, dict):
        raise ValueError("Each trial must be a table")
    allowed = set(Trial.__dataclass_fields__) | {"cap_usd"}
    allowed.discard("cap")
    if raw.keys() - allowed:
        raise ValueError(f"Unknown trial keys: {sorted(raw.keys() - allowed)}")
    required = {
        name: text(raw, name)
        for name in ("name", "type", "what", "why", "tests", "out_of_scope", "depth", "engine")
    }
    if not re.fullmatch(r"[A-Za-z0-9_-]+", required["name"]):
        raise ValueError("Trial names must be safe, unique identifiers")
    if required["type"] not in {"investigation", "bug", "feature", "refactor", "docs"}:
        raise ValueError("Unsupported trial type")
    if required["depth"] not in {"quick", "normal", "deep"}:
        raise ValueError("Unsupported depth")
    if required["engine"] not in {"claude", "codex"}:
        raise ValueError("Live trials support Claude and Codex only")
    cross = raw.get("cross_engine", False)
    if not isinstance(cross, bool):
        raise ValueError("cross_engine must be boolean")
    shape = raw.get("shape", "")
    if shape not in {"", "single", "pipeline"}:
        raise ValueError("Unsupported shape")
    models = strings(raw.get("role_models", []))
    if any(not re.fullmatch(r"[a-z_]+=[A-Za-z0-9._:/-]+", model) for model in models):
        raise ValueError("Role models use role=model")
    checks: list[Check] = []
    raw_checks = raw.get("checks", [])
    if not isinstance(raw_checks, list):
        raise ValueError("Checks must be a list")
    for check in raw_checks:
        if not isinstance(check, dict) or set(check) != {"file", "regex"}:
            raise ValueError("Checks require file and regex")
        filename = relative(text(check, "file"))
        pattern = text(check, "regex")
        try:
            re.compile(pattern)
        except re.error as error:
            raise ValueError(f"Invalid check regex: {error}") from error
        checks.append(Check(filename, pattern))
    optional = {key: text(raw, key) if key in raw else "" for key in ("model", "recovery_note")}
    return Trial(
        **required,
        **optional,
        cap=amount(raw.get("cap_usd")),
        shape=shape,
        cross_engine=cross,
        role_models=models,
        acceptance=strings(raw.get("acceptance", [])),
        checks=tuple(checks),
    )


def load(path: Path) -> Spec:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if set(data) != {"project", "total_cap_usd", "trials"}:
        raise ValueError("Spec requires project, total_cap_usd and trials only")
    project = Path(text(data, "project"))
    if not project.is_absolute():
        project = path.resolve().parent / project
    cap = amount(data["total_cap_usd"])
    if not isinstance(data["trials"], list) or not data["trials"]:
        raise ValueError("Spec requires at least one trial")
    trials = tuple(parse_trial(raw) for raw in data["trials"])
    if len({trial.name for trial in trials}) != len(trials):
        raise ValueError("Duplicate trial names")
    if sum(trial.cap for trial in trials) > cap + 1e-9:
        raise ValueError("Trial caps exceed the total cap")
    return Spec(project.resolve(), cap, trials)
