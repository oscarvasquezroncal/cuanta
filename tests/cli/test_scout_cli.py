from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.run_reports import RunReports
from cuanta.domain.evidence_pack import EvidencePack, OutsideEdits, SeniorScope, check_pack
from cuanta.domain.ledger import Run
from cuanta.domain.role_handoff import Fact
from cuanta.domain.scout_report import scout_payload
from tests.cli.test_engine_guarantees import codex_ready, cross_args, forge
from tests.fakes import FakeRunner
from tests.support import invoke

SCOUT_TOOLS = ["Read", "Grep", "Glob"]


def configure(root: Path, text: str) -> None:
    state = root / ".cuanta"
    state.mkdir(exist_ok=True)
    (state / "config.toml").write_text(text, encoding="utf-8")


def preview(root: Path, *flags: str) -> dict[str, object]:
    result = invoke(cross_args(root, "--route", "fixed", "--dry-run", "--json", *flags))
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert isinstance(data, dict)
    return data


def listed(value: object) -> list[object]:
    assert isinstance(value, list)
    return value


def roles_of(data: dict[str, object]) -> dict[str, object]:
    rows = [row for row in listed(data["roles"]) if isinstance(row, dict)]
    return {str(row["role"]): row["model"] for row in rows}


def agents_of(data: dict[str, object]) -> dict[str, dict[str, object]]:
    found = json.loads(Path(str(data["agents_file"])).read_text(encoding="utf-8"))
    assert isinstance(found, dict)
    return found


def test_a_forced_scout_shape_gives_the_claude_session_a_read_only_haiku_scout(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    data = preview(tmp_path, "--shape", "scout")
    assert "per_role" not in data
    agents = agents_of(data)
    assert set(agents) == {"scout", "python-senior", "tester"}
    scout = agents["scout"]
    assert scout["model"] == "claude-haiku-4-5"
    tools = scout["tools"]
    assert isinstance(tools, list)
    assert [tool for tool in tools if tool in SCOUT_TOOLS] == SCOUT_TOOLS
    assert not {"Edit", "Write", "Bash"} & set(tools)
    prompt = str(data["prompt"])
    assert "SCOUT AND SENIOR SHAPE (set by cuanta for this run):" in prompt
    assert "Docs are off for this run: do not invoke the docs-updater." in prompt
    assert data["shape"] == {
        "shape": "scout",
        "forced": True,
        "exploration_share": None,
        "threshold": 0.35,
        "scout_mode": "native",
        "pinned": False,
    }
    team = data["team"]
    assert isinstance(team, list)
    assert "Shape: scout and senior (forced); the scout runs as a subagent in the session" in team
    assert any(str(line).startswith("team · scout → haiku (economy)") for line in team)
    envelope = data["envelope"]
    assert isinstance(envelope, dict) and envelope["shape"] == "scout"
    assert [row["role"] for row in envelope["roles"]][:2] == ["orchestrator", "scout"]
    assert not fake_runner.stdins


def test_the_gpt_team_scout_is_a_read_only_luna_launch_before_the_senior(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    codex_ready(fake_runner)
    data = preview(tmp_path, "--engine", "codex", "--shape", "scout")
    assert data["per_role"] is True
    assert roles_of(data) == {"scout": "gpt-6-luna", "senior": "gpt-6-sol", "tester": "gpt-6-sol"}
    shape = data["shape"]
    assert isinstance(shape, dict) and shape["scout_mode"] == "launch"
    team = listed(data["team"])
    assert "Shape: scout and senior (forced); the scout runs as its own read-only launch" in team
    assert not any("as a subagent in the session" in str(line) for line in team)
    envelope = data["envelope"]
    assert isinstance(envelope, dict) and envelope["shape"] == "scout"
    assert [row["role"] for row in envelope["roles"]] == ["scout", "senior", "tester"]
    result = invoke(
        cross_args(tmp_path, "--engine", "codex", "--route", "fixed", "--shape", "scout")
    )
    assert result.exit_code == 0, result.stdout
    launches = [call for call in fake_runner.calls if call[:2] == ("codex", "exec")]
    assert [call[call.index("--model") + 1] for call in launches] == [
        "gpt-6-luna",
        "gpt-6-sol",
        "gpt-6-sol",
    ]
    assert launches[0][launches[0].index("--sandbox") + 1] == "read-only"
    assert launches[1][launches[1].index("--sandbox") + 1] != "read-only"
    prompts = [text for text in fake_runner.stdins if text]
    senior = next(text for text in prompts if "You are the senior of a pipeline" in text)
    assert "=== EVIDENCE PACK FROM THE SCOUT (checked by cuanta) ===" in senior
    assert "HANDOFF CHAIN FROM EARLIER ROLES" not in senior
    assert not any("multi_agent=true" in " ".join(call) for call in launches)
    for call in launches:
        assert "features.multi_agent=false" in call and "features.multi_agent_v2=false" in call
    output = " ".join(result.stdout.split())
    assert "Shape: scout and senior (forced); the scout runs as its own read-only launch" in output
    assert "as a subagent in the session" not in output
    assert "evidence pack:" in output


def test_the_claude_launch_mode_runs_the_scout_as_its_own_launch(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    configure(tmp_path, '[runs]\nscout_mode = "launch"\n')
    data = preview(tmp_path, "--shape", "scout")
    assert data["per_role"] is True
    assert roles_of(data) == {"scout": "haiku", "senior": "opus", "tester": "sonnet"}
    shape = data["shape"]
    assert isinstance(shape, dict) and shape["scout_mode"] == "launch"
    team = listed(data["team"])
    assert team[0] == "team · Claude team · one launch per role"
    assert "Shape: scout and senior (forced); the scout runs as its own read-only launch" in team
    assert not fake_runner.stdins


@pytest.mark.parametrize(("threshold", "scout"), [("0.01", True), ("1.0", False)])
def test_the_default_shape_follows_the_exploration_share_of_the_forecast(
    tmp_path: Path, fake_runner: FakeRunner, threshold: str, scout: bool
) -> None:
    forge(tmp_path)
    configure(tmp_path, f"[runs]\nscout_threshold = {threshold}\n")
    data = preview(tmp_path)
    shape = data["shape"]
    assert isinstance(shape, dict)
    assert shape["forced"] is False and shape["threshold"] == float(threshold)
    share = shape["exploration_share"]
    assert isinstance(share, float) and 0 < share < 1
    assert shape["shape"] == ("scout" if scout else "pipeline")
    agents = agents_of(data)
    assert ("scout" in agents) is scout
    assert ("architecture-analyst" in agents) is not scout
    team = " ".join(str(line) for line in listed(data["team"]))
    assert ("because exploration is" in team) is scout
    forced = preview(tmp_path, "--shape", "pipeline")
    assert forced["shape"] == {
        **shape,
        "shape": "pipeline",
        "forced": True,
        "exploration_share": None,
        "scout_mode": None,
    }
    assert not fake_runner.stdins


def test_a_docs_pin_turns_docs_on_when_the_request_does_not_ask_for_docs(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    codex_ready(fake_runner)
    unpinned = preview(tmp_path)
    assert unpinned["docs"] == {"on": False, "reason": "not_requested"}
    assert "docs-updater" not in agents_of(unpinned)
    pinned = preview(tmp_path, "--role-model", "docs=haiku")
    assert pinned["docs"] == {"on": True, "reason": "pinned"}
    assert agents_of(pinned)["docs-updater"]["model"] == "claude-haiku-4-5"
    assert "Docs: on, the docs role is pinned" in listed(pinned["team"])
    assert "do not invoke the docs-updater" not in str(pinned["prompt"])
    gpt = preview(tmp_path, "--engine", "codex", "--role-model", "docs=gpt-6-luna")
    assert gpt["docs"] == {"on": True, "reason": "pinned"}
    assert roles_of(gpt)["docs"] == "gpt-6-luna"
    assert not fake_runner.stdins


def test_an_analyst_pin_keeps_the_pipeline_and_a_scout_pin_picks_the_scout(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    configure(tmp_path, "[runs]\nscout_threshold = 0.01\n")
    auto = preview(tmp_path)
    assert isinstance(auto["shape"], dict) and auto["shape"]["shape"] == "scout"
    analyst = preview(tmp_path, "--role-model", "analyst=sonnet")
    shape = analyst["shape"]
    assert isinstance(shape, dict)
    assert (shape["shape"], shape["pinned"], shape["exploration_share"]) == (
        "pipeline",
        True,
        None,
    )
    agents = agents_of(analyst)
    assert agents["architecture-analyst"]["model"] == "claude-sonnet-5" and "scout" not in agents
    configure(tmp_path, "[runs]\nscout_threshold = 1.0\n")
    scout = preview(tmp_path, "--role-model", "scout=sonnet")
    picked = scout["shape"]
    assert isinstance(picked, dict) and (picked["shape"], picked["pinned"]) == ("scout", True)
    assert agents_of(scout)["scout"]["model"] == "claude-sonnet-5"
    assert "Shape: scout and senior, because the scout is pinned" in " ".join(
        str(line) for line in listed(scout["team"])
    )
    assert not fake_runner.stdins


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (("--simple",), "--shape scout needs a team; simple mode runs one agent"),
        (("--engine", "opencode"), "--shape scout needs a Claude or GPT team"),
        (("--route", "off"), "--shape scout needs routing: the scout is a routed role"),
    ],
)
def test_a_scout_shape_that_cannot_run_is_refused(
    tmp_path: Path, fake_runner: FakeRunner, flags: tuple[str, ...], message: str
) -> None:
    forge(tmp_path)
    result = invoke(cross_args(tmp_path, "--shape", "scout", "--dry-run", "--json", *flags))
    assert result.exit_code == 1, result.stdout
    error = json.loads(result.stdout)["error"]
    assert message in error["message"]
    assert not fake_runner.stdins


def test_scout_pins_are_honored_and_scout_shapes_are_refused_for_investigations(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    pinned = preview(tmp_path, "--shape", "scout", "--role-model", "scout=sonnet")
    assert agents_of(pinned)["scout"]["model"] == "claude-sonnet-5"
    unused = invoke(
        cross_args(
            tmp_path, "--route", "fixed", "--shape", "pipeline", "--role-model", "scout=sonnet"
        )
    )
    assert unused.exit_code != 0
    assert "role pins cannot be honored" in unused.stdout + unused.stderr
    assert "does not run in this shape" in unused.stdout + unused.stderr
    investigation = invoke(
        [
            "mandate",
            "--type",
            "investigation",
            "--what",
            "Explain the fixture",
            "--why",
            "How does it work?",
            "--out-of-scope",
            "No edits",
            "--shape",
            "scout",
            "--dry-run",
            "--project",
            str(tmp_path),
        ]
    )
    assert investigation.exit_code != 0
    text = " ".join((investigation.stdout + investigation.stderr).split())
    assert "--shape scout applies to features, fixes and refactors" in text
    assert not fake_runner.stdins


def test_runs_show_reports_the_scout_pack_the_senior_flags_and_docs(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    (tmp_path / ".cuanta").mkdir()
    ledger = SqliteLedger(tmp_path / ".cuanta" / "ledger.db")
    try:
        ledger.add_run(
            Run(
                "01JSCOUT",
                "cross",
                "codex",
                started_at="2026-09-28T10:00:00Z",
                ended_at="2026-09-28T10:03:00Z",
                status="ok",
                cost_usd=0.31,
                task_type="feature",
                scope="scout",
            )
        )
    finally:
        ledger.close()
    pack = EvidencePack(
        summary="cart",
        facts=(Fact("src/cart.ts", 1, 9, "total"),),
        edit=("src/cart.ts",),
    )
    check = check_pack(pack, None)
    senior = SeniorScope(
        ("src/cart.ts",), (), ("src/app.ts",), OutsideEdits(("src/named.ts",), ("src/stray.ts",))
    )
    RunReports(LocalWorkspace(tmp_path)).save_meta(
        "01JSCOUT",
        {
            "scout": scout_payload("launch", "01JSCOUT", "cap:0123456789abcdef", check, senior),
            "docs": {"on": False, "reason": "not_requested"},
        },
    )
    shown = invoke(["runs", "show", "01JSCOUT", "--json", "--project", str(tmp_path)])
    assert shown.exit_code == 0, shown.stdout
    scout = json.loads(shown.stdout)["scout"]
    assert (scout["mode"], scout["edit_set"], scout["capsule"]) == (
        "launch",
        ["src/cart.ts"],
        "cap:0123456789abcdef",
    )
    assert scout["leaked"] == ["src/app.ts"] and scout["outside_unnamed"] == ["src/stray.ts"]
    assert (scout["docs"], scout["docs_reason"]) == ("off", "not_requested")
    text = " ".join(
        invoke(["runs", "show", "01JSCOUT", "--plain", "--project", str(tmp_path)]).stdout.split()
    )
    assert "evidence pack" in text and "scout launch · cap:0123456789abcdef" in text
    assert "edits outside the set, not named src/stray.ts" in text
    assert "docs off (not requested)" in text
