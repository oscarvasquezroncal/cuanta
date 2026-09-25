from __future__ import annotations

import json
import secrets
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.adapters.telemetry.listener_control import LocalListenerControl
from cuanta.adapters.telemetry.otlp_receiver import RunningListener, build_listener, next_free_port
from cuanta.domain.ledger import Run
from cuanta.ports.ledger import EventQuery

OTLP = Path(__file__).parents[1] / "fixtures" / "otlp"
pytestmark = pytest.mark.xdist_group("local-listener")


@pytest.fixture
def ledger_path(tmp_path: Path) -> Path:
    return tmp_path / "ledger.db"


@pytest.fixture
def listener(ledger_path: Path) -> Iterator[RunningListener]:
    running = build_listener(next_free_port(47000), lambda: SqliteLedger(ledger_path), False, "tok")
    running.start()
    yield running
    running.stop()


def _url(listener: RunningListener, path: str) -> str:
    return f"http://127.0.0.1:{listener.port}{path}"


def _wait_written(listener: RunningListener, count: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and listener.writer.stats.written < count:
        time.sleep(0.05)


def test_replayed_capture_lands_with_run_attribution(
    listener: RunningListener, ledger_path: Path
) -> None:
    ledger = SqliteLedger(ledger_path)
    ledger.add_run(Run(id="01TRACED", kind="mandate", trace_id="0af7651916cd43dd8448eb211c80319c"))
    payload = json.loads((OTLP / "claude_logs.json").read_text(encoding="utf-8"))
    for record_group in payload["resourceLogs"]:
        for scope in record_group["scopeLogs"]:
            for record in scope["logRecords"]:
                record["attributes"] = [
                    item for item in record["attributes"] if item["key"] != "cuanta.run_id"
                ]
        record_group["resource"]["attributes"] = [
            item
            for item in record_group["resource"]["attributes"]
            if item["key"] != "cuanta.run_id"
        ]
    response = httpx.post(_url(listener, "/v1/logs"), json=payload, timeout=5)
    assert response.status_code == 200
    expected = listener.writer.stats.received
    _wait_written(listener, expected)
    events = ledger.events(EventQuery(run_id="01TRACED"))
    assert len(events) == expected
    assert any(event.kind == "api_request" and event.input_tokens == 8 for event in events)
    ledger.close()


def test_resource_run_id_wins(listener: RunningListener, ledger_path: Path) -> None:
    payload = json.loads((OTLP / "claude_logs.json").read_text(encoding="utf-8"))
    httpx.post(_url(listener, "/v1/logs"), json=payload, timeout=5)
    _wait_written(listener, listener.writer.stats.received)
    ledger = SqliteLedger(ledger_path)
    assert ledger.events(EventQuery(run_id="01TESTRUN"))
    ledger.close()


def test_protobuf_gets_415_with_hint(listener: RunningListener) -> None:
    response = httpx.post(
        _url(listener, "/v1/logs"),
        content=b"\x0a\x00",
        headers={"Content-Type": "application/x-protobuf"},
        timeout=5,
    )
    assert response.status_code == 415
    assert "http/json" in response.json()["hint"]


def test_bad_json_and_unknown_path(listener: RunningListener) -> None:
    bad = httpx.post(
        _url(listener, "/v1/logs"),
        content=b"{",
        headers={"Content-Type": "application/json"},
        timeout=5,
    )
    assert bad.status_code == 400
    missing = httpx.post(_url(listener, "/v2/nope"), json={}, timeout=5)
    assert missing.status_code == 404


def test_health_and_token_protected_shutdown(listener: RunningListener) -> None:
    health = httpx.get(_url(listener, "/health"), timeout=5).json()
    assert health["ok"] is True
    denied = httpx.post(_url(listener, "/shutdown"), headers={"X-Cuanta-Token": "wrong"}, timeout=5)
    assert denied.status_code == 403


@pytest.mark.perf
def test_sustains_five_hundred_events_per_second(listener: RunningListener) -> None:
    record = {
        "timeUnixNano": "1767225600000000000",
        "body": {"stringValue": "claude_code.api_request"},
        "attributes": [
            {"key": "event.name", "value": {"stringValue": "api_request"}},
            {"key": "input_tokens", "value": {"intValue": "10"}},
            {"key": "model", "value": {"stringValue": "m"}},
        ],
    }
    batch = {
        "resourceLogs": [
            {"resource": {"attributes": []}, "scopeLogs": [{"logRecords": [record] * 50}]}
        ]
    }
    started = time.monotonic()
    with httpx.Client(timeout=5) as client:
        for _ in range(20):
            assert client.post(_url(listener, "/v1/logs"), json=batch).status_code == 200
    _wait_written(listener, 1000)
    elapsed = time.monotonic() - started
    assert listener.writer.stats.written == 1000
    assert 1000 / elapsed >= 500


def test_scoped_listener_starts_and_stops(tmp_path: Path) -> None:
    control = LocalListenerControl(
        tmp_path / ".cuanta", tmp_path, lambda: SqliteLedger(tmp_path / "l.db"), linger_s=0.0
    )
    assert not control.status().running
    with control.scoped(next_free_port(47100)) as status:
        assert status.running
        assert status.owned
        assert control.status().running
    assert not control.status().running
    assert not control.pidfile.exists()


def test_stop_without_listener_is_false(tmp_path: Path) -> None:
    control = LocalListenerControl(tmp_path, tmp_path, lambda: SqliteLedger(tmp_path / "l.db"))
    assert control.stop() is False
    assert secrets.token_hex(1)


def test_dropped_connections_are_silent_other_errors_are_not(
    listener: RunningListener, capsys: pytest.CaptureFixture[str]
) -> None:
    for error in (
        ConnectionResetError(10054, "forcibly closed"),
        BrokenPipeError(32, "broken pipe"),
        ConnectionAbortedError(10053, "aborted"),
    ):
        try:
            raise error
        except ConnectionError:
            listener.server.handle_error(None, ("127.0.0.1", 50000))
    assert capsys.readouterr().err == ""
    try:
        raise ValueError("real bug")
    except ValueError:
        listener.server.handle_error(None, ("127.0.0.1", 50000))
    assert "real bug" in capsys.readouterr().err


def test_reset_mid_request_prints_nothing(
    listener: RunningListener, capfd: pytest.CaptureFixture[str]
) -> None:
    import socket
    import struct

    client = socket.create_connection(("127.0.0.1", listener.port))
    client.sendall(
        b"POST /v1/logs HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
        b'Content-Length: 5000\r\n\r\n{"resourceLogs":'
    )
    client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    client.close()
    time.sleep(0.5)
    health = httpx.get(_url(listener, "/health"), timeout=5)
    assert health.status_code == 200
    assert "Traceback" not in capfd.readouterr().err
