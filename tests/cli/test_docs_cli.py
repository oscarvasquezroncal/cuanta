from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.run_reports import RunReports
from cuanta.bootstrap import Container
from tests.cli.test_engine_guarantees import codex_ready, cross_args, forge
from tests.cli.test_scout_cli import agents_of, configure, listed, preview, roles_of
from tests.fakes import FakeRunner, FakeStream
from tests.real_run import MANDATES
from tests.support import invoke

RESULT = '{"type":"result","subtype":"success","total_cost_usd":0.01,"is_error":false}'
EVIDENCE = str(MANDATES / "real_run_es.md")
FEATURE = (
    "--type",
    "feature",
    "--profile",
    "balanced",
    "--what",
    "Ejecuta el mandato por fases",
    "--tests",
    "pytest en verde",
)
AGENT_DOCS = {"on": True, "reason": "agent", "field": "why", "term": "docs-updater"}
AGENT_LINE = "Docs: on, the request names the docs-updater agent in the evidence"
AGENT_ROW = "docs on (agent: docs-updater in the evidence)"
OFF_PROMPT = "Docs are off for this run: do not invoke the docs-updater."


def refused(result_stdout: str) -> str:
    error = json.loads(result_stdout)["error"]
    assert isinstance(error, dict)
    return str(error["message"])


def test_a_mandate_whose_docs_phase_is_only_in_the_evidence_keeps_the_docs_updater(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    data = preview(tmp_path, *FEATURE, "--evidence", EVIDENCE)
    assert data["docs"] == AGENT_DOCS
    assert AGENT_LINE in listed(data["team"])
    assert OFF_PROMPT not in str(data["prompt"])
    assert "docs-updater" in agents_of(data)
    assert not fake_runner.stdins


def test_the_run_card_and_the_result_name_the_rule_that_turned_docs_on(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    fake_runner.streams["claude -p"] = FakeStream([RESULT])
    result = invoke(
        cross_args(tmp_path, *FEATURE, "--route", "fixed", "--evidence", EVIDENCE, "--plain")
    )
    assert result.exit_code == 0, result.stdout
    text = " ".join(result.stdout.split())
    assert AGENT_LINE in text
    assert AGENT_ROW in text
    assert "docs off (not requested)" not in text
    sent = [stdin for stdin in fake_runner.stdins if stdin]
    assert sent and all(OFF_PROMPT not in stdin for stdin in sent)
    listed_runs = json.loads(invoke(["runs", "list", "--json", "--project", str(tmp_path)]).stdout)
    run_id = str(listed_runs["runs"][0]["id"])
    meta = RunReports(LocalWorkspace(tmp_path)).meta(run_id)
    assert meta is not None and meta["docs"] == AGENT_DOCS
    shown = invoke(["runs", "show", run_id, "--plain", "--project", str(tmp_path)])
    assert AGENT_ROW in " ".join(shown.stdout.split())


@pytest.mark.parametrize(
    ("config", "flags", "docs", "line"),
    [
        (
            '[runs]\ndocs = "off"\n',
            ("--docs", "on"),
            {"on": True, "reason": "flag_on"},
            "Docs: on (--docs on)",
        ),
        (
            "",
            ("--docs", "off", "--evidence", EVIDENCE),
            {"on": False, "reason": "flag_off"},
            "Docs: off (--docs off)",
        ),
        (
            '[runs]\ndocs = "on"\n',
            ("--docs", "auto", "--evidence", EVIDENCE),
            AGENT_DOCS,
            AGENT_LINE,
        ),
    ],
)
def test_the_docs_option_wins_over_runs_docs_and_names_itself(
    tmp_path: Path,
    fake_runner: FakeRunner,
    config: str,
    flags: tuple[str, ...],
    docs: dict[str, object],
    line: str,
) -> None:
    forge(tmp_path)
    if config:
        configure(tmp_path, config)
    data = preview(tmp_path, *FEATURE, *flags)
    assert data["docs"] == docs
    assert line in listed(data["team"])
    on = docs["on"] is True
    assert ("docs-updater" in agents_of(data)) is on
    assert (OFF_PROMPT in str(data["prompt"])) is not on
    assert not fake_runner.stdins


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (("--docs", "maybe"), "unknown --docs maybe"),
        (("--classic", "--docs", "off"), "--classic runs with docs on"),
        (("--classic", "--docs", "auto"), "--classic runs with docs on"),
        (("--docs", "off", "--role-model", "docs=haiku"), "--docs off and a docs pin disagree"),
        (
            ("--profile", "fast", "--docs", "on"),
            "fast implementation runs one writer without the docs role",
        ),
        (("--simple", "--docs", "on"), "simple mode runs without the docs role"),
        (("--type", "investigation", "--docs", "on"), "investigations run without the docs role"),
    ],
)
def test_contradicting_docs_options_are_refused_before_any_launch(
    tmp_path: Path, fake_runner: FakeRunner, flags: tuple[str, ...], message: str
) -> None:
    forge(tmp_path)
    result = invoke(cross_args(tmp_path, "--dry-run", "--json", *flags))
    assert result.exit_code == 1, result.stdout
    assert message in refused(result.stdout)
    assert not fake_runner.stdins


def test_docs_on_is_refused_when_runs_profile_is_fast(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    configure(tmp_path, '[runs]\nprofile = "fast"\n')
    result = invoke(cross_args(tmp_path, "--dry-run", "--json", "--docs", "on"))
    assert result.exit_code == 1, result.stdout
    assert "fast implementation runs one writer without the docs role" in refused(result.stdout)
    auto = invoke(cross_args(tmp_path, "--dry-run", "--json", "--docs", "auto"))
    assert auto.exit_code == 0, auto.stdout
    assert not fake_runner.stdins


def test_docs_on_keeps_the_auto_profile_on_the_team(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    forge(tmp_path)
    monkeypatch.setattr(Container, "fast_ready", lambda self, name: True)
    fast = preview(tmp_path)
    assert fast["agents_file"] is None and fast.get("docs") is None
    team = preview(tmp_path, "--docs", "on")
    assert team["docs"] == {"on": True, "reason": "flag_on"}
    assert "docs-updater" in agents_of(team)
    assert not fake_runner.stdins


def test_a_gpt_team_follows_the_docs_option_in_the_preview_and_the_run(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_ready(fake_runner)
    monkeypatch.setattr(Container, "build_blocked", lambda self: frozenset({"codex"}))
    asked = preview(tmp_path, "--engine", "codex", *FEATURE, "--evidence", EVIDENCE)
    assert asked["docs"] == AGENT_DOCS and "docs" in roles_of(asked)
    assert AGENT_LINE in listed(asked["team"])
    off = preview(tmp_path, "--engine", "codex", *FEATURE, "--evidence", EVIDENCE, "--docs", "off")
    assert off["docs"] == {"on": False, "reason": "flag_off"} and "docs" not in roles_of(off)
    result = invoke(cross_args(tmp_path, "--engine", "codex", "--route", "fixed", "--docs", "on"))
    assert result.exit_code == 0, result.stdout
    launches = [call for call in fake_runner.calls if call[:2] == ("codex", "exec")]
    assert len(launches) == 4
    text = " ".join(result.stdout.split())
    assert "Docs: on (--docs on)" in text
    assert "docs: on (--docs on)" in text
