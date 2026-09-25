from __future__ import annotations

import shlex
import struct
import subprocess
import sys
import zlib
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cuanta.domain.errors import NotAvailable
from cuanta.domain.michi import COLORS, EYE, pixel_map
from cuanta.domain.voice import Mood

if TYPE_CHECKING:
    from aiohttp import web

WEB_HINT = 'uv tool install --editable ".[web]"  or  pip install "cuanta[web]"'
ICON_SIZE = 32
ICON_SCALE = 2
EYE_COLOR = "#2B2735"


def shell_command(parts: Sequence[str], platform: str = "") -> str:
    if (platform or sys.platform) == "win32":
        return subprocess.list2cmdline(list(parts))
    return shlex.join(parts)


def app_command(project: Path, language: str, theme_choice: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "cuanta",
        "--project",
        str(project),
        "ui",
        "--lang",
        language,
        "--ui-theme",
        theme_choice,
    ]


def _rgba(code: str) -> bytes:
    color = EYE_COLOR if code == EYE else COLORS.get(code)
    if color is None:
        return b"\x00\x00\x00\x00"
    return bytes.fromhex(color.removeprefix("#")) + b"\xff"


def icon_pixels() -> list[bytes]:
    rows = pixel_map(Mood.HAPPY)
    width = len(rows[0]) * ICON_SCALE
    height = len(rows) * ICON_SCALE
    left = (ICON_SIZE - width) // 2
    top = (ICON_SIZE - height) // 2
    blank = b"\x00\x00\x00\x00"
    lines: list[bytes] = []
    for y in range(ICON_SIZE):
        line = bytearray()
        for x in range(ICON_SIZE):
            row, column = (y - top) // ICON_SCALE, (x - left) // ICON_SCALE
            inside = 0 <= y - top < height and 0 <= x - left < width
            line += _rgba(rows[row][column]) if inside else blank
        lines.append(bytes(line))
    return lines


def _chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))


def favicon_png() -> bytes:
    raw = b"".join(b"\x00" + line for line in icon_pixels())
    header = struct.pack(">IIBBBBB", ICON_SIZE, ICON_SIZE, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def favicon_ico() -> bytes:
    png = favicon_png()
    directory = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", ICON_SIZE, ICON_SIZE, 0, 0, 1, 32, len(png), 22)
    return directory + entry + png


def server(command: str, host: str, port: int) -> Any:
    try:
        from aiohttp import web
        from textual_serve.server import Server
    except ImportError as error:
        raise NotAvailable("cuanta ui --web needs the web extra", WEB_HINT) from error

    icon = favicon_ico()

    async def handle_favicon(_: web.Request) -> web.Response:
        return web.Response(body=icon, content_type="image/x-icon")

    class CuantaServer(Server):
        async def _make_app(self) -> web.Application:
            app = await super()._make_app()
            app.router.add_get("/favicon.ico", handle_favicon)
            return app

    return CuantaServer(command, host=host, port=port, title="cuanta")


def serve(project: Path, language: str, theme_choice: str, host: str, port: int) -> None:
    command = shell_command(app_command(project, language, theme_choice))
    server(command, host, port).serve()
