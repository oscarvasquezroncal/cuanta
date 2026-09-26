from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

SIGNATURE = b"DIRC"
HEADER = struct.Struct(">4sII")
ENTRY_FIXED = 62
SHA_START = 40
SHA_END = 60
EXTENDED_FLAG = 0x4000
NAME_MASK = 0x0FFF
STAGE_SHIFT = 12
SUPPORTED = frozenset({2, 3, 4})
STAT = struct.Struct(">IIIIIIIIII")
NANOSECONDS = 1_000_000_000
SIZE_MASK = 0xFFFFFFFF


@dataclass(frozen=True, slots=True)
class IndexEntry:
    sha: str
    size: int
    mtime_ns: int

    def matches(self, size: int, mtime_ns: int) -> bool:
        return self.size == size & SIZE_MASK and self.mtime_ns == mtime_ns


def blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def _varint(data: bytes, offset: int) -> tuple[int, int]:
    byte = data[offset]
    offset += 1
    value = byte & 0x7F
    while byte & 0x80:
        value += 1
        byte = data[offset]
        offset += 1
        value = (value << 7) + (byte & 0x7F)
    return value, offset


def parse_index(data: bytes) -> dict[str, IndexEntry] | None:
    if len(data) < HEADER.size:
        return None
    signature, version, count = HEADER.unpack_from(data)
    if signature != SIGNATURE or version not in SUPPORTED:
        return None
    entries: dict[str, IndexEntry] = {}
    offset = HEADER.size
    previous = b""
    try:
        for _ in range(count):
            start = offset
            sha = data[start + SHA_START : start + SHA_END].hex()
            fields = STAT.unpack_from(data, start)
            mtime = fields[2] * NANOSECONDS + fields[3]
            (flags,) = struct.unpack_from(">H", data, start + SHA_END)
            offset = start + ENTRY_FIXED
            if version >= 3 and flags & EXTENDED_FLAG:
                offset += 2
            if version == 4:
                strip, offset = _varint(data, offset)
                end = data.index(b"\0", offset)
                name = previous[: len(previous) - strip] + data[offset:end]
                offset = end + 1
            else:
                end = data.index(b"\0", offset)
                name = data[offset:end]
                offset = start + ((end - start) // 8 + 1) * 8
            previous = name
            if (flags >> STAGE_SHIFT) & 3 == 0:
                path = name.decode("utf-8", errors="surrogateescape")
                entries[path] = IndexEntry(sha, fields[9], mtime)
    except (IndexError, ValueError, struct.error):
        return None
    return entries
