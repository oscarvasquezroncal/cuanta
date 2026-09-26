from __future__ import annotations

import tomllib
from importlib import resources
from math import isfinite

from cuanta.domain.pricing import Price, PriceTable


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if isfinite(number) and number >= 0 else None


def load_prices() -> PriceTable:
    text = resources.files("cuanta").joinpath("assets", "prices.toml").read_text(encoding="utf-8")
    data = tomllib.loads(text)
    models: dict[str, Price] = {}
    raw = data.get("models")
    if isinstance(raw, dict):
        for name, values in raw.items():
            if isinstance(values, dict):
                input_price = _number(values.get("input"))
                output_price = _number(values.get("output"))
                if input_price is None or output_price is None:
                    continue
                models[str(name).lower()] = Price(
                    input=input_price,
                    output=output_price,
                    cache_write=_number(values.get("cache_write")),
                    cache_read=_number(values.get("cache_read")),
                )
    version = data.get("version")
    return PriceTable(
        models=models,
        verified_on=str(data.get("verified_on", "")),
        version=version if isinstance(version, int) else 0,
    )
