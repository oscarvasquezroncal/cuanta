from __future__ import annotations

import base64
import difflib
import zlib
from collections.abc import Iterable
from dataclasses import dataclass

from cuanta.domain.gitindex import blob_id

BINARY_SNIFF_BYTES = 8000
TEXT_LIMIT_BYTES = 1_000_000
CONTEXT_LINES = 3
NO_NEWLINE = "\\ No newline at end of file"
FILE_MODE = "100644"
EXECUTABLE_MODE = "100755"
NULL_PATH = "/dev/null"
ZERO_ID = "0" * 40
BINARY_LINE_BYTES = 52
ESCAPES = {'"': '\\"', "\\": "\\\\", "\t": "\\t", "\n": "\\n", "\r": "\\r"}


@dataclass(frozen=True, slots=True)
class FilePatch:
    path: str
    text: str
    added: int
    removed: int
    binary: bool


def is_binary(data: bytes) -> bool:
    return b"\x00" in data[:BINARY_SNIFF_BYTES] or len(data) > TEXT_LIMIT_BYTES


def decode(data: bytes) -> str:
    return data.decode("utf-8", errors="surrogateescape")


def encode(text: str) -> bytes:
    return text.encode("utf-8", errors="surrogateescape")


def split_lines(text: str) -> list[str]:
    if not text:
        return []
    lines = text.split("\n")
    kept = [line + "\n" for line in lines[:-1]]
    return [*kept, lines[-1]] if lines[-1] else kept


def quote_path(path: str) -> str:
    raw = encode(path)
    if all(32 <= byte < 127 and chr(byte) not in ESCAPES for byte in raw):
        return path
    quoted: list[str] = []
    for byte in raw:
        char = chr(byte)
        if char in ESCAPES:
            quoted.append(ESCAPES[char])
        elif 32 <= byte < 127:
            quoted.append(char)
        else:
            quoted.append(f"\\{byte:03o}")
    return '"' + "".join(quoted) + '"'


def _file_line(marker: str, name: str) -> str:
    return f"{marker} {name}\t" if " " in name else f"{marker} {name}"


def _hunks(before: str, after: str) -> tuple[list[str], int, int]:
    produced = difflib.unified_diff(
        split_lines(before), split_lines(after), n=CONTEXT_LINES, lineterm=""
    )
    output: list[str] = []
    added = removed = 0
    for line in list(produced)[2:]:
        if line.startswith("@@"):
            output.append(line)
            continue
        added += line.startswith("+")
        removed += line.startswith("-")
        if line.endswith("\n"):
            output.append(line[:-1])
        else:
            output.extend((line, NO_NEWLINE))
    return output, added, removed


def _literal(data: bytes) -> list[str]:
    packed = zlib.compress(data, 9)
    lines = [f"literal {len(data)}"]
    for start in range(0, len(packed), BINARY_LINE_BYTES):
        chunk = packed[start : start + BINARY_LINE_BYTES]
        size = len(chunk)
        marker = chr(ord("A") + size - 1) if size <= 26 else chr(ord("a") + size - 27)
        lines.append(marker + base64.b85encode(chunk, pad=True).decode("ascii"))
    return [*lines, ""]


def _binary(before: bytes | None, after: bytes | None, file_mode: str) -> list[str]:
    old = blob_id(before) if before is not None else ZERO_ID
    new = blob_id(after) if after is not None else ZERO_ID
    mode = f" {file_mode}" if file_mode and before is not None and after is not None else ""
    return [
        f"index {old}..{new}{mode}",
        "GIT binary patch",
        *_literal(after or b""),
        *_literal(before or b""),
    ]


def file_patch(
    path: str,
    before: bytes | None,
    after: bytes | None,
    executable: bool = False,
    was_executable: bool = False,
) -> FilePatch:
    file_mode = EXECUTABLE_MODE if executable else FILE_MODE
    old_mode = EXECUTABLE_MODE if was_executable else FILE_MODE
    source = NULL_PATH if before is None else quote_path(f"a/{path}")
    target = NULL_PATH if after is None else quote_path(f"b/{path}")
    lines = [f"diff --git {quote_path(f'a/{path}')} {quote_path(f'b/{path}')}"]
    if before is None:
        lines.append(f"new file mode {file_mode}")
    elif after is None:
        lines.append(f"deleted file mode {old_mode}")
    elif old_mode != file_mode:
        lines += [f"old mode {old_mode}", f"new mode {file_mode}"]
    if before is not None and before == after:
        return FilePatch(path, "\n".join(lines) + "\n", 0, 0, False)
    old, new = before or b"", after or b""
    if is_binary(old) or is_binary(new):
        lines += _binary(before, after, file_mode if old_mode == file_mode else "")
        return FilePatch(path, "\n".join(lines) + "\n", 0, 0, True)
    hunks, added, removed = _hunks(decode(old), decode(new))
    if hunks:
        lines += [_file_line("---", source), _file_line("+++", target), *hunks]
    return FilePatch(path, "\n".join(lines) + "\n", added, removed, False)


def join_patches(patches: Iterable[FilePatch]) -> str:
    return "".join(patch.text for patch in patches)
