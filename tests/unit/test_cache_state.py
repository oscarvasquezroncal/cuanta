from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.application.cache_state import PrefixQuery
from cuanta.bootstrap import Container
from cuanta.domain.cache import (
    CacheState,
    PrefixState,
    cache_payload,
    first_request_cache,
    last_request_at,
    prefix_window,
)
from cuanta.domain.config import Config
from cuanta.domain.ledger import LedgerEvent, Run
from cuanta.domain.report import context_split
from cuanta.ports.ledger import EventQuery
from tests.ledger_fixture import RUN, fill


def test_first_request_cache_reports_a_partial_warm_hit() -> None:
    ledger = MemoryLedger()
    fill(ledger)
    cache = first_request_cache(ledger.events(EventQuery(run_id=RUN)))
    assert cache is not None
    assert cache.state is CacheState.WARM
    assert cache.total == 27_000
    assert cache.share == 20_000 / 27_000
    assert cache_payload(cache) == {
        "state": "warm",
        "cache_read_tokens": 20_000,
        "cache_write_tokens": 3_000,
        "context_tokens": 27_000,
        "share": 0.7407,
    }


def test_cold_or_missing_first_request_is_not_called_warm() -> None:
    cold = first_request_cache(
        [LedgerEvent(kind="api_request", input_tokens=500, cache_write_tokens=150)]
    )
    assert cold is not None
    assert cold.state is CacheState.COLD
    assert cold.share == 0
    assert first_request_cache([LedgerEvent(kind="result_usage", input_tokens=100)]) is None
    assert first_request_cache([LedgerEvent(kind="api_request")]) is None


def test_cache_uses_the_same_first_request_as_context_split() -> None:
    events = [
        LedgerEvent(kind="api_request", ts="2026-01-05T10:02:00Z", cache_read_tokens=100),
        LedgerEvent(kind="api_request", ts="2026-01-05T10:00:00Z", input_tokens=20),
    ]
    cache = first_request_cache(events)
    split = context_split(events, 0)
    assert cache is not None and split is not None
    assert cache.state is CacheState.COLD
    assert cache.total == split.first_request == 20


def test_prefix_window_uses_last_usage_event_and_measured_ttl() -> None:
    events = [
        LedgerEvent(kind="api_request", ts="2026-01-05T10:00:00Z", cache_write_tokens=100),
        LedgerEvent(kind="api_request", ts="2026-01-05T10:05:00Z", cache_read_tokens=100),
        LedgerEvent(kind="tool_result", ts="2026-01-05T10:06:00Z"),
    ]
    last = datetime(2026, 1, 5, 10, 5, tzinfo=UTC)
    assert last_request_at(events) == last
    warm = prefix_window(events, 300, last + timedelta(seconds=60))
    assert warm.state is PrefixState.WARM
    assert warm.until == last + timedelta(seconds=300)
    cold = prefix_window(events, 300, last + timedelta(seconds=300))
    assert cold.state is PrefixState.COLD
    assert cold.until == warm.until


def test_prefix_is_unknown_without_a_ttl_or_usable_request_time() -> None:
    now = datetime(2026, 1, 5, 10, 5, tzinfo=UTC)
    assert (
        prefix_window([LedgerEvent(kind="api_request", ts=now.isoformat())], 0, now).state
        is PrefixState.UNKNOWN
    )
    assert prefix_window([], 300, now).state is PrefixState.UNKNOWN
    assert (
        prefix_window([LedgerEvent(kind="api_request", ts="invalid")], 300, now).state
        is PrefixState.UNKNOWN
    )


def test_stream_result_time_is_used_when_request_telemetry_is_absent() -> None:
    event = LedgerEvent(kind="result_usage", ts="2026-01-05T10:05:00Z")
    assert last_request_at([event]) == datetime(2026, 1, 5, 10, 5, tzinfo=UTC)


def test_prefix_query_uses_the_latest_claude_run_with_requests() -> None:
    ledger = MemoryLedger()
    fill(ledger)
    ledger.add_run(Run("01ZNEWCLAUDE0000000000000", "mandate", engine="claude"))
    ledger.add_run(Run("01ZNEWCODEX00000000000000", "mandate", engine="codex"))
    now = datetime(2026, 1, 5, 10, 7, tzinfo=UTC)
    query = PrefixQuery(
        lambda: ledger,
        lambda: True,
        300,
        lambda: int(now.timestamp() * 1000),
        "subscription",
        lambda: False,
    )
    window = query.run("claude")
    assert window.state is PrefixState.WARM
    assert window.until == datetime(2026, 1, 5, 10, 10, tzinfo=UTC)
    assert query.run("codex").state is PrefixState.UNKNOWN
    assert (
        PrefixQuery(lambda: ledger, lambda: True, 0, lambda: 0, "subscription", lambda: False)
        .run("claude")
        .state
        is PrefixState.UNKNOWN
    )
    assert (
        PrefixQuery(lambda: ledger, lambda: False, 300, lambda: 0, "subscription", lambda: False)
        .run("claude")
        .state
        is PrefixState.UNKNOWN
    )
    assert (
        PrefixQuery(lambda: ledger, lambda: True, 300, lambda: 0, "", lambda: False)
        .run("claude")
        .state
        is PrefixState.UNKNOWN
    )


def test_prefix_query_needs_usage_in_the_first_request() -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("r", "mandate", engine="claude"))
    ledger.add_events([LedgerEvent(run_id="r", kind="api_request", ts="2026-01-05T10:00:00Z")])
    query = PrefixQuery(lambda: ledger, lambda: True, 300, lambda: 0, "subscription", lambda: False)
    assert query.run("claude").state is PrefixState.UNKNOWN


def test_first_request_without_cache_evidence_has_no_predicted_window() -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("r", "mandate", engine="claude"))
    ledger.add_events(
        [LedgerEvent(run_id="r", kind="api_request", ts="2026-01-05T10:00:00Z", input_tokens=500)]
    )
    now = datetime(2026, 1, 5, 10, 1, tzinfo=UTC)
    query = PrefixQuery(
        lambda: ledger,
        lambda: True,
        300,
        lambda: int(now.timestamp() * 1000),
        "subscription",
        lambda: False,
    )
    assert (
        prefix_window(ledger.events(EventQuery(run_id="r")), 300, now).state is PrefixState.UNKNOWN
    )
    assert query.run("claude").state is PrefixState.UNKNOWN


def test_subscription_measurement_is_unknown_when_api_key_appears() -> None:
    ledger = MemoryLedger()
    fill(ledger)
    now = datetime(2026, 1, 5, 10, 7, tzinfo=UTC)
    present = [False]
    query = PrefixQuery(
        lambda: ledger,
        lambda: True,
        300,
        lambda: int(now.timestamp() * 1000),
        "subscription",
        lambda: present[0],
    )
    assert query.run("claude").state is PrefixState.WARM
    present[0] = True
    assert query.run("claude").state is PrefixState.UNKNOWN
    api = PrefixQuery(
        lambda: ledger,
        lambda: True,
        300,
        lambda: int(now.timestamp() * 1000),
        "api",
        lambda: present[0],
    )
    present[0] = False
    assert api.run("claude").state is PrefixState.UNKNOWN
    present[0] = True
    assert api.run("claude").state is PrefixState.WARM


def test_prefix_query_rejects_a_different_or_missing_observed_model() -> None:
    ledger = MemoryLedger()
    fill(ledger)
    now = datetime(2026, 1, 5, 10, 7, tzinfo=UTC)

    def query(model: str, source: MemoryLedger = ledger) -> PrefixQuery:
        return PrefixQuery(
            lambda: source,
            lambda: True,
            300,
            lambda: int(now.timestamp() * 1000),
            "subscription",
            lambda: False,
            model,
        )

    assert query("claude-opus").run("claude").state is PrefixState.WARM
    assert query("claude-sonnet").run("claude").state is PrefixState.UNKNOWN
    missing = MemoryLedger()
    missing.add_run(Run("r", "mandate", engine="claude"))
    missing.add_events(
        [
            LedgerEvent(
                run_id="r", kind="api_request", ts="2026-01-05T10:05:00Z", cache_read_tokens=100
            )
        ]
    )
    assert query("claude-opus", missing).run("claude").state is PrefixState.UNKNOWN


def test_container_injects_runtime_api_key_presence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 1, 5, 10, 7, tzinfo=UTC)
    container = Container(
        tmp_path,
        Config(cache_ttl_s=300, cache_auth="subscription", cache_model="claude-opus"),
        clock=FixedClock(ms=int(now.timestamp() * 1000)),
    )
    try:
        ledger = container.ledger()
        fill(ledger)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
        assert container.prefix_query().run("claude").state is PrefixState.UNKNOWN
        monkeypatch.delenv("ANTHROPIC_API_KEY")
        assert container.prefix_query().run("claude").state is PrefixState.WARM
    finally:
        container.close()
