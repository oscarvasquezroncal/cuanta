from __future__ import annotations

from pathlib import Path

import pytest

from cuanta.adapters.instinct.jev import JevInstinct
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.application.doctor import instinct_check
from cuanta.domain.ledger import Decision
from cuanta.domain.progress import Status
from tests.tui.fakes import DETECTION


@pytest.mark.parametrize(
    ("key", "base", "fragment"),
    [
        ("apikey_example", "https://openrouter.ai", "apikey_ key with OpenRouter URL"),
        ("sk-or-example", "https://api.typesafe.ai", "sk-or- key with TypeSafe URL"),
    ],
)
def test_doctor_warns_for_jev_key_url_mismatch(
    monkeypatch: pytest.MonkeyPatch, key: str, base: str, fragment: str
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", key)
    monkeypatch.setenv("TYPESAFE_BASE_URL", base)
    backend = JevInstinct()
    checks = instinct_check(
        "jev", backend.available, backend.setup_warning, MemoryLedger, lambda: False
    )(DETECTION)
    assert any(check.status is Status.WARN and fragment in check.detail for check in checks)


def test_doctor_shows_last_jev_fallback_error(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    ledger = SqliteLedger(path)
    ledger.add_decision(
        Decision(
            run_id="R",
            backend="heuristic",
            primitive="choose",
            question="q",
            options='["a"]',
            answer="a",
            confidence=1.0,
            latency_ms=1,
            fallback_error="jev answered HTTP 503",
            fallback_from="jev",
        )
    )
    ledger.close()
    backend = JevInstinct()
    checks = instinct_check(
        "jev",
        backend.available,
        backend.setup_warning,
        lambda: SqliteLedger(path),
        path.is_file,
    )(DETECTION)
    assert any(
        check.name == "instinct jev fallback" and "jev answered HTTP 503" in check.detail
        for check in checks
    )
