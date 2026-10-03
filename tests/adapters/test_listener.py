from __future__ import annotations

import json
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.adapters.system.log_file import LogFile
from cuanta.adapters.telemetry import claude_code_mapper, listener_control, otlp_receiver
from cuanta.adapters.telemetry.listener_control import LocalListenerControl
from cuanta.adapters.telemetry.otlp_json import LogRecord
from cuanta.adapters.telemetry.otlp_receiver import (
    EventWriter,
    RunningListener,
    build_listener,
    map_payload,
    next_free_port,
)
from cuanta.bootstrap import Container
from cuanta.domain.errors import EnvironmentFailure
from cuanta.domain.ledger import LedgerEvent, Run
from cuanta.domain.telemetry import UNREADABLE_KIND
from cuanta.ports.ledger import EventQuery, Ledger
from cuanta.ports.listener import ListenerStatus

OTLP = Path(__file__).parents[1] / "fixtures" / "otlp"
REDACTION = OTLP / "claude_redaction.json"
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


def test_scoped_listener_moves_on_when_a_free_looking_port_is_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = LocalListenerControl(
        tmp_path / ".cuanta", tmp_path, lambda: SqliteLedger(tmp_path / "l.db"), linger_s=0.0
    )
    taken = next_free_port(47200)
    monkeypatch.setattr(otlp_receiver, "port_is_free", lambda port: True)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", taken))
        holder.listen()
        with control.scoped(taken) as status:
            assert status.running
            assert status.port != taken
    assert not control.pidfile.exists()


def test_stop_without_listener_is_false(tmp_path: Path) -> None:
    control = LocalListenerControl(tmp_path, tmp_path, lambda: SqliteLedger(tmp_path / "l.db"))
    assert control.stop() is False
    assert secrets.token_hex(1)


@pytest.mark.parametrize("echo", [False, True])
def test_dropped_connections_are_silent_and_other_errors_go_to_the_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], echo: bool
) -> None:
    log = tmp_path / "logs" / "listener.log"
    running = build_listener(
        next_free_port(47000),
        lambda: SqliteLedger(tmp_path / "l.db"),
        False,
        "tok",
        log=LogFile(log, echo=echo),
    )
    try:
        for error in (
            ConnectionResetError(10054, "forcibly closed"),
            BrokenPipeError(32, "broken pipe"),
            ConnectionAbortedError(10053, "aborted"),
        ):
            try:
                raise error
            except ConnectionError:
                running.server.handle_error(None, ("127.0.0.1", 50000))
        assert capsys.readouterr().err == ""
        assert not log.exists()
        try:
            raise ValueError("real bug")
        except ValueError:
            running.server.handle_error(None, ("127.0.0.1", 50000))
    finally:
        running.server.server_close()
    err = capsys.readouterr().err
    assert ("real bug" in err) is echo
    assert "Exception occurred" not in err
    logged = log.read_text(encoding="utf-8")
    assert "request from 127.0.0.1" in logged
    assert "Traceback" in logged
    assert "ValueError: real bug" in logged


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


def _redaction() -> Any:
    return json.loads(REDACTION.read_text(encoding="utf-8"))


def _break_bash_records(monkeypatch: pytest.MonkeyPatch) -> None:
    original = claude_code_mapper.map_log

    def failing(record: LogRecord, keep_prompts: bool = False) -> LedgerEvent:
        if record.attributes.get("tool_name") == "Bash":
            raise ValueError("mapper bug")
        return original(record, keep_prompts)

    monkeypatch.setattr(claude_code_mapper, "map_log", failing)


def test_a_record_redaction_touches_lands_without_a_traceback(
    listener: RunningListener, ledger_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    response = httpx.post(_url(listener, "/v1/logs"), json=_redaction(), timeout=5)
    assert response.status_code == 200
    _wait_written(listener, 2)
    ledger = SqliteLedger(ledger_path)
    kinds = sorted(event.kind for event in ledger.events(EventQuery(run_id="01REALRUN")))
    ledger.close()
    assert kinds == ["api_request", "tool_result"]
    assert "Traceback" not in capfd.readouterr().err


@pytest.mark.parametrize("verbose", [False, True])
def test_an_unmappable_record_is_dropped_counted_and_logged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    verbose: bool,
) -> None:
    _break_bash_records(monkeypatch)
    control = LocalListenerControl(
        tmp_path / ".cuanta",
        tmp_path,
        lambda: SqliteLedger(tmp_path / "l.db"),
        linger_s=0.0,
        verbose=verbose,
    )
    with control.scoped(next_free_port(47300)) as status:
        base = f"http://127.0.0.1:{status.port}"
        response = httpx.post(f"{base}/v1/logs", json=_redaction(), timeout=5)
        health = httpx.get(f"{base}/health", timeout=5).json()
    assert response.status_code == 200
    assert health["unreadable"] == 1
    ledger = SqliteLedger(tmp_path / "l.db")
    events = ledger.events(EventQuery(run_id="01REALRUN"))
    ledger.close()
    assert sorted(event.kind for event in events) == ["api_request", "telemetry_unreadable"]
    marker = next(event for event in events if event.kind == "telemetry_unreadable")
    assert marker.source == "cuanta"
    assert json.loads(marker.raw) == {"event": "claude_code.tool_result", "error": "ValueError"}
    logged = (tmp_path / ".cuanta" / "logs" / "listener.log").read_text(encoding="utf-8")
    assert "unreadable claude_code.tool_result" in logged
    assert "Traceback" in logged
    assert "mapper bug" in logged
    err = capfd.readouterr().err
    assert ("mapper bug" in err) is verbose
    assert "Exception occurred" not in err


def test_a_record_whose_count_overflows_is_counted_and_its_batch_still_lands(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "logs" / "listener.log"
    running = build_listener(
        next_free_port(47700),
        lambda: SqliteLedger(tmp_path / "l.db"),
        False,
        "tok",
        log=LogFile(log),
    )
    payload = _redaction()
    records = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    records[1]["attributes"].append({"key": "duration_ms", "value": {"intValue": 1e999}})
    running.start()
    try:
        response = httpx.post(
            _url(running, "/v1/logs"),
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        _wait_written(running, 2)
        health = httpx.get(_url(running, "/health"), timeout=5).json()
    finally:
        running.stop()
    assert response.status_code == 200
    assert health["unreadable"] == 1
    ledger = SqliteLedger(tmp_path / "l.db")
    kinds = sorted(event.kind for event in ledger.events(EventQuery(run_id="01REALRUN")))
    ledger.close()
    assert kinds == ["api_request", UNREADABLE_KIND]
    logged = log.read_text(encoding="utf-8")
    assert "OverflowError" in logged and "unreadable payload" not in logged
    assert "Traceback" not in capfd.readouterr().err


def test_container_verbose_reaches_the_listener_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    _break_bash_records(monkeypatch)
    container = Container.for_project(tmp_path, verbose=True)
    try:
        with container.listener(linger_s=0.0).scoped(next_free_port(47400)) as status:
            response = httpx.post(
                f"http://127.0.0.1:{status.port}/v1/logs", json=_redaction(), timeout=5
            )
    finally:
        container.close()
    assert response.status_code == 200
    assert "mapper bug" in capfd.readouterr().err
    logged = (tmp_path / ".cuanta" / "logs" / "listener.log").read_text(encoding="utf-8")
    assert "mapper bug" in logged


def test_a_payload_that_is_not_otlp_gets_400_and_a_log_entry(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "logs" / "listener.log"
    running = build_listener(
        next_free_port(47600),
        lambda: SqliteLedger(tmp_path / "l.db"),
        False,
        "tok",
        log=LogFile(log),
    )
    running.start()
    try:
        response = httpx.post(
            _url(running, "/v1/logs"), json={"resourceLogs": [{"resource": "x"}]}, timeout=5
        )
        health = httpx.get(_url(running, "/health"), timeout=5)
    finally:
        running.stop()
    assert response.status_code == 400
    assert health.status_code == 200
    logged = log.read_text(encoding="utf-8")
    assert "unreadable payload /v1/logs" in logged
    assert "Traceback" in logged
    assert "Traceback" not in capfd.readouterr().err


class LockedOnce(MemoryLedger):
    def __init__(self) -> None:
        super().__init__()
        self.locked = True

    def add_events(self, events: Sequence[LedgerEvent]) -> int:
        if self.locked:
            self.locked = False
            raise sqlite3.OperationalError("database is locked")
        return super().add_events(events)


def test_a_failing_ledger_write_drops_only_that_batch(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    ledger = LockedOnce()
    log = tmp_path / "logs" / "listener.log"
    writer = EventWriter(lambda: ledger, LogFile(log))
    writer.start()
    try:
        writer.submit([LedgerEvent(run_id="R", kind="first")])
        assert writer.drain(5)
        writer.submit([LedgerEvent(run_id="R", kind="second")])
        assert writer.drain(5)
    finally:
        writer.stop()
    assert [event.kind for event in ledger.events(EventQuery(run_id="R"))] == ["second"]
    assert writer.stats.written == 1
    assert writer.stats.dropped == 1
    logged = log.read_text(encoding="utf-8")
    assert "ledger write failed; events dropped: 1" in logged
    assert "database is locked" in logged
    assert "Traceback" not in capfd.readouterr().err


def _locked_ledger() -> Ledger:
    raise sqlite3.OperationalError("database is locked")


def test_a_ledger_that_cannot_open_drops_and_counts_without_a_traceback(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "logs" / "listener.log"
    writer = EventWriter(_locked_ledger, LogFile(log))
    writer.start()
    try:
        writer.submit([LedgerEvent(run_id="R", kind="first"), LedgerEvent(run_id="R", kind="next")])
        assert writer.drain(5)
        writer.submit([LedgerEvent(run_id="R", kind="last")])
        assert writer.drain(5)
    finally:
        writer.stop()
    assert writer.stats.dropped == 3
    assert writer.stats.written == 0
    logged = log.read_text(encoding="utf-8")
    assert logged.count("ledger unavailable") == 1
    assert "database is locked" in logged
    assert "Traceback" not in capfd.readouterr().err


def test_health_reports_what_a_listener_without_a_ledger_dropped(tmp_path: Path) -> None:
    running = build_listener(
        next_free_port(47800),
        _locked_ledger,
        False,
        "tok",
        log=LogFile(tmp_path / "logs" / "listener.log"),
    )
    running.start()
    try:
        response = httpx.post(_url(running, "/v1/logs"), json=_redaction(), timeout=5)
        deadline = time.monotonic() + 5
        health = httpx.get(_url(running, "/health"), timeout=5).json()
        while health["dropped"] < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
            health = httpx.get(_url(running, "/health"), timeout=5).json()
        started = time.monotonic()
    finally:
        running.stop()
    assert time.monotonic() - started < 4
    assert response.status_code == 200
    assert health["dropped"] == 2
    assert health["written"] == 0


class ClosingFails(MemoryLedger):
    def close(self) -> None:
        raise sqlite3.OperationalError("disk I/O error")


def test_a_failing_ledger_close_is_logged_not_raised(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    ledger = ClosingFails()
    log = tmp_path / "logs" / "listener.log"
    writer = EventWriter(lambda: ledger, LogFile(log))
    writer.start()
    try:
        writer.submit([LedgerEvent(run_id="R", kind="kept")])
        assert writer.drain(5)
    finally:
        writer.stop()
    assert [event.kind for event in ledger.events(EventQuery(run_id="R"))] == ["kept"]
    logged = log.read_text(encoding="utf-8")
    assert "ledger close failed" in logged
    assert "disk I/O error" in logged
    assert "Traceback" not in capfd.readouterr().err


def _requests(*counts: str) -> dict[str, Any]:
    records = [
        {
            "timeUnixNano": "1767225600000000000",
            "body": {"stringValue": "claude_code.api_request"},
            "attributes": [
                {"key": "event.name", "value": {"stringValue": "api_request"}},
                {"key": "input_tokens", "value": {"intValue": count}},
                {"key": "cuanta.run_id", "value": {"stringValue": "01ABSURD"}},
            ],
        }
        for count in counts
    ]
    return {
        "resourceLogs": [{"resource": {"attributes": []}, "scopeLogs": [{"logRecords": records}]}]
    }


def test_a_count_beyond_the_ledger_range_keeps_its_batch(tmp_path: Path) -> None:
    ledger = SqliteLedger(tmp_path / "l.db")
    writer = EventWriter(lambda: ledger, LogFile(tmp_path / "logs" / "listener.log"))
    writer.submit(map_payload("/v1/logs", _requests("8", "99999999999999999999", "12")))
    writer.start()
    try:
        assert writer.drain(5)
    finally:
        writer.stop()
    reader = SqliteLedger(tmp_path / "l.db")
    stored = reader.events(EventQuery(run_id="01ABSURD"))
    reader.close()
    assert sorted(event.input_tokens for event in stored) == [0, 8, 12]
    assert writer.stats.written == 3
    assert writer.stats.dropped == 0


def test_an_event_the_ledger_cannot_store_becomes_a_counted_marker(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "logs" / "listener.log"
    ledger = SqliteLedger(tmp_path / "l.db")
    writer = EventWriter(lambda: ledger, LogFile(log))
    writer.submit(
        [
            LedgerEvent(run_id="R", kind="api_request", input_tokens=8),
            LedgerEvent(run_id="R", kind="api_request", model="claude-\ud83d", input_tokens=9),
            LedgerEvent(run_id="R", kind="tool_result"),
        ]
    )
    writer.start()
    try:
        assert writer.drain(5)
    finally:
        writer.stop()
    reader = SqliteLedger(tmp_path / "l.db")
    stored = reader.events(EventQuery(run_id="R"))
    reader.close()
    assert sorted(event.kind for event in stored) == ["api_request", UNREADABLE_KIND, "tool_result"]
    marker = next(event for event in stored if event.kind == UNREADABLE_KIND)
    assert json.loads(marker.raw) == {"event": "api_request", "error": "UnicodeEncodeError"}
    assert (writer.stats.written, writer.stats.unreadable, writer.stats.dropped) == (3, 1, 0)
    logged = log.read_text(encoding="utf-8")
    assert "unstorable api_request" in logged
    assert "UnicodeEncodeError" in logged
    assert "Traceback" not in capfd.readouterr().err


def test_the_background_child_writes_to_its_own_out_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[tuple[Path, Path]] = []

    def spawn(command: list[str], **options: Any) -> None:
        opened.append((Path(options["stdout"].name), Path(options["stderr"].name)))

    monkeypatch.setattr("cuanta.adapters.telemetry.listener_control.subprocess.Popen", spawn)
    control = LocalListenerControl(
        tmp_path / ".cuanta", tmp_path, lambda: SqliteLedger(tmp_path / "l.db")
    )
    logs = tmp_path / ".cuanta" / "logs"
    out = logs / "listener.out.log"
    logs.mkdir(parents=True)
    out.write_text("output of an earlier start\n", encoding="utf-8")
    monkeypatch.setattr(control, "_probe", lambda port: ListenerStatus(True, port, 4242))
    assert control.start_background(next_free_port(47500)).running
    assert opened == [(out, out)]
    assert out.read_text(encoding="utf-8") == ""
    monkeypatch.setattr(listener_control, "START_TIMEOUT_S", 0.0)
    monkeypatch.setattr(control, "_probe", lambda port: ListenerStatus(False, port))
    with pytest.raises(EnvironmentFailure) as raised:
        control.start_background(next_free_port(47500))
    assert raised.value.hint == f"see {out}"
    assert not (logs / "listener.log").exists()
    assert not (tmp_path / ".cuanta" / "listener.log").exists()


HOLDER = "import time; print('child ready', flush=True); time.sleep(60)"


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def test_the_listener_log_rotates_while_the_background_child_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = subprocess.Popen
    children: list[subprocess.Popen[Any]] = []

    def spawn(command: list[str], **options: Any) -> subprocess.Popen[Any]:
        child = real([sys.executable, "-c", HOLDER], **options)
        children.append(child)
        return child

    monkeypatch.setattr("cuanta.adapters.telemetry.listener_control.subprocess.Popen", spawn)
    control = LocalListenerControl(
        tmp_path / ".cuanta", tmp_path, lambda: SqliteLedger(tmp_path / "l.db")
    )
    monkeypatch.setattr(control, "_probe", lambda port: ListenerStatus(True, port, 4242))
    log = control.log_path
    rotated = log.with_name("listener.log.1")
    out = log.with_name("listener.out.log")
    try:
        control.start_background(next_free_port(47700))
        deadline = time.monotonic() + 15
        while "child ready" not in _text(out) + _text(log) and time.monotonic() < deadline:
            time.sleep(0.05)
        writer = LogFile(log, limit_bytes=256)
        for index in range(8):
            writer.write(f"entry {index}", "x" * 120)
        assert rotated.exists()
        assert log.stat().st_size < 512
    finally:
        for child in children:
            child.kill()
            child.wait(timeout=10)
    assert "child ready" in _text(out)
    assert "child ready" not in _text(log) + _text(rotated)


@pytest.mark.parametrize("verbose", [False, True])
def test_a_run_served_by_the_background_listener_names_its_log_when_verbose(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    verbose: bool,
) -> None:
    control = LocalListenerControl(
        tmp_path / ".cuanta",
        tmp_path,
        lambda: SqliteLedger(tmp_path / "l.db"),
        linger_s=0.0,
        verbose=verbose,
    )
    monkeypatch.setattr(control, "status", lambda: ListenerStatus(True, 4318, 4242))
    with control.scoped(4318) as status:
        assert status.running
        assert not status.owned
    err = capfd.readouterr().err
    assert ("background listener on 127.0.0.1:4318" in err) is verbose
    assert (str(control.log_path) in err) is verbose
    assert not control.log_path.exists()
