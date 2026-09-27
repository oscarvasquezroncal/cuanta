from __future__ import annotations

import io
import json
from collections.abc import Mapping
from typing import cast

import pytest

from cuanta.application.mcp import MAX_MESSAGE_BYTES, SUPPORTED_VERSIONS, McpServer


class Tools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, object]]] = []

    def call(self, name: str, arguments: Mapping[str, object]) -> object:
        self.calls.append((name, arguments))
        if arguments.get("invalid"):
            raise ValueError("Use inclusive a:b lines")
        if arguments.get("crash"):
            raise OSError("private engine detail")
        if arguments.get("unserializable"):
            return object()
        return {"text": "España\nline 2", "path": arguments.get("path", "cart.py")}


def _server(tools: Tools | None = None) -> McpServer:
    return McpServer(
        (tools or Tools()).call,
        ({"name": "page", "inputSchema": {"type": "object"}},),
        "0.3.0",
    )


def _request(
    server: McpServer, method: str, params: object = None, identity: object = 1
) -> dict[str, object]:
    payload: dict[str, object] = {"jsonrpc": "2.0", "id": identity, "method": method}
    if params is not None:
        payload["params"] = params
    response = server.handle_line(json.dumps(payload))
    assert response is not None
    return cast(dict[str, object], json.loads(response))


def _initialize(server: McpServer, version: str = "2025-11-25") -> dict[str, object]:
    return _request(
        server,
        "initialize",
        {
            "protocolVersion": version,
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        },
    )


def _ready(server: McpServer, version: str = "2025-11-25") -> None:
    _initialize(server, version)
    assert server.handle_line('{"jsonrpc":"2.0","method":"notifications/initialized"}') is None


@pytest.mark.parametrize("version", SUPPORTED_VERSIONS)
def test_lifecycle_echoes_supported_version_and_waits_for_initialized(version: str) -> None:
    server = _server()
    response = _initialize(server, version)
    result = cast(dict[str, object], response["result"])
    assert result["protocolVersion"] == version
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert "Server is not initialized" in str(_request(server, "tools/list"))
    server.handle_line('{"jsonrpc":"2.0","method":"notifications/initialized"}')
    assert "tools" in str(_request(server, "tools/list"))
    assert "Already initialized" in str(_initialize(server, version))


def test_unsupported_version_negotiates_latest_supported_version() -> None:
    result = cast(dict[str, object], _initialize(_server(), "future-version")["result"])
    assert result["protocolVersion"] == SUPPORTED_VERSIONS[0]


def test_invalid_initialize_keeps_server_available_for_valid_retry() -> None:
    server = _server()
    response = _request(server, "initialize", {"protocolVersion": "2025-11-25"})
    assert cast(dict[str, object], response["error"])["code"] == -32602
    assert "result" in _initialize(server)


def test_ping_is_available_during_initialization_and_notifications_never_reply() -> None:
    server = _server()
    assert _request(server, "ping", identity="heartbeat")["result"] == {}
    for method in ("ping", "initialize", "tools/call", "notifications/cancelled", "unknown"):
        assert server.handle_line(json.dumps({"jsonrpc": "2.0", "method": method})) is None
    assert "Server is not initialized" in str(_request(server, "tools/list"))


@pytest.mark.parametrize("line", ["{", "NaN", '{"x":Infinity}', "[" * 2000])
def test_parse_failures_have_jsonrpc_errors_and_no_traceback(line: str) -> None:
    reply = _server().handle_line(line)
    assert reply is not None
    payload = json.loads(reply)
    assert payload["id"] is None and payload["error"]["code"] == -32700


@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        {"jsonrpc": "1.0", "method": "ping", "id": 1},
        {"jsonrpc": "2.0", "method": "", "id": 1},
        {"jsonrpc": "2.0", "method": "ping", "id": None},
        {"jsonrpc": "2.0", "method": "ping", "id": True},
        {"jsonrpc": "2.0", "method": "ping", "id": 1.5},
    ],
)
def test_malformed_envelopes_are_rejected(payload: object) -> None:
    reply = _server().handle_line(json.dumps(payload))
    assert reply is not None
    assert json.loads(reply)["error"]["code"] == -32600


def test_known_notification_with_bad_parameters_does_not_reply_or_initialize() -> None:
    server = _server()
    _initialize(server)
    assert (
        server.handle_line('{"jsonrpc":"2.0","method":"notifications/initialized","params":[]}')
        is None
    )
    assert "Server is not initialized" in str(_request(server, "tools/list"))


@pytest.mark.parametrize("version", SUPPORTED_VERSIONS)
def test_tool_results_match_negotiated_version_and_preserve_text(version: str) -> None:
    tools = Tools()
    server = _server(tools)
    _ready(server, version)
    response = _request(server, "tools/call", {"name": "page", "arguments": {"path": "cart.py"}})
    result = cast(dict[str, object], response["result"])
    assert result["isError"] is False
    content = cast(list[dict[str, object]], result["content"])
    assert json.loads(str(content[0]["text"]))["text"] == "España\nline 2"
    assert ("structuredContent" in result) is (version != "2025-03-26")
    assert tools.calls == [("page", {"path": "cart.py"})]


def test_protocol_and_execution_errors_are_distinct_and_recoverable() -> None:
    tools = Tools()
    server = _server(tools)
    _ready(server)
    assert "Method not found" in str(_request(server, "resources/list"))
    assert "Unknown tools cursor" in str(_request(server, "tools/list", {"cursor": "missing"}))
    for params in ({"name": "absent"}, {"name": "page", "arguments": []}, {}):
        response = _request(server, "tools/call", params)
        assert cast(dict[str, object], response["error"])["code"] == -32602
    assert "Parameters must be an object" in str(_request(server, "tools/call", []))
    response = _request(server, "tools/call", {"name": "page", "arguments": {"invalid": True}})
    assert cast(dict[str, object], response["result"])["isError"] is True
    for argument in ("crash", "unserializable"):
        response = _request(server, "tools/call", {"name": "page", "arguments": {argument: True}})
        assert cast(dict[str, object], response["result"])["isError"] is True
        assert "private engine detail" not in str(response)
    assert _request(server, "ping")["result"] == {}


def test_observers_receive_actual_result_and_failures_cannot_break_protocol() -> None:
    observations: list[tuple[str, Mapping[str, object], object, float]] = []
    initializations: list[tuple[str, Mapping[str, object]]] = []

    def observe(name: str, args: Mapping[str, object], result: object, latency: float) -> None:
        observations.append((name, args, result, latency))
        raise OSError("unavailable ledger")

    def initialized(version: str, client: Mapping[str, object]) -> None:
        initializations.append((version, client))
        raise OSError("unavailable ledger")

    server = McpServer(Tools().call, ({"name": "page"},), "0.3.0", observe, initialized)
    _ready(server)
    response = _request(server, "tools/call", {"name": "page", "arguments": {"path": "cart.py"}})
    assert observations[0][:3] == ("page", {"path": "cart.py"}, response["result"])
    assert observations[0][3] >= 0
    assert initializations == [("2025-11-25", {"name": "test-client", "version": "1.0"})]


@pytest.mark.parametrize("failure", ["backend", "encoding", "oversize", "nonfinite"])
def test_every_executed_failure_observes_only_the_small_returned_error(failure: str) -> None:
    observations: list[tuple[str, Mapping[str, object], object, float]] = []

    def call(name: str, arguments: Mapping[str, object]) -> object:
        if failure == "backend":
            raise OSError("private engine detail")
        if failure == "encoding":
            return object()
        if failure == "nonfinite":
            return {"private_source": float("nan")}
        return {"private_source": "x" * MAX_MESSAGE_BYTES}

    def observe(name: str, args: Mapping[str, object], result: object, latency: float) -> None:
        observations.append((name, args, result, latency))

    server = McpServer(call, ({"name": "page"},), "0.3.0", observe)
    _ready(server)
    response = _request(server, "tools/call", {"name": "page", "arguments": {"path": "cart.py"}})
    result = cast(dict[str, object], response["result"])
    assert result["isError"] is True
    assert len(observations) == 1 and observations[0][:3] == ("page", {"path": "cart.py"}, result)
    assert observations[0][3] >= 0
    assert len(json.dumps(result).encode()) < 200
    assert "private" not in str(result) and "structuredContent" not in result
    assert _request(server, "ping")["result"] == {}


def test_oversized_response_identity_observes_the_final_bounded_protocol_error() -> None:
    observations: list[object] = []

    def observe(name: str, args: Mapping[str, object], result: object, latency: float) -> None:
        observations.append(result)

    server = McpServer(Tools().call, ({"name": "page"},), "0.3.0", observe)
    _ready(server)
    response = _request(
        server, "tools/call", {"name": "page"}, identity="x" * (MAX_MESSAGE_BYTES - 100)
    )
    assert response["id"] is None
    assert cast(dict[str, object], response["error"])["code"] == -32603
    assert observations == [{"error": response["error"]}]
    assert len(json.dumps(response)) < 200


def test_oversized_input_is_drained_then_next_request_is_processed_and_eof_closes() -> None:
    closed: list[bool] = []
    server = McpServer(
        Tools().call, ({"name": "page"},), "0.3.0", close=lambda: closed.append(True)
    )
    reader = io.StringIO(
        "x" * (MAX_MESSAGE_BYTES + 20) + '\n{"jsonrpc":"2.0","id":2,"method":"ping"}\n'
    )
    writer = io.StringIO()
    server.serve(reader, writer)
    replies = [json.loads(line) for line in writer.getvalue().splitlines()]
    assert len(replies) == 2
    assert replies[0]["error"]["code"] == -32600
    assert replies[1] == {"jsonrpc": "2.0", "id": 2, "result": {}}
    server.close()
    assert closed == [True]


def test_byte_limit_applies_to_unicode_and_oversized_backend_output() -> None:
    reply = _server().handle_line('"' + "é" * (MAX_MESSAGE_BYTES // 2) + '"')
    assert reply is not None and json.loads(reply)["error"]["code"] == -32600
    server = McpServer(lambda _name, _args: "x" * MAX_MESSAGE_BYTES, ({"name": "page"},), "0.3.0")
    _ready(server)
    assert "Result exceeds the byte limit" in str(_request(server, "tools/call", {"name": "page"}))


def test_broken_output_still_releases_index_reader() -> None:
    class BrokenOutput(io.StringIO):
        def write(self, text: str) -> int:
            raise BrokenPipeError

    closed: list[bool] = []
    server = McpServer(
        Tools().call, ({"name": "page"},), "0.3.0", close=lambda: closed.append(True)
    )
    with pytest.raises(BrokenPipeError):
        server.serve(io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n'), BrokenOutput())
    assert closed == [True]
