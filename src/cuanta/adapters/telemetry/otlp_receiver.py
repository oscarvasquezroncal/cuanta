from __future__ import annotations

import ipaddress
import json
import os
import queue
import secrets
import socket
import sys
import threading
import time
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from cuanta.adapters.system.log_file import LogFile
from cuanta.adapters.telemetry import claude_code_mapper, codex_mapper
from cuanta.adapters.telemetry.mapping import as_int, as_text, first, raw_json
from cuanta.adapters.telemetry.otlp_json import (
    LogRecord,
    SpanRecord,
    Unreadable,
    iter_logs,
    iter_metrics,
    iter_spans,
)
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.telemetry import LOCALHOST, unreadable_event
from cuanta.ports.ledger import Ledger

PATHS = ("/v1/logs", "/v1/metrics", "/v1/traces")
MAX_BODY = 32 * 1024 * 1024
MAX_PORT = 65535
BATCH_SIZE = 500
FLUSH_INTERVAL_S = 0.25
VALUE_ERRORS = (ArithmeticError, TypeError, ValueError)
UNSUPPORTED_HINT = (
    "cuanta listens for OTLP/JSON only: set OTEL_EXPORTER_OTLP_PROTOCOL=http/json "
    '(Codex: protocol = "json")'
)


Failed = Callable[[str, Exception], None]


def _log_event(record: LogRecord, keep_prompts: bool) -> LedgerEvent:
    if codex_mapper.handles(record):
        return codex_mapper.map_log(record, keep_prompts)
    if claude_code_mapper.handles(record):
        return claude_code_mapper.map_log(record, keep_prompts)
    return LedgerEvent(
        source="otlp",
        kind=record.name or "log",
        trace_id=record.trace_id,
        ts=record.ts,
        raw=raw_json(record.raw, keep_prompts),
    )


def _span_event(span: SpanRecord) -> LedgerEvent:
    return LedgerEvent(
        source="otlp",
        kind=f"span:{span.name}",
        trace_id=span.trace_id,
        duration_ms=as_int(span.duration_ms),
        ts=span.ts,
        raw=raw_json({"name": span.name, "attributes": span.attributes}, False),
    )


@dataclass
class _Batch:
    failed: Failed | None
    events: list[LedgerEvent] = field(default_factory=list)

    def lost(
        self,
        error: Exception,
        name: str,
        attrs: dict[str, Any],
        resource: dict[str, Any],
        trace_id: str,
        ts: str,
    ) -> None:
        run_id = as_text(first(attrs, "cuanta.run_id") or first(resource, "cuanta.run_id"))
        self.events.append(unreadable_event(run_id, trace_id, ts, name, type(error).__name__))
        if self.failed is not None:
            self.failed(name, error)

    def unreadable(self, item: Unreadable) -> None:
        self.lost(item.error, item.name, item.attributes, item.resource, item.trace_id, item.ts)


def map_payload(
    path: str, payload: Any, keep_prompts: bool = False, failed: Failed | None = None
) -> list[LedgerEvent]:
    batch = _Batch(failed)
    if path == "/v1/logs":
        for record in iter_logs(payload):
            if isinstance(record, Unreadable):
                batch.unreadable(record)
                continue
            try:
                batch.events.append(_log_event(record, keep_prompts))
            except Exception as error:
                batch.lost(
                    error,
                    record.name,
                    record.attributes,
                    record.resource,
                    record.trace_id,
                    record.ts,
                )
    elif path == "/v1/metrics":
        for point in iter_metrics(payload):
            if isinstance(point, Unreadable):
                batch.unreadable(point)
                continue
            try:
                batch.events.append(claude_code_mapper.map_metric(point))
            except Exception as error:
                batch.lost(error, point.name, point.attributes, point.resource, "", point.ts)
    elif path == "/v1/traces":
        for span in iter_spans(payload):
            if isinstance(span, Unreadable):
                batch.unreadable(span)
                continue
            try:
                batch.events.append(_span_event(span))
            except Exception as error:
                batch.lost(error, span.name, span.attributes, span.resource, span.trace_id, span.ts)
    return batch.events


def _trace(error: BaseException) -> str:
    return "".join(traceback.format_exception(error))


def _storable(text: str) -> str:
    return text.encode("utf-8", "replace").decode("utf-8")


@dataclass
class ListenerStats:
    received: int = 0
    written: int = 0
    rejected: int = 0
    unreadable: int = 0
    dropped: int = 0
    started: float = field(default_factory=time.monotonic)


class EventWriter:
    def __init__(self, ledger_factory: Callable[[], Ledger], log: LogFile | None = None) -> None:
        self._queue: queue.Queue[LedgerEvent | None] = queue.Queue()
        self._ledger_factory = ledger_factory
        self._log = log if log is not None else LogFile(None)
        self._thread = threading.Thread(target=self._loop, name="cuanta-writer", daemon=True)
        self._trace_runs: dict[str, str] = {}
        self.stats = ListenerStats()
        self._idle = threading.Event()
        self._idle.set()

    def start(self) -> None:
        self._thread.start()

    def submit(self, events: Sequence[LedgerEvent]) -> None:
        if events:
            self._idle.clear()
        for event in events:
            self._queue.put(event)
        self.stats.received += len(events)

    def _attribute(self, ledger: Ledger, event: LedgerEvent) -> LedgerEvent:
        if event.run_id or not event.trace_id:
            return event
        cached = self._trace_runs.get(event.trace_id)
        if cached is None:
            run = ledger.run_by_trace(event.trace_id)
            cached = run.id if run is not None else ""
            if cached:
                self._trace_runs[event.trace_id] = cached
        return replace(event, run_id=cached) if cached else event

    def _loop(self) -> None:
        ledger = self._open()
        if ledger is None:
            self._discard()
            return
        pending: list[LedgerEvent] = []
        running = True
        while running:
            try:
                item = self._queue.get(timeout=FLUSH_INTERVAL_S)
                if item is None:
                    running = False
                else:
                    pending.append(item)
                while len(pending) < BATCH_SIZE:
                    extra = self._queue.get_nowait()
                    if extra is None:
                        running = False
                        break
                    pending.append(extra)
            except queue.Empty:
                pass
            if pending:
                self._flush(ledger, pending)
                pending = []
            if self._queue.empty():
                self._idle.set()
        self._close(ledger)

    def _open(self) -> Ledger | None:
        try:
            return self._ledger_factory()
        except Exception as error:
            self._log.write("ledger unavailable; telemetry is dropped and counted", _trace(error))
            return None

    def _discard(self) -> None:
        while self._queue.get() is not None:
            self.stats.dropped += 1
            if self._queue.empty():
                self._idle.set()
        self._idle.set()

    def _close(self, ledger: Ledger) -> None:
        try:
            ledger.close()
        except Exception as error:
            self._log.write("ledger close failed", _trace(error))

    def _flush(self, ledger: Ledger, pending: list[LedgerEvent]) -> None:
        try:
            attributed = [self._attribute(ledger, event) for event in pending]
            self.stats.written += ledger.add_events(attributed)
        except VALUE_ERRORS:
            self._one_by_one(ledger, pending)
        except Exception as error:
            self._dropped(len(pending), error)

    def _one_by_one(self, ledger: Ledger, pending: list[LedgerEvent]) -> None:
        for index, event in enumerate(pending):
            try:
                self.stats.written += ledger.add_events([self._attribute(ledger, event)])
            except VALUE_ERRORS as error:
                self._unstorable(ledger, event, error)
            except Exception as error:
                self._dropped(len(pending) - index, error)
                return

    def _unstorable(self, ledger: Ledger, event: LedgerEvent, error: Exception) -> None:
        self._log.write(f"unstorable {event.kind}", _trace(error))
        marker = unreadable_event(
            _storable(event.run_id),
            _storable(event.trace_id),
            _storable(event.ts),
            _storable(event.kind),
            type(error).__name__,
        )
        try:
            self.stats.written += ledger.add_events([marker])
        except Exception as marker_error:
            self._dropped(1, marker_error)
            return
        self.stats.unreadable += 1

    def _dropped(self, count: int, error: Exception) -> None:
        self.stats.dropped += count
        self._log.write(f"ledger write failed; events dropped: {count}", _trace(error))

    def drain(self, timeout: float = 10.0) -> bool:
        return self._idle.wait(timeout)

    def stop(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=10)


def _loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


class OtlpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        port: int,
        writer: EventWriter,
        keep_prompts: bool,
        token: str,
        log: LogFile | None = None,
    ) -> None:
        self.writer = writer
        self.keep_prompts = keep_prompts
        self.token = token
        self.log = log if log is not None else LogFile(None)
        super().__init__((LOCALHOST, port), OtlpHandler)

    def handle_error(self, request: Any, client_address: Any) -> None:
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        peer = client_address[0] if isinstance(client_address, tuple) else client_address
        self.log.write(f"request from {peer}", traceback.format_exc())


class OtlpHandler(BaseHTTPRequestHandler):
    server: OtlpServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        return None

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def record_unreadable(self, name: str, error: Exception) -> None:
        self.server.writer.stats.unreadable += 1
        self.server.log.write(f"unreadable {name}", _trace(error))

    def _local(self) -> bool:
        if _loopback(self.client_address[0]):
            return True
        self.server.writer.stats.rejected += 1
        self._send(403, {"error": "cuanta accepts local peers only"})
        return False

    def do_GET(self) -> None:
        if not self._local():
            return
        if self.path == "/health":
            stats = self.server.writer.stats
            self._send(
                200,
                {
                    "ok": True,
                    "pid": os.getpid(),
                    "received": stats.received,
                    "written": stats.written,
                    "unreadable": stats.unreadable,
                    "dropped": stats.dropped,
                    "uptime_s": round(time.monotonic() - stats.started, 1),
                },
            )
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._local():
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._send(413, {"error": "payload too large"})
            return
        body = self.rfile.read(length) if length else b""
        if self.path == "/shutdown":
            token = self.headers.get("X-Cuanta-Token", "")
            if not secrets.compare_digest(token, self.server.token):
                self._send(403, {"error": "bad token"})
                return
            self._send(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if self.path not in PATHS:
            self._send(404, {"error": f"unknown path {self.path}"})
            return
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            self.server.writer.stats.rejected += 1
            self._send(
                415,
                {
                    "error": f"unsupported content type {content_type or 'none'}",
                    "hint": UNSUPPORTED_HINT,
                },
            )
            return
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            self._send(400, {"error": "invalid JSON"})
            return
        try:
            events = map_payload(
                self.path, payload, self.server.keep_prompts, self.record_unreadable
            )
        except Exception as error:
            self.server.log.write(f"unreadable payload {self.path}", _trace(error))
            self._send(400, {"error": "unreadable OTLP payload"})
            return
        self.server.writer.submit(events)
        self._send(200, {"partialSuccess": {}})


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((LOCALHOST, port))
        except OSError:
            return False
    return True


def next_free_port(start: int, attempts: int = 50) -> int:
    for port in range(start, start + attempts):
        if port_is_free(port):
            return port
    raise OSError(f"no free port in {start}..{start + attempts - 1}")


class RunningListener:
    def __init__(self, server: OtlpServer, writer: EventWriter) -> None:
        self.server = server
        self.writer = writer
        self._thread = threading.Thread(
            target=server.serve_forever, name="cuanta-otlp", daemon=True
        )

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def start(self) -> RunningListener:
        self.writer.start()
        self._thread.start()
        return self

    def serve_forever(self) -> None:
        self.writer.start()
        try:
            self.server.serve_forever()
        finally:
            self.server.server_close()
            self.writer.stop()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.writer.drain(5)
        self.writer.stop()


def build_listener(
    port: int,
    ledger_factory: Callable[[], Ledger],
    keep_prompts: bool,
    token: str,
    *,
    log: LogFile | None = None,
) -> RunningListener:
    writer = EventWriter(ledger_factory, log)
    server = OtlpServer(port, writer, keep_prompts, token, log)
    return RunningListener(server, writer)


def bind_listener(
    start: int,
    ledger_factory: Callable[[], Ledger],
    keep_prompts: bool,
    token: str,
    attempts: int = 50,
    *,
    log: LogFile | None = None,
) -> RunningListener:
    for port in range(start, min(start + attempts, MAX_PORT + 1)):
        try:
            return build_listener(port, ledger_factory, keep_prompts, token, log=log)
        except OSError:
            continue
    raise OSError(f"no free port in {start}..{start + attempts - 1}")
