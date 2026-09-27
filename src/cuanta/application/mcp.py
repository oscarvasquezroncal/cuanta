from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from time import perf_counter
from typing import TextIO, cast

SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
MAX_MESSAGE_BYTES = 1024 * 1024
ToolCall = Callable[[str, Mapping[str, object]], object]
ToolObserver = Callable[[str, Mapping[str, object], object, float], None]
InitializeObserver = Callable[[str, Mapping[str, object]], None]


def _object(value: object) -> dict[str, object] | None:
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return cast(dict[str, object], value)
    return None


def _nonfinite(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def _encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _error(identity: object, code: int, message: str) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": identity, "error": {"code": code, "message": message}}


def _result(identity: object, result: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": identity, "result": result}


def _execution_error(message: str) -> dict[str, object]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


class McpServer:
    def __init__(
        self,
        call: ToolCall,
        tools: Sequence[Mapping[str, object]],
        version: str,
        observe: ToolObserver | None = None,
        initialized: InitializeObserver | None = None,
        close: Callable[[], None] | None = None,
    ) -> None:
        self._call = call
        self._tools = tuple(dict(tool) for tool in tools)
        self._names = frozenset(str(tool["name"]) for tool in tools)
        self._version = version
        self._observe = observe
        self._initialized = initialized
        self._close = close
        self._negotiated = ""
        self._ready = False

    def handle_line(self, line: str) -> str | None:
        if len(line.encode("utf-8", errors="replace")) > MAX_MESSAGE_BYTES:
            return _encode(_error(None, -32600, "Message exceeds the byte limit"))
        try:
            value: object = json.loads(line, parse_constant=_nonfinite)
        except (ValueError, RecursionError):
            return _encode(_error(None, -32700, "Parse error"))
        message = _object(value)
        if message is None:
            return _encode(_error(None, -32600, "Invalid request"))
        method = message.get("method")
        identity = message.get("id")
        if message.get("jsonrpc") != "2.0" or not isinstance(method, str) or not method:
            return _encode(_error(None, -32600, "Invalid request"))
        notification = "id" not in message
        if not notification and (
            isinstance(identity, bool) or not isinstance(identity, (int, str))
        ):
            return _encode(_error(None, -32600, "Invalid request id"))
        params = _object(message.get("params", {}))
        if notification:
            if method == "notifications/initialized" and params is not None and self._negotiated:
                self._ready = True
            return None
        if params is None:
            return _encode(_error(identity, -32602, "Parameters must be an object"))
        try:
            response = self._request(identity, method, params)
            encoded = _encode(response)
            if len(encoded) > MAX_MESSAGE_BYTES:
                return _encode(_error(identity, -32603, "Result exceeds the byte limit"))
            return encoded
        except Exception:
            return _encode(_error(identity, -32603, "Internal error"))

    def _request(
        self, identity: object, method: str, params: dict[str, object]
    ) -> dict[str, object]:
        if method == "ping":
            return _result(identity, {})
        if method == "initialize":
            return self._initialize(identity, params)
        if not self._ready:
            return _error(identity, -32002, "Server is not initialized")
        if method == "tools/list":
            if params.get("cursor") is not None:
                return _error(identity, -32602, "Unknown tools cursor")
            return _result(identity, {"tools": self._tools})
        if method == "tools/call":
            return self._tool_call(identity, params)
        return _error(identity, -32601, "Method not found")

    def _initialize(self, identity: object, params: dict[str, object]) -> dict[str, object]:
        if self._negotiated:
            return _error(identity, -32600, "Already initialized")
        requested = params.get("protocolVersion")
        client = _object(params.get("clientInfo"))
        capabilities = _object(params.get("capabilities"))
        if (
            not isinstance(requested, str)
            or not requested
            or client is None
            or capabilities is None
            or not isinstance(client.get("name"), str)
            or not isinstance(client.get("version"), str)
        ):
            return _error(identity, -32602, "Invalid initialization parameters")
        self._negotiated = requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        if self._initialized is not None:
            with suppress(Exception):
                self._initialized(self._negotiated, client)
        return _result(
            identity,
            {
                "protocolVersion": self._negotiated,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "cuanta", "version": self._version},
                "instructions": (
                    "Use find, card, impact, facts, page and tests_for before broad file reads. "
                    "After understanding a file, save an anchored note. "
                    "Notes update the local index only; these tools never edit source files."
                ),
            },
        )

    def _tool_call(self, identity: object, params: dict[str, object]) -> dict[str, object]:
        name = params.get("name")
        arguments = _object(params.get("arguments", {}))
        if not isinstance(name, str) or arguments is None:
            return _error(identity, -32602, "Tool name and object arguments are required")
        if name not in self._names:
            return _error(identity, -32602, "Unknown tool")
        started = perf_counter()
        result = self._execute_tool(name, arguments)
        response = _result(identity, result)
        if len(_encode(response)) > MAX_MESSAGE_BYTES:
            result = _execution_error("Result exceeds the byte limit")
            response = _result(identity, result)
        if len(_encode(response)) > MAX_MESSAGE_BYTES:
            response = _error(None, -32603, "Result exceeds the byte limit")
            result = {"error": response["error"]}
        if self._observe is not None:
            with suppress(Exception):
                self._observe(name, arguments, result, perf_counter() - started)
        return response

    def _execute_tool(self, name: str, arguments: Mapping[str, object]) -> dict[str, object]:
        try:
            value = self._call(name, arguments)
        except ValueError as error:
            return _execution_error(str(error))
        except Exception:
            return _execution_error("Index tool failed")
        try:
            result: dict[str, object] = {
                "content": [{"type": "text", "text": _encode(value)}],
                "isError": False,
            }
            structured = _object(value)
            if structured is not None and self._negotiated != "2025-03-26":
                result["structuredContent"] = structured
            _encode(result)
        except Exception:
            return _execution_error("Index tool returned invalid data")
        return result

    def serve(self, reader: TextIO, writer: TextIO) -> None:
        try:
            self._serve(reader, writer)
        finally:
            self.close()

    def close(self) -> None:
        callback, self._close = self._close, None
        if callback is not None:
            callback()

    def _serve(self, reader: TextIO, writer: TextIO) -> None:
        while True:
            line = reader.readline(MAX_MESSAGE_BYTES + 1)
            if not line:
                return
            response: str | None
            if len(line) > MAX_MESSAGE_BYTES and not line.endswith("\n"):
                while line and not line.endswith("\n"):
                    line = reader.readline(MAX_MESSAGE_BYTES + 1)
                response = _encode(_error(None, -32600, "Message exceeds the byte limit"))
            else:
                response = self.handle_line(line)
            if response is not None:
                writer.write(response + "\n")
                writer.flush()
