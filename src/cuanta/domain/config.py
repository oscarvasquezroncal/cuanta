from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, replace
from typing import Any, cast

DEFAULT_PORT = 4318


@dataclass(frozen=True, slots=True)
class Config:
    engine: str = "claude"
    instinct: str = "heuristic"
    emoji: bool = True
    theme: str = "auto"
    language: str = ""
    background: str = "solid"
    terminal_tip_dismissed: bool = False
    listener_mode: str = "auto"
    mandate_layout: str = "guided"
    run_session: str = "lean"
    onboarded: bool = False
    port: int = DEFAULT_PORT
    test_command: str = ""
    test_runner: str = ""
    budget_usd: float = 0.0
    exclusions: tuple[str, ...] = ()
    remote_consent: tuple[str, ...] = ()
    store_prompts: bool = False
    loop_max_iterations: int = 3
    model_tiers: tuple[tuple[str, str], ...] = ()
    routing: tuple[tuple[str, object], ...] = ()


KEY_MAP: dict[str, str] = {
    "engine": "engine",
    "instinct.backend": "instinct",
    "instinct.consent": "remote_consent",
    "ui.emoji": "emoji",
    "ui.theme": "theme",
    "ui.language": "language",
    "ui.background": "background",
    "terminal.tip_dismissed": "terminal_tip_dismissed",
    "ui.listener": "listener_mode",
    "ui.mandate_layout": "mandate_layout",
    "runs.session": "run_session",
    "ui.onboarded": "onboarded",
    "listener.port": "port",
    "test.command": "test_command",
    "test.runner": "test_runner",
    "budget.usd": "budget_usd",
    "detect.exclude": "exclusions",
    "privacy.store_prompts": "store_prompts",
    "loop.max_iterations": "loop_max_iterations",
}

ENV_MAP: dict[str, str] = {
    "CUANTA_ENGINE": "engine",
    "CUANTA_INSTINCT": "instinct",
    "CUANTA_EMOJI": "emoji",
    "CUANTA_THEME": "theme",
    "CUANTA_LANG": "language",
    "CUANTA_PORT": "port",
    "CUANTA_TEST_COMMAND": "test_command",
    "CUANTA_TEST_RUNNER": "test_runner",
    "CUANTA_BUDGET_USD": "budget_usd",
}

LAYOUTS = ("guided", "one_page")
LEGACY_LAYOUT_KEY = "ui.expert_mandate"
REPLACED_KEYS: dict[str, str] = {"ui.mandate_layout": LEGACY_LAYOUT_KEY}
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def flatten(table: Mapping[str, object], prefix: str = "") -> dict[str, object]:
    flat: dict[str, object] = {}
    for key, value in table.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat.update(flatten(value, f"{dotted}."))
        else:
            flat[dotted] = value
    return flat


def _coerce(field_name: str, value: object) -> object | None:
    template = Config()
    current = getattr(template, field_name)
    if isinstance(current, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in _TRUE | _FALSE:
            return value.lower() in _TRUE
        return None
    if isinstance(current, int):
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value)
        return None
    if isinstance(current, float):
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return None
        return None
    if isinstance(current, tuple):
        if isinstance(value, list | tuple):
            return tuple(str(item) for item in value)
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return None
    return str(value) if isinstance(value, str | int | float) else None


MODEL_TIERS_PREFIX = "models.tiers."
MERGED_TABLES = frozenset({"model_tiers", "routing"})
ROUTING_PREFIX = "routing."


def layer_from_table(table: Mapping[str, object]) -> dict[str, object]:
    layer: dict[str, object] = {}
    tiers: list[tuple[str, str]] = []
    models = table.get("models")
    raw = models.get("tiers") if isinstance(models, Mapping) else None
    if isinstance(raw, Mapping):
        tiers = [(str(name), value) for name, value in raw.items() if isinstance(value, str)]
    if tiers:
        layer["model_tiers"] = tuple(tiers)
    routing = table.get("routing")
    if isinstance(routing, Mapping):
        layer["routing"] = tuple(
            (key, tuple(value) if isinstance(value, list) else value)
            for key, value in flatten(routing).items()
        )
    for dotted, value in flatten(table).items():
        if dotted.startswith((MODEL_TIERS_PREFIX, ROUTING_PREFIX)):
            continue
        field_name = KEY_MAP.get(dotted)
        if field_name is None:
            continue
        coerced = _coerce(field_name, value)
        if coerced is not None:
            layer[field_name] = coerced
    legacy = legacy_layout(flatten(table))
    if legacy is not None and "mandate_layout" not in layer:
        layer["mandate_layout"] = legacy
    if layer.get("mandate_layout") not in (None, *LAYOUTS):
        del layer["mandate_layout"]
    return layer


def legacy_layout(flat: Mapping[str, object]) -> str | None:
    if LEGACY_LAYOUT_KEY not in flat:
        return None
    value = flat[LEGACY_LAYOUT_KEY]
    expert = value if isinstance(value, bool) else str(value).lower() in _TRUE
    return "one_page" if expert else "guided"


def layer_from_env(environ: Mapping[str, str]) -> dict[str, object]:
    layer: dict[str, object] = {}
    for variable, field_name in ENV_MAP.items():
        if variable in environ:
            coerced = _coerce(field_name, environ[variable])
            if coerced is not None:
                layer[field_name] = coerced
    return layer


def merge(layers_low_to_high: Sequence[Mapping[str, object]]) -> Config:
    config = Config()
    known = {item.name for item in fields(Config)}
    for layer in layers_low_to_high:
        updates = {key: value for key, value in layer.items() if key in known}
        if "remote_consent" in updates:
            added = cast(tuple[str, ...], updates["remote_consent"])
            updates["remote_consent"] = tuple(dict.fromkeys((*config.remote_consent, *added)))
        for name in MERGED_TABLES & set(updates):
            combined = dict(cast(tuple[tuple[str, object], ...], getattr(config, name)))
            combined.update(dict(cast(tuple[tuple[str, object], ...], updates[name])))
            updates[name] = tuple(combined.items())
        config = replace(config, **cast(dict[str, Any], updates))
    return config
