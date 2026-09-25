from __future__ import annotations

import tomllib
from importlib import resources

from cuanta.domain.pricing import Price, PriceTable


def _number(value: object) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def load_prices() -> PriceTable:
    text = resources.files("cuanta").joinpath("assets", "prices.toml").read_text(encoding="utf-8")
    data = tomllib.loads(text)
    models: dict[str, Price] = {}
    raw = data.get("models")
    if isinstance(raw, dict):
        for name, values in raw.items():
            if isinstance(values, dict):
                models[str(name).lower()] = Price(
                    input=_number(values.get("input")),
                    output=_number(values.get("output")),
                    cache_write=_number(values.get("cache_write")),
                    cache_read=_number(values.get("cache_read")),
                )
    version = data.get("version")
    return PriceTable(
        models=models,
        verified_on=str(data.get("verified_on", "")),
        version=version if isinstance(version, int) else 0,
    )
