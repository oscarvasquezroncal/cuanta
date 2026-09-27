from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.cli.test_bench_cli import _args
from tests.cli.test_bench_cli import env as _bench_environment
from tests.support import invoke

bench_environment = _bench_environment


@pytest.mark.timeout(300)
def test_two_investigations_accept_delivered_answers_across_all_conditions(
    tmp_path: Path, bench_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_INVESTIGATION", "1")
    values = {**bench_environment, "FAKE_CLAUDE_INVESTIGATION": "1"}
    result = invoke(
        _args(
            tmp_path,
            "--suite",
            "investigation",
            "--reps",
            "1",
            "--seed",
            "7",
            "--shape",
            "pipeline",
            "--pack",
            "off",
            "--depth",
            "normal",
            "--yes",
        ),
        env=values,
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    rows = payload["runs"]
    assert len(rows) == 6
    assert {row["task"] for row in rows} == {
        "next-cart-investigation",
        "python-pricing-investigation",
    }
    assert {row["condition"] for row in rows} == {"baseline", "cuanta", "cuanta-routed"}
    for row in rows:
        assert row["accepted"], row
        assert row["guard_violations"] == []
        assert row["out_of_plan_edits"] == []
        assert row["pack"] == "off"
        assert row["depth"] == "normal"
        assert row["shape"] == ("single" if row["condition"] == "baseline" else "pipeline")
        assert row["read_efficiency"]["value"] == 1.0
        assert row["anatomy"]["totals"]["requests"] > 0
        assert "answer" not in row
    folder = tmp_path / ".cuanta" / "bench" / payload["bench_id"]
    report = (folder / "report.md").read_text(encoding="utf-8")
    assert "Consumption anatomy by condition" in report
    assert "Read efficiency" in report
    assert "cited files read / files read" in report
    assert not (tmp_path / "README.md").exists()
