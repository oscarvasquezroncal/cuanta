from __future__ import annotations

import sqlite3
from pathlib import Path


def closed_snapshot_uri(path: Path, label: str) -> str:
    if any(item.is_symlink() or item.is_junction() for item in (path, path.parent)):
        raise OSError(f"A read-only {label} cannot use links or junctions")
    if not path.is_file():
        raise OSError(f"The read-only {label} is unavailable")
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists() or sidecar.is_symlink() or sidecar.is_junction():
            raise sqlite3.DatabaseError(f"The read-only {label} requires a closed snapshot")
    return f"{path.resolve().as_uri()}?mode=ro&immutable=1"
