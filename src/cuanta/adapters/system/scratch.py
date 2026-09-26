from __future__ import annotations

import shutil
import tempfile
from pathlib import Path


def scratch_project(prefix: str = "cuanta-probe-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix, dir=tempfile.gettempdir()))


def discard_scratch(path: Path) -> None:
    target = path.resolve()
    if target.parent != Path(tempfile.gettempdir()).resolve() or not target.name.startswith(
        "cuanta-probe-"
    ):
        raise ValueError("scratch path is outside the probe temporary directory")
    shutil.rmtree(target)
