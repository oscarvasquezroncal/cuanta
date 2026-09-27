from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import invoke


@pytest.mark.parametrize(
    ("flag", "value"), [("--shape", "bad"), ("--pack", "bad"), ("--pack", ""), ("--depth", "bad")]
)
def test_bench_rejects_unknown_option_before_spending(
    tmp_path: Path, flag: str, value: str
) -> None:
    result = invoke(["bench", "run", "--project", str(tmp_path), flag, value])
    assert result.exit_code != 0
    assert f"unknown {flag}" in result.stdout + result.stderr
    assert not (tmp_path / ".cuanta" / "bench").exists()


def test_bench_help_includes_investigation_and_actual_option_flags() -> None:
    result = invoke(["bench", "run", "--help"])
    assert result.exit_code == 0
    assert "investigation" in result.stdout
    assert all(flag in result.stdout for flag in ("--shape", "--pack", "--depth"))
