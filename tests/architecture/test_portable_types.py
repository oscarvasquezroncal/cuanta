from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_sandbox_types_hold_on_posix(platform: str, tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "--platform",
            platform,
            "--cache-dir",
            str(tmp_path / "mypy"),
            "src/cuanta/adapters/system/sandbox.py",
            "src/cuanta/adapters/system/sandbox_cleanup.py",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
