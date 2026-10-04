from __future__ import annotations

from dataclasses import dataclass

MEBIBYTE = 1_048_576
CUANTA_DIR_WARN_BYTES = 500 * MEBIBYTE
INDEX_DATABASE = "index.db"
SQLITE_MAGIC = b"SQLite format 3\x00"
SQLITE_HEADER_BYTES = 100
PAGE_SIZE_FIELD = slice(16, 18)
FREE_PAGES_FIELD = slice(36, 40)
LARGEST_PAGE = 65_536
REBUILD_SHARE = 4


@dataclass(frozen=True, slots=True)
class DiskUsage:
    total_bytes: int = 0
    files: int = 0
    largest: str = ""
    largest_bytes: int = 0
    reclaimable_bytes: int = 0


def megabytes(size: int) -> str:
    return f"{size / MEBIBYTE:.1f}"


def free_bytes(header: bytes, size: int) -> int:
    if len(header) < SQLITE_HEADER_BYTES or not header.startswith(SQLITE_MAGIC):
        return size
    page = int.from_bytes(header[PAGE_SIZE_FIELD], "big")
    pages = int.from_bytes(header[FREE_PAGES_FIELD], "big")
    return min(size, pages * (LARGEST_PAGE if page == 1 else page))


def rebuild_helps(usage: DiskUsage, limit_bytes: int) -> bool:
    reclaimable = usage.reclaimable_bytes
    return reclaimable > 0 and (
        usage.total_bytes - reclaimable <= limit_bytes
        or reclaimable * REBUILD_SHARE >= usage.total_bytes
    )
