from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.run_reports import RunReports
from cuanta.bootstrap import Container
from cuanta.domain.evidence_pack import EvidencePack, OutsideEdits, SeniorScope, check_pack
from cuanta.domain.ledger import Run
from cuanta.domain.messages import english, msg
from cuanta.domain.role_handoff import Fact
from cuanta.domain.scout_report import scout_payload
from tests.cli.test_engine_guarantees import codex_ready, cross_args, forge
from tests.fakes import FakeRunner, FakeStream, copy_repo
from tests.real_run import MANDATES, REAL_MODEL, phased_mandate
from tests.support import invoke

SCOUT_TOOLS = ["Read", "Grep", "Glob"]
PURE = ("--profile", "balanced", "--pure", "--model", REAL_MODEL, "--preset", "best")
RESULT = '{"type":"result","subtype":"success","total_cost_usd":0.01,"is_error":false}'


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
        "pure": None,
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


def route_lines(data: dict[str, object]) -> list[str]:
    return [
        str(line)
        for line in listed(data["team"])
        if str(line).startswith("team · ") and " → " in str(line)
    ]


def test_a_premium_pure_model_keeps_the_pipeline_unless_the_scout_is_forced(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    configure(tmp_path, "[runs]\nscout_threshold = 0.01\n")
    auto = preview(tmp_path, *PURE)
    assert auto["shape"] == {
        "shape": "pipeline",
        "forced": False,
        "exploration_share": None,
        "threshold": 0.01,
        "scout_mode": None,
        "pinned": False,
        "pure": REAL_MODEL,
    }
    agents = agents_of(auto)
    assert "scout" not in agents
    assert {spec["model"] for spec in agents.values()} == {REAL_MODEL}
    assert english(msg("scout.shape_pure", model=REAL_MODEL)) in listed(auto["team"])
    routes = route_lines(auto)
    assert routes and all(" → opus (premium) · pure: " in line for line in routes)
    forced = preview(tmp_path, *PURE, "--shape", "scout")
    shape = forced["shape"]
    assert isinstance(shape, dict) and shape["shape"] == "scout"
    assert agents_of(forced)["scout"]["model"] == REAL_MODEL
    assert any(
        line.startswith("team · scout → opus (premium) · pure: ") for line in route_lines(forced)
    )
    cross = preview(tmp_path, *PURE[:5], "--cross-engine")
    assert set(roles_of(cross).values()) == {"opus"}
    assert english(msg("scout.shape_pure", model=REAL_MODEL)) in listed(cross["team"])
    assert not fake_runner.stdins


def test_a_pure_premium_launch_card_says_the_scout_is_skipped(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    configure(tmp_path, "[runs]\nscout_threshold = 0.01\n")
    fake_runner.streams["claude -p"] = FakeStream([RESULT])
    result = invoke(cross_args(tmp_path, "--route", "fixed", "--max-budget-usd", "1", *PURE))
    assert result.exit_code == 0, result.stdout
    output = " ".join(result.stdout.split())
    assert english(msg("scout.shape_pure", model=REAL_MODEL)) in output
    assert "team · senior → opus (premium) · pure: --pure runs every role on" in output
    assert "because your policy uses" not in output
    calls = [call for call in fake_runner.calls if call[:2] == ("claude", "-p")]
    assert len(calls) == 1
    sent = Path(calls[0][calls[0].index("--agents") + 1]).read_text(encoding="utf-8")
    agents = json.loads(sent)
    assert "scout" not in agents
    assert {spec["model"] for spec in agents.values()} == {REAL_MODEL}


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


ANCHOR_NOTES = [
    "File:line references not found among the indexed files: app/models/legacy.py:12",
    "File:line references past the last line of their file: app/routers/consult.py:900",
]


BALANCED = ("--profile", "balanced")


def backend_preview(root: Path, *flags: str) -> dict[str, object]:
    result = invoke(
        [
            "mandate",
            "--type",
            "feature",
            "--what",
            "Implementar el mandato adjunto",
            "--tests",
            "pytest en verde",
            "--out-of-scope",
            "el frontend",
            "--depth",
            "deep",
            "--route",
            "fixed",
            "--project",
            str(root),
            "--dry-run",
            "--json",
            *flags,
        ]
    )
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert isinstance(data, dict)
    return data


def test_a_mandate_with_anchors_only_in_its_evidence_packs_them_for_the_session_and_the_scout(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    root = copy_repo("python_backend", tmp_path)
    forge(root)
    data = backend_preview(
        root, *BALANCED, "--evidence", str(MANDATES / "real_run_es.md"), "--shape", "scout"
    )
    prompt = str(data["prompt"])
    assert (
        "[L2 anchor:app/services/interpreter.py:287-289 app/services/interpreter.py:284-292]"
        in prompt
    )
    assert "ANCHOR_287" in prompt
    scout = str(agents_of(data)["scout"]["prompt"])
    assert "anchor:app/routers/consult.py:67" in scout and "ANCHOR_67" in scout
    assert data["pack_notes"] == ANCHOR_NOTES
    assert [line for line in listed(data["team"]) if line in ANCHOR_NOTES] == ANCHOR_NOTES
    assert not fake_runner.stdins


def test_read_only_words_freeze_a_writing_mandate_only_outside_its_phases_and_it_says_why(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    root = copy_repo("python_backend", tmp_path)
    forge(root)
    phased = tmp_path / "phased.md"
    phased.write_text(
        phased_mandate().replace("## Fase 1 — Auditoría", "## Fase 1 — Auditoría (solo lectura)"),
        encoding="utf-8",
    )
    writable = backend_preview(root, *BALANCED, "--evidence", str(phased))
    assert writable["pack_notes"] == ANCHOR_NOTES
    senior = agents_of(writable)["python-senior"]
    assert "Write" not in listed(senior.get("disallowedTools", []))
    frozen = backend_preview(
        root, *BALANCED, "--why", "Solo lectura: revisar app/services/interpreter.py:287"
    )
    notes = [
        'Change plan: read-only, because the request says "Solo lectura"',
        "Referenced files the change plan protects (context only, not editable): "
        "app/services/interpreter.py:287",
    ]
    assert listed(frozen["pack_notes"]) == notes
    assert [line for line in listed(frozen["team"]) if line in notes] == notes
    assert "Write" in listed(agents_of(frozen)["python-senior"]["disallowedTools"])


def test_the_per_role_dry_run_names_the_anchors_that_did_not_resolve(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    codex_ready(fake_runner)
    root = copy_repo("python_backend", tmp_path)
    data = backend_preview(
        root, "--engine", "codex", "--evidence", str(MANDATES / "real_run_es.md")
    )
    assert data["per_role"] is True
    assert data["pack_notes"] == ANCHOR_NOTES
    assert [line for line in listed(data["team"]) if line in ANCHOR_NOTES] == ANCHOR_NOTES
    assert not fake_runner.stdins


PHASED_GUARD = (
    "Contexto del cambio.\n\n"
    "## Fase 1 — Auditoría\n"
    "No toques app/routers/consult.py ni app/services/interpreter.py.\n\n"
    "## Fase 3 — Endpoint\n"
    "Corrige app/routers/consult.py:67.\n"
)


def test_a_phase_guard_on_a_file_a_later_phase_anchors_stays_editable_and_the_card_says_so(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    root = copy_repo("python_backend", tmp_path)
    forge(root)
    evidence = tmp_path / "phased.md"
    evidence.write_text(PHASED_GUARD, encoding="utf-8")
    data = backend_preview(root, *BALANCED, "--evidence", str(evidence))
    note = (
        "Change plan: app/routers/consult.py stays editable: a phase says not to touch it, but "
        "the request anchors or names it elsewhere; put it in Out of scope to protect it"
    )
    assert note in listed(data["pack_notes"]) and note in listed(data["team"])
    denied = listed(agents_of(data)["python-senior"].get("disallowedTools", []))
    assert "Edit(app/routers/consult.py)" not in denied
    assert "Edit(app/services/interpreter.py)" in denied
    assert not fake_runner.stdins


def test_a_pin_in_the_routing_config_keeps_the_auto_profile_on_the_team(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    forge(tmp_path)
    monkeypatch.setattr(Container, "fast_ready", lambda self, name: True)
    assert preview(tmp_path)["agents_file"] is None
    configure(tmp_path, '[routing.models]\ntester = "claude:sonnet"\n')
    team = preview(tmp_path)
    assert agents_of(team)["tester"]["model"] == "claude-sonnet-5"
    shape = team["shape"]
    assert isinstance(shape, dict) and shape["shape"] != "single"
    refused = invoke(
        cross_args(tmp_path, "--route", "fixed", "--dry-run", "--json", "--profile", "fast")
    )
    assert refused.exit_code == 1, refused.stdout
    error = json.loads(refused.stdout)["error"]
    assert isinstance(error, dict)
    assert (
        error["message"]
        == "fast implementation runs one model (claude-opus-5-5), and a role pin names another"
    )
    assert not fake_runner.stdins
