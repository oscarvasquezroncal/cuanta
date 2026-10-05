from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_node_launcher_contracts() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional locally; the npm launcher requires Node 18 or newer")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [node, "--test", "packaging/npm/test/launcher.test.cjs"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
