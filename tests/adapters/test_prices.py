from __future__ import annotations

from pathlib import Path

import pytest

from cuanta.adapters.system import prices
from cuanta.domain.pricing import Price, PriceTable


def load_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> PriceTable:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "prices.toml").write_text(text, encoding="utf-8")
    monkeypatch.setattr("cuanta.adapters.system.prices.resources.files", lambda package: tmp_path)
    return prices.load_prices()


def test_price_loader_keeps_absent_cache_rates_unknown_and_explicit_zero_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = load_table(
        tmp_path,
        monkeypatch,
        "[models.partial]\ninput = 1.0\noutput = 2.0\n"
        "[models.free]\ninput = 0.0\noutput = 0.0\ncache_write = 0.0\ncache_read = 0.0\n",
    )
    assert table.lookup("partial") == Price(1.0, 2.0, None, None)
    assert table.lookup("free") == Price(0.0, 0.0, 0.0, 0.0)


@pytest.mark.parametrize("field", ["input", "output"])
@pytest.mark.parametrize("raw", [None, '"unknown"', "true", "-0.5", "inf", "nan"])
def test_price_loader_excludes_rows_with_missing_or_invalid_required_rates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, raw: str | None
) -> None:
    other = "output" if field == "input" else "input"
    text = f"[models.invalid]\n{other} = 1.0\n"
    if raw is not None:
        text += f"{field} = {raw}\n"
    assert load_table(tmp_path, monkeypatch, text).lookup("invalid") is None


@pytest.mark.parametrize("field", ["cache_write", "cache_read"])
@pytest.mark.parametrize("raw", ['"unknown"', "true", "-0.5", "inf", "nan"])
def test_price_loader_keeps_invalid_cache_rates_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, raw: str
) -> None:
    text = f"[models.partial]\ninput = 1.0\noutput = 2.0\n{field} = {raw}\n"
    assert load_table(tmp_path, monkeypatch, text).lookup("partial") == Price(1.0, 2.0, None, None)
