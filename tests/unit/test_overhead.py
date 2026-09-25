from __future__ import annotations

import json

from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.overhead import (
    attributes,
    iso_ms,
    overhead_messages,
    session_overhead,
    spawn_event,
    startup_of,
    startup_payload,
)

PLUGINS = (
    ("claude-mem", "12.1.0"),
    ("caveman", "63e797cd753b"),
    ("ui-ux-pro-max", "2.5.0"),
    ("claude-agent-forge", "0.3.1"),
    ("agents-md", "1.0.0"),
)
SERVERS = (
    ("claude.ai Gmail", "connected", "", 410),
    ("claude.ai Google Drive", "connected", "", 380),
    ("claude.ai Claude Docs", "connected", "", 350),
    ("stitch", "connected", "", 900),
    ("plugin:claude-mem:mcp-search", "connected", "", 620),
    ("magic", "failed", "not authenticated", 3200),
)
HOOKS = (
    ("SessionStart:caveman", 120, 3200),
    ("SessionStart:claude-mem", 540, 5400),
    ("SessionStart:startup", 118, 906),
    ("SessionStart:compact", 100, 0),
)


def otlp(kind: str, ts: str, values: dict[str, object]) -> LedgerEvent:
    listed = [
        {
            "key": key,
            "value": {"intValue": value} if isinstance(value, int) else {"stringValue": value},
        }
        for key, value in values.items()
    ]
    raw = json.dumps({"attributes": listed})
    return LedgerEvent(run_id="01M39WA3N3P9RNEDQX35DXTDJH", kind=kind, ts=ts, raw=raw)


def user_run() -> list[LedgerEvent]:
    events: list[LedgerEvent] = []
    for name, version in PLUGINS:
        events.append(
            otlp(
                "plugin_loaded",
                "2026-09-24T10:00:00.100+00:00",
                {"plugin.name": name, "plugin.version": version},
            )
        )
    for name, status, error, duration in SERVERS:
        values: dict[str, object] = {"server_name": name, "status": status, "duration_ms": duration}
        if error:
            values["error"] = error
        events.append(otlp("mcp_server_connection", "2026-09-24T10:00:00.200+00:00", values))
    for name, duration, chars in HOOKS:
        events.append(
            otlp(
                "hook_execution_complete",
                "2026-09-24T10:00:00.300+00:00",
                {"hook_name": name, "duration_ms": duration, "additional_context_length": chars},
            )
        )
    events.append(
        LedgerEvent(
            run_id="01M39WA3N3P9RNEDQX35DXTDJH",
            kind="api_request",
            ts="2026-09-24T10:00:04.100+00:00",
            input_tokens=4,
            cache_read_tokens=20_482,
            cache_write_tokens=32_625,
        )
    )
    return events


def test_the_users_run_breaks_down_into_plugins_servers_and_hooks() -> None:
    overhead = session_overhead(user_run(), 5_641)
    assert len(overhead.plugins) == 5
    assert "claude-agent-forge 0.3.1" in overhead.plugins
    assert len(overhead.servers) == 6
    failed = overhead.failed_servers
    assert [server.name for server in failed] == ["magic"]
    assert failed[0].error == "not authenticated"
    assert failed[0].duration_ms == 3200
    assert len(overhead.hooks) == 4
    assert overhead.hook_ms == 878
    assert overhead.hook_chars == 9_506
    assert overhead.split is not None
    assert overhead.split.first_request == 53_111
    assert overhead.split.fixed_share > 0.95
    assert overhead.startup_ms == 4_000
    assert not overhead.empty


def test_a_lean_run_has_no_overhead_items() -> None:
    lean = [event for event in user_run() if event.kind == "api_request"]
    overhead = session_overhead(lean, 5_641)
    assert overhead.empty
    assert overhead.startup_ms == 0


def test_attributes_accept_flat_maps_and_bad_json() -> None:
    assert attributes('{"attributes": {"a": 1}}') == {"a": 1}
    assert attributes("not json") == {}
    assert attributes("") == {}


def lean_start() -> list[LedgerEvent]:
    return [
        spawn_event("r", "t", 1_790_306_064_000),
        LedgerEvent(run_id="r", kind="managed_settings_resolved", ts="2026-09-25T03:14:26.573Z"),
        LedgerEvent(run_id="r", kind="plugin_loaded", ts="2026-09-25T03:14:26.611Z"),
        LedgerEvent(run_id="r", kind="user_prompt", ts="2026-09-25T03:14:26.959Z"),
        LedgerEvent(run_id="r", kind="metric:claude_code.session.count", ts="2026-09-25T03:14:20Z"),
        LedgerEvent(
            run_id="r",
            kind="api_request",
            ts="2026-09-25T03:14:47.109Z",
            duration_ms=20_121,
            ttft_ms=6_400,
            output_tokens=1_540,
        ),
        LedgerEvent(run_id="r", kind="result_usage", ts="2026-09-25T03:14:10Z"),
    ]


def test_the_start_up_splits_into_spawn_setup_queue_and_the_first_request() -> None:
    startup = startup_of(lean_start())
    assert startup is not None
    assert startup.spawn_ms == 2_573
    assert startup.setup_ms == 386
    assert startup.queue_ms == 29
    assert startup.request_ms == 20_121
    assert startup.ttft_ms == 6_400
    assert startup.output_tokens == 1_540
    assert startup.before_request_ms == 2_988
    assert session_overhead(lean_start(), 0).startup_ms == 2_988
    params = dict(overhead_messages(session_overhead(lean_start(), 0))[-1].params)
    assert params["before"] == "3.0 s"
    assert params["spawn"] == "2.6 s"
    assert params["request"] == "20.1 s"
    assert params["ttft"] == "6.4 s"
    payload = startup_payload(startup)
    assert payload is not None
    assert payload["spawn_to_session_ms"] == 2_573
    assert payload["first_token_ms"] == 6_400


def test_without_a_spawn_or_a_request_the_start_up_is_partial_or_absent() -> None:
    no_spawn = [event for event in lean_start() if event.kind != "spawn"]
    startup = startup_of(no_spawn)
    assert startup is not None
    assert startup.spawn_ms is None
    assert startup_of([event for event in no_spawn if event.kind != "api_request"]) is None
    assert startup_payload(None) is None


def test_spawn_events_carry_millisecond_timestamps() -> None:
    assert iso_ms(1_790_306_064_000) == "2026-09-25T03:14:24.000Z"
    event = spawn_event("r", "t", 1_790_306_064_123)
    assert (event.kind, event.source, event.ts) == ("spawn", "cuanta", "2026-09-25T03:14:24.123Z")
