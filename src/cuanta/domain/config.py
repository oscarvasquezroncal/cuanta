from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, replace
from typing import Any, cast

from cuanta.domain.read_discipline import READ_LINE_LIMIT

DEFAULT_PORT = 4318
SCOUT_MODES = ("native", "launch")
DOCS_MODES = ("auto", "on", "off")
DEFAULT_SCOUT_THRESHOLD = 0.35


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
    git_workflow: str = "branches"
    onboarded: bool = False
    port: int = DEFAULT_PORT
    test_command: str = ""
    test_runner: str = ""
    budget_usd: float = 0.0
    max_turns: int = 0
    cache_ttl_s: int = 0
    cache_auth: str = ""
    cache_engine_version: str = ""
    cache_measured_on: str = ""
    cache_model: str = ""
    exclusions: tuple[str, ...] = ()
    remote_consent: tuple[str, ...] = ()
    store_prompts: bool = False
    loop_max_iterations: int = 3
    model_tiers: tuple[tuple[str, str], ...] = ()
    routing: tuple[tuple[str, object], ...] = ()
    instinct_share_paths: bool = False
    instinct_envelope: bool = False
    read_discipline: bool = False
    pipeline_read_discipline: bool = True
    read_max_lines: int = READ_LINE_LIMIT
    index_tools: bool = False
    pipeline_index_tools: bool = True
    index_enabled: bool = True
    pack_enabled: bool = True
    scout_mode: str = "native"
    scout_threshold: float = DEFAULT_SCOUT_THRESHOLD
    docs_mode: str = "auto"
    governor: bool = True


KEY_MAP: dict[str, str] = {
    "engine": "engine",
    "instinct.backend": "instinct",
    "instinct.consent": "remote_consent",
    "instinct.share_paths": "instinct_share_paths",
    "instinct.envelope": "instinct_envelope",
    "ui.emoji": "emoji",
    "ui.theme": "theme",
    "ui.language": "language",
    "ui.background": "background",
    "terminal.tip_dismissed": "terminal_tip_dismissed",
    "ui.listener": "listener_mode",
    "ui.mandate_layout": "mandate_layout",
    "runs.session": "run_session",
    "runs.read_discipline": "read_discipline",
    "runs.pipeline_read_discipline": "pipeline_read_discipline",
    "runs.read_max_lines": "read_max_lines",
    "runs.index_tools": "index_tools",
    "runs.pipeline_index_tools": "pipeline_index_tools",
    "runs.index_enabled": "index_enabled",
    "runs.pack_enabled": "pack_enabled",
    "runs.scout_mode": "scout_mode",
    "runs.scout_threshold": "scout_threshold",
    "runs.docs": "docs_mode",
    "runs.governor": "governor",
    "git.workflow": "git_workflow",
    "ui.onboarded": "onboarded",
    "listener.port": "port",
    "test.command": "test_command",
    "test.runner": "test_runner",
    "budget.usd": "budget_usd",
    "runs.max_turns": "max_turns",
    "cache.ttl_s": "cache_ttl_s",
    "cache.auth": "cache_auth",
    "cache.engine_version": "cache_engine_version",
    "cache.measured_on": "cache_measured_on",
    "cache.model": "cache_model",
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
    "CUANTA_MAX_TURNS": "max_turns",
}

LAYOUTS = ("guided", "one_page")
GIT_WORKFLOWS = ("branches", "trunk")
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
    if layer.get("git_workflow") not in (None, *GIT_WORKFLOWS):
        del layer["git_workflow"]
    lines = layer.get("read_max_lines")
    if isinstance(lines, int) and lines < 1:
        del layer["read_max_lines"]
    if layer.get("scout_mode") not in (None, *SCOUT_MODES):
        del layer["scout_mode"]
    if layer.get("docs_mode") not in (None, *DOCS_MODES):
        del layer["docs_mode"]
    threshold = layer.get("scout_threshold")
    if isinstance(threshold, float) and not 0.0 < threshold <= 1.0:
        del layer["scout_threshold"]
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
