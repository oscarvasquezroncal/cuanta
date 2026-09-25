from __future__ import annotations

from pathlib import Path

from cuanta.domain.capsules import capsule_digest


class FileCapsuleStore:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def put(self, content: str) -> tuple[str, str, int]:
        data = content.encode("utf-8", errors="replace")
        digest = capsule_digest(data)
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._directory / f"{digest}.log"
        if not path.exists():
            path.write_bytes(data)
        return digest, str(path), len(data)

    def read(self, digest: str) -> str | None:
        path = self._directory / f"{digest}.log"
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
