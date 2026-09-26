from __future__ import annotations

import codecs
import json
import zipfile
from pathlib import Path

import pytest

from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from tests.fakes import FakeRunner
from tests.ledger_fixture import RUN, fill
from tests.support import assert_golden, invoke


def seed(root: Path) -> None:
    ledger = SqliteLedger(root / ".cuanta" / "ledger.db")
    fill(ledger)
    ledger.close()


@pytest.fixture
def seeded(tmp_path: Path, fake_runner: FakeRunner) -> Path:
    seed(tmp_path)
    return tmp_path


def test_spectrum_plain_golden(seeded: Path) -> None:
    result = invoke(["spectrum", "--plain", "--project", str(seeded)], env={"COLUMNS": "120"})
    assert result.exit_code == 0, result.stdout + result.stderr
    assert_golden("spectrum_plain.txt", result.stdout)


def test_spectrum_json_contract(seeded: Path) -> None:
    result = invoke(["spectrum", RUN[:8], "--json", "--project", str(seeded)])
    document = json.loads(result.stdout)
    assert document["runs"] == [RUN]
    totals = document["totals"]
    assert (
        totals["total"]
        == 4000
        + 20000
        + 3000
        + 800
        + 1000
        + 26000
        + 500
        + 2000
        + 10000
        + 500
        + 300
        + 500
        + 30000
        + 200
    )
    assert totals["cost_usd"] == 0.9
    assert document["utilization"]["label"] == "heuristic v1"
    assert document["utilization"]["value"] > 0
    kinds = {leak["kind"] for leak in document["leaks"]}
    assert {"repeated_read", "test_output", "amplification"} <= kinds
    assert document["tree"]["children"][0]["label"] == "main"
    assert document["overhead"]["first_request_cache"] == {
        "state": "warm",
        "cache_read_tokens": 20_000,
        "cache_write_tokens": 3_000,
        "context_tokens": 27_000,
        "share": 0.7407,
    }
    assert_golden("spectrum.json", json.dumps(document, indent=2, sort_keys=True))


def test_spectrum_views_and_plan(seeded: Path) -> None:
    by_model = json.loads(
        invoke(["spectrum", "--by", "model", "--json", "--project", str(seeded)]).stdout
    )
    assert [row["key"] for row in by_model["by"]["rows"]] == ["claude-opus", "claude-sonnet"]
    plan = json.loads(
        invoke(["spectrum", "HU-007", "--plan", "--json", "--project", str(seeded)]).stdout
    )
    assert plan["selection"].startswith("HU-007")
    assert len(plan["plan"]["windows"]) == 1
    assert "do not publish exact quotas" in plan["plan"]["note"]
    pretty = invoke(
        ["spectrum", "--by", "file", "--project", str(seeded), "--no-emoji"], pretty=True
    )
    assert pretty.exit_code == 0
    assert "src/app.py" in pretty.stdout


def test_spectrum_errors(tmp_path: Path, fake_runner: FakeRunner) -> None:
    empty = invoke(["spectrum", "--json", "--project", str(tmp_path)])
    assert empty.exit_code == 1
    assert "no runs recorded" in json.loads(empty.stdout)["error"]["message"]
    seed(tmp_path)
    assert invoke(["spectrum", "HU-999", "--json", "--project", str(tmp_path)]).exit_code == 1
    assert (
        invoke(["spectrum", "--by", "planet", "--json", "--project", str(tmp_path)]).exit_code == 1
    )


def test_ledger_export(seeded: Path) -> None:
    whole = json.loads(invoke(["ledger", "export", "--json", "--project", str(seeded)]).stdout)
    assert set(whole) == {"runs", "events", "test_runs", "decisions", "baselines"}
    csv_result = invoke(
        [
            "ledger",
            "export",
            "--format",
            "csv",
            "--table",
            "events",
            "--plain",
            "--project",
            str(seeded),
        ]
    )
    assert csv_result.stdout.splitlines()[0].startswith("run_id,source")
    target = seeded / "runs.csv"
    written = invoke(
        [
            "ledger",
            "export",
            "--format",
            "csv",
            "--table",
            "runs",
            "--out",
            str(target),
            "--plain",
            "--project",
            str(seeded),
        ]
    )
    assert written.exit_code == 0
    assert RUN in target.read_text(encoding="utf-8")
    bundle = invoke(
        ["ledger", "export", "--format", "csv", "--all", "--json", "--project", str(seeded)]
    )
    assert bundle.exit_code == 0, bundle.stdout
    archive = Path(json.loads(bundle.stdout)["path"])
    assert archive.name == "ledger.zip"
    with zipfile.ZipFile(archive) as opened:
        assert sorted(opened.namelist()) == [
            "baselines.csv",
            "decisions.csv",
            "events.csv",
            "runs.csv",
            "test_runs.csv",
        ]
        assert opened.read("runs.csv").startswith(codecs.BOM_UTF8 + b"id,kind")
    pretty = invoke(["ledger", "export", "--table", "events", "--plain", "--project", str(seeded)])
    assert '"raw"' not in pretty.stdout
    assert pretty.stdout.startswith("{\n  ")
    raw = invoke(
        [
            "ledger",
            "export",
            "--table",
            "events",
            "--include-raw",
            "--plain",
            "--project",
            str(seeded),
        ]
    )
    assert '"raw"' in raw.stdout


def test_rebuild_requires_import(seeded: Path) -> None:
    result = invoke(["spectrum", "--rebuild", "--plain", "--project", str(seeded)])
    assert result.exit_code != 0
    assert "--rebuild needs --import" in result.stderr + result.stdout


def test_import_rebuild_reports_removed_events(seeded: Path, tmp_path: Path) -> None:
    result = invoke(
        ["spectrum", "--import", "--rebuild", "--json", "--project", str(seeded)],
        env={"CUANTA_HOME": str(tmp_path / "home"), "HOME": str(tmp_path / "home")},
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    document = json.loads(result.stdout)
    assert set(document["imported"]) == {"claude_transcript", "codex_transcript"}
    assert all("removed" in item for item in document["imported"].values())
