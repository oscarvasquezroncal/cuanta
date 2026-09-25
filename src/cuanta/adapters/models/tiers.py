from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib import resources

from cuanta.domain.models import Tier, TierTable, parse_tier


@dataclass(frozen=True, slots=True)
class ClaudeFacts:
    alias: str
    resolves: str
    display: str
    context: int
    efforts: tuple[str, ...]


def _data() -> dict[str, object]:
    text = resources.files("cuanta").joinpath("assets", "model_tiers.toml").read_text("utf-8")
    return tomllib.loads(text)


def load_tier_table() -> TierTable:
    data = _data()
    anchors: dict[str, Tier] = {}
    raw = data.get("tiers")
    if isinstance(raw, dict):
        for name, models in raw.items():
            tier = parse_tier(str(name))
            if tier is not None and isinstance(models, list):
                anchors.update({str(model).lower(): tier for model in models})
    version = data.get("version")
    return TierTable(
        version=version if isinstance(version, int) else 0,
        verified_on=str(data.get("verified_on", "")),
        anchors=anchors,
        aliases={fact.resolves: fact.alias for fact in claude_facts()},
    )


def claude_facts() -> tuple[ClaudeFacts, ...]:
    claude = _data().get("claude")
    models = claude.get("models") if isinstance(claude, dict) else None
    if not isinstance(models, dict):
        return ()
    facts: list[ClaudeFacts] = []
    for alias, values in models.items():
        if not isinstance(values, dict):
            continue
        context = values.get("context")
        efforts = values.get("efforts")
        facts.append(
            ClaudeFacts(
                alias=str(alias),
                resolves=str(values.get("resolves", alias)),
                display=str(values.get("display", alias)),
                context=context if isinstance(context, int) else 0,
                efforts=tuple(str(item) for item in efforts) if isinstance(efforts, list) else (),
            )
        )
    return tuple(facts)
