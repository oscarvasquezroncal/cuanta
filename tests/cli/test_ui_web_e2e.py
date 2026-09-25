from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

aiohttp = pytest.importorskip("aiohttp")
pytest.importorskip("textual_serve")

READY_TIMEOUT_S = 30.0
FRAME_TIMEOUT_S = 60.0
TOTAL_TIMEOUT_S = 90.0


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


async def wait_until_serving(base: str) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_S
    async with aiohttp.ClientSession() as session:
        while time.monotonic() < deadline:
            try:
                async with session.get(base + "/") as response:
                    if response.status == 200:
                        return
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(0.2)
    raise AssertionError(f"cuanta ui --web never answered on {base}")


async def first_frame(base: str) -> tuple[bytes, bytes, str]:
    await wait_until_serving(base)
    async with aiohttp.ClientSession() as session:
        async with session.get(base + "/favicon.ico") as response:
            icon = await response.read()
            kind = response.content_type
        socket_url = base.replace("http", "ws", 1) + "/ws?width=100&height=30"
        async with session.ws_connect(socket_url) as channel:
            received = b""
            deadline = time.monotonic() + FRAME_TIMEOUT_S
            while b"cuanta" not in received and time.monotonic() < deadline:
                message = await channel.receive(timeout=deadline - time.monotonic())
                if message.type == aiohttp.WSMsgType.BINARY:
                    received += message.data
                elif message.type != aiohttp.WSMsgType.TEXT:
                    break
    return received, icon, kind


async def bounded(base: str) -> tuple[bytes, bytes, str]:
    return await asyncio.wait_for(first_frame(base), TOTAL_TIMEOUT_S)


def test_ui_web_serves_the_first_frame(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "shop"\n', encoding="utf-8")
    port = free_port()
    environ = {key: value for key, value in os.environ.items() if key != "CUANTA_FORCE_TTY"}
    environ["CUANTA_NO_ANIMATION"] = "1"
    command = [
        sys.executable,
        "-m",
        "cuanta",
        "--project",
        str(tmp_path),
        "ui",
        "--web",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    server = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=environ,
    )
    try:
        frame, icon, kind = asyncio.run(bounded(f"http://127.0.0.1:{port}"))
    finally:
        server.kill()
        output = server.communicate(timeout=30)[0].decode("utf-8", "replace")
    assert b"\x1b[" in frame, output
    assert b"cuanta" in frame, output
    assert "needs an interactive terminal" not in output
    assert kind == "image/x-icon"
    assert icon[:4] == b"\x00\x00\x01\x00"
    assert icon[22:30] == b"\x89PNG\r\n\x1a\n"
