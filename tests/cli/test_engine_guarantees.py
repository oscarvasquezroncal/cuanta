from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuanta.adapters.engines.codex import CodexEngine
from cuanta.application.mandate_flow import MandateOptions, resolve_budget
from cuanta.application.routing import RoutePlan
from cuanta.bootstrap import Container
from cuanta.cli.commands.mandate import TEAM_BUDGET_USD
from cuanta.domain.depth import DEFAULT_DEPTH, profile
from cuanta.domain.role_budgets import RepairBudget
from cuanta.ports.system import Completed
from tests.fakes import FakeRunner, FakeStream
from tests.support import invoke

CODEX_SLUGS = (
    "gpt-6-luna",
    "gpt-6-sol",
    "gpt-6-astra",
    "gpt-5.6-luna",
    "gpt-5.6-terra",
    "gpt-5.6-sol",
)
AGENT = "---\nname: {name}\ndescription: d\ntools: Read\nmodel: sonnet\n---\nBody of {name}.\n"
FORGE_AGENTS = ("architecture-analyst", "python-senior", "tester", "docs-updater")
TEMPLATE = (
    Path(__file__).parents[2]
    / "src/cuanta/assets/forge/skills/agent-system-init/templates/MANDATE_TEMPLATE.template.md"
)


def mandate_args(root: Path, engine: str) -> list[str]:
    return [
        "mandate",
        "--simple",
        "--engine",
        engine,
        "--type",
        "investigation",
        "--what",
        "Explain the fixture",
        "--why",
        "How does it work?",
        "--out-of-scope",
        "No edits",
        "--project",
        str(root),
    ]


def test_cli_refuses_opencode_investigations_without_spawning(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    result = invoke([*mandate_args(tmp_path, "opencode"), "--json"])
    assert result.exit_code != 0
    assert "graphify" in result.stdout
    assert "cannot enforce read-only" in result.stdout
    assert not fake_runner.stdins


def test_codex_preview_shows_enforcement_and_explicit_sandbox(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    result = invoke([*mandate_args(tmp_path, "codex"), "--dry-run", "--json"])
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    command = data["command"]
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "--skip-git-repo-check" not in command
    assert any("Spend cap: checked after the run" in line for line in data["team"])
    assert any("Turn limit: not available" in line for line in data["team"])
    assert not fake_runner.stdins


def test_codex_launch_warns_before_the_result_and_labels_estimated_cost(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    fake_runner.binaries["codex"] = "/fixture/codex"
    fake_runner.responses["codex exec --help"] = Completed(
        0, " ".join(CodexEngine.required_tokens), ""
    )
    fake_runner.streams["codex exec"] = FakeStream(
        [
            '{"type":"thread.started","thread_id":"fixture"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"# Report"}}',
            '{"type":"turn.completed","usage":{"input_tokens":1000,"cached_input_tokens":0,"output_tokens":100}}',
        ]
    )
    result = invoke(
        [*mandate_args(tmp_path, "codex"), "--model", "gpt-6-luna", "--max-budget-usd", "0.1"]
    )
    assert result.exit_code == 0, result.stdout
    assert "cannot enforce the spend cap" in result.stdout
    assert "(estimated)" in result.stdout
    assert result.stdout.index("cannot enforce") < result.stdout.index("# Report")


@pytest.mark.parametrize(
    ("flags", "configured", "expected"),
    [
        ((), 0, 40),
        (("--depth", "quick"), 0, 20),
        (("--depth", "deep"), 0, 80),
        (("--depth", "quick"), 13, 13),
        (("--depth", "deep", "--max-turns", "1"), 13, 1),
    ],
)
def test_cross_cli_passes_resolved_turn_limit_to_every_claude_role(
    tmp_path: Path,
    fake_runner: FakeRunner,
    flags: tuple[str, ...],
    configured: int,
    expected: int,
) -> None:
    fake_runner.streams["claude -p"] = FakeStream(
        ['{"type":"result","subtype":"success","total_cost_usd":0.001,"is_error":false}']
    )
    result = invoke(
        [
            "mandate",
            "--cross-engine",
            "--route",
            "fixed",
            "--type",
            "bug",
            "--what",
            "Fix incorrect addition",
            "--why",
            "The sum is wrong",
            "--out-of-scope",
            "Documentation",
            "--project",
            str(tmp_path),
            *flags,
        ],
        env={"CUANTA_MAX_TURNS": str(configured)},
    )
    assert result.exit_code == 0, result.stdout
    launches = [call for call in fake_runner.calls if call[:2] == ("claude", "-p")]
    assert len(launches) >= 3
    assert all(call[call.index("--max-turns") + 1] == str(expected) for call in launches)
    assert "Read-only: checked after the run" in result.stdout
    assert "Turn limit: enforced" in result.stdout
    assert "complete" in result.stdout
    assert "analyst: claude" in result.stdout


def cross_args(root: Path, *extra: str) -> list[str]:
    return [
        "mandate",
        "--type",
        "bug",
        "--what",
        "Fix incorrect addition",
        "--why",
        "The sum is wrong",
        "--out-of-scope",
        "Documentation",
        "--project",
        str(root),
        *extra,
    ]


def codex_ready(runner: FakeRunner) -> None:
    runner.binaries["codex"] = "/fixture/codex"
    runner.responses["codex exec --help"] = Completed(0, " ".join(CodexEngine.required_tokens), "")
    models = [{"slug": slug, "display_name": slug, "visibility": "list"} for slug in CODEX_SLUGS]
    runner.responses["codex debug models"] = Completed(0, json.dumps({"models": models}), "")
    runner.streams["codex exec"] = FakeStream(
        [
            '{"type":"thread.started","thread_id":"fixture"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"done"}}',
            '{"type":"turn.completed","usage":{"input_tokens":1000,"cached_input_tokens":0,"output_tokens":100}}',
        ]
    )


def forge(root: Path) -> None:
    agents = root / ".claude" / "agents"
    agents.mkdir(parents=True)
    for name in FORGE_AGENTS:
        (agents / f"{name}.md").write_text(AGENT.format(name=name), encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "MANDATE_TEMPLATE.md").write_text(
        TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8"
    )


def test_mixing_is_retired_from_the_cli(tmp_path: Path, fake_runner: FakeRunner) -> None:
    result = invoke(cross_args(tmp_path, "--cross-engine", "--mix", "claude-plans-codex-writes"))
    assert result.exit_code != 0
    assert "--mix" in result.stdout + result.stderr
    opencode = invoke(cross_args(tmp_path, "--cross-engine", "--engine", "opencode"))
    assert opencode.exit_code != 0
    output = " ".join((opencode.stdout + opencode.stderr).split())
    assert "opencode cannot run a team of separate launches" in output
    assert "--engine claude or --engine codex" in output
    assert not fake_runner.stdins


@pytest.mark.parametrize(
    ("flags", "senior"),
    [((), "claude-opus-5-5"), (("--depth", "quick"), "claude-sonnet-5")],
)
def test_the_claude_team_is_the_default_and_its_agents_file_has_per_role_models(
    tmp_path: Path, fake_runner: FakeRunner, flags: tuple[str, ...], senior: str
) -> None:
    forge(tmp_path)
    result = invoke(cross_args(tmp_path, "--route", "fixed", "--dry-run", "--json", *flags))
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data["engine"] == "claude"
    assert "per_role" not in data
    written = json.loads(Path(data["agents_file"]).read_text(encoding="utf-8"))
    assert {name: spec["model"] for name, spec in written.items()} == {
        "architecture-analyst": "claude-sonnet-5",
        "python-senior": senior,
        "tester": "claude-sonnet-5",
    }
    assert data["docs"] == {"on": False, "reason": "not_requested"}
    assert "Docs: off, the request does not ask for docs" in data["team"]
    assert "Docs are off for this run: do not invoke the docs-updater." in data["prompt"]
    alias = "opus (premium)" if senior == "claude-opus-5-5" else "sonnet (standard)"
    assert any(line.startswith(f"team · senior → {alias}") for line in data["team"])
    assert not fake_runner.stdins


@pytest.mark.parametrize("flags", [(), ("--depth", "quick"), ("--depth", "deep")])
def test_the_engine_option_chooses_a_gpt_team_that_previews_each_role(
    tmp_path: Path, fake_runner: FakeRunner, flags: tuple[str, ...]
) -> None:
    codex_ready(fake_runner)
    args = cross_args(tmp_path, "--engine", "codex", "--route", "fixed", "--dry-run", "--json")
    result = invoke([*args, *flags])
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert (data["engine"], data["per_role"], data["dry_run"]) == ("codex", True, True)
    assert {row["role"]: row["model"] for row in data["roles"]} == {
        "analyst": "gpt-6-sol",
        "senior": "gpt-6-sol",
        "tester": "gpt-6-sol",
    }
    assert {row["engine"] for row in data["roles"]} == {"codex"}
    assert data["team"][0] == "team · GPT team · one launch per role"
    assert "Docs: off, the request does not ask for docs" in data["team"]
    assert data["docs"] == {"on": False, "reason": "not_requested"}
    assert not any("gpt-5.6" in line for line in data["team"])
    assert not fake_runner.stdins


def test_role_pins_stay_inside_the_team_provider(tmp_path: Path, fake_runner: FakeRunner) -> None:
    codex_ready(fake_runner)
    forge(tmp_path)
    gpt = invoke(cross_args(tmp_path, "--engine", "codex", "--role-model", "senior=opus"))
    output = " ".join((gpt.stdout + gpt.stderr).split())
    assert gpt.exit_code != 0
    assert "role pins cannot be honored" in output
    assert "senior: claude:opus belongs to another provider; a team uses one provider (codex)" in (
        output
    )
    for flags in (("--cross-engine",), ()):
        claude = invoke(cross_args(tmp_path, *flags, "--role-model", "tester=codex:gpt-6-sol"))
        text = " ".join((claude.stdout + claude.stderr).split())
        assert claude.exit_code != 0
        assert (
            "tester: codex:gpt-6-sol belongs to another provider; a team uses one provider "
            "(claude)" in text
        )
    assert not fake_runner.stdins
    kept = invoke(
        cross_args(
            tmp_path,
            "--engine",
            "codex",
            "--route",
            "fixed",
            "--role-model",
            "senior=gpt-5.6-sol",
            "--dry-run",
            "--json",
        )
    )
    assert kept.exit_code == 0, kept.stdout
    roles = {row["role"]: row["model"] for row in json.loads(kept.stdout)["roles"]}
    assert roles["senior"] == "gpt-5.6-sol"


def test_a_gpt_team_runs_one_codex_launch_per_role_with_its_own_model(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_ready(fake_runner)
    monkeypatch.setattr(Container, "build_blocked", lambda self: frozenset({"codex"}))
    result = invoke(
        cross_args(
            tmp_path, "--engine", "codex", "--route", "fixed", "--role-model", "senior=gpt-5.6-sol"
        )
    )
    assert result.exit_code == 0, result.stdout
    launches = [call for call in fake_runner.calls if call[:2] == ("codex", "exec")]
    assert [call[call.index("--model") + 1] for call in launches] == [
        "gpt-6-sol",
        "gpt-5.6-sol",
        "gpt-6-sol",
    ]
    assert not any(call[0] == "claude" and "-p" in call for call in fake_runner.calls)
    prompts = [text for text in fake_runner.stdins if text]
    tester = next(text for text in prompts if "You are the tester of a pipeline" in text)
    assert "Do not run node, npm or npx commands (builds or tests)" in tester
    assert "Do not run builds yourself" not in tester
    output = " ".join(result.stdout.split())
    assert "GPT team" in output
    assert "tester: codex gpt-6-sol" in output
    assert "Codex cannot run builds on this Windows host; cuanta verifies instead" in output


def test_unknown_role_pins_are_rejected_before_any_launch(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    result = invoke(cross_args(tmp_path, "--cross-engine", "--role-model", "senior=no-such-model"))
    assert result.exit_code != 0
    output = result.stdout + result.stderr
    assert "role pins cannot be honored" in output
    assert "senior is pinned to no-such-model, which is not in the model catalog" in output
    assert not fake_runner.stdins


@pytest.mark.parametrize(
    ("flags", "cap"),
    [
        (("--max-budget-usd", "0.3"), 0.3),
        (("--depth", "quick"), 0.75),
        (("--depth", "quick", "--max-budget-usd", "0.3"), 0.3),
        ((), 2.0),
        (("--cross-budget-usd", "2", "--max-budget-usd", "0.3"), 0.3),
        (("--cross-budget-usd", "0.5", "--depth", "deep"), 0.5),
        (("--cross-engine", "--depth", "deep"), 5.0),
    ],
)
def test_a_gpt_team_is_capped_by_the_mandate_spend_cap(
    tmp_path: Path, fake_runner: FakeRunner, flags: tuple[str, ...], cap: float
) -> None:
    codex_ready(fake_runner)
    args = cross_args(tmp_path, "--engine", "codex", "--route", "fixed", "--dry-run", "--json")
    result = invoke([*args, *flags])
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data["budget_usd"] == pytest.approx(cap)
    assert not fake_runner.stdins


def test_a_default_gpt_team_gets_the_normal_depth_cap_the_app_gives_it(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    codex_ready(fake_runner)
    args = cross_args(tmp_path, "--engine", "codex", "--route", "fixed", "--dry-run", "--json")
    normal = profile(DEFAULT_DEPTH, "bug").cost_cap_usd
    app = resolve_budget(MandateOptions(depth=DEFAULT_DEPTH.value), "bug", 0.0)
    result = invoke(args)
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["budget_usd"] == pytest.approx(normal) == app
    assert normal > TEAM_BUDGET_USD
    setting = invoke(args, env={"CUANTA_BUDGET_USD": "0.4"})
    assert setting.exit_code == 0, setting.stdout
    assert json.loads(setting.stdout)["budget_usd"] == pytest.approx(0.4)
    assert not fake_runner.stdins


@pytest.mark.parametrize(
    ("flags", "cap"),
    [((), 1.0), (("--depth", "deep"), 1.0), (("--max-budget-usd", "0.3"), 0.3)],
)
def test_a_claude_team_as_separate_launches_keeps_its_default_cap(
    tmp_path: Path, fake_runner: FakeRunner, flags: tuple[str, ...], cap: float
) -> None:
    args = cross_args(tmp_path, "--cross-engine", "--route", "fixed", "--dry-run", "--json")
    result = invoke([*args, *flags])
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["budget_usd"] == pytest.approx(cap)


def test_a_gpt_team_launch_spends_within_the_max_budget(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    codex_ready(fake_runner)
    result = invoke(
        cross_args(tmp_path, "--engine", "codex", "--route", "fixed", "--max-budget-usd", "0.3")
    )
    assert result.exit_code == 0, result.stdout
    output = " ".join(result.stdout.split())
    assert "of $0.3000" in output
    assert "GPT team · one launch per role" in output
    assert "experimental" not in output
    assert [call for call in fake_runner.calls if call[:2] == ("codex", "exec")]


def test_a_gpt_fix_without_checks_shows_and_runs_its_team_without_a_repair_reserve(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    repairs: list[bool] = []
    original = Container.role_budget

    def recording(
        self: Container,
        plan: RoutePlan,
        task_type: str,
        depth: str,
        cap: float,
        repair: bool,
        docs_off: bool = False,
    ) -> RepairBudget:
        repairs.append(repair)
        return original(self, plan, task_type, depth, cap, repair, docs_off)

    monkeypatch.setattr(Container, "role_budget", recording)
    codex_ready(fake_runner)
    result = invoke(
        cross_args(tmp_path, "--engine", "codex", "--route", "fixed", "--max-budget-usd", "0.3")
    )
    assert result.exit_code == 0, result.stdout
    assert repairs == [False, False]
    assert "held for one repair turn" not in " ".join(result.stdout.split())


def test_a_prompted_investigation_stays_one_codex_session(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    codex_ready(fake_runner)
    forge(tmp_path)
    args = [
        "mandate",
        "--engine",
        "codex",
        "--what",
        "Explain the fixture",
        "--why",
        "How does it work?",
        "--out-of-scope",
        "No edits",
        "--dry-run",
        "--project",
        str(tmp_path),
    ]
    result = invoke(args, pretty=True, input_text="investigation\n")
    assert result.exit_code == 0, result.stdout
    output = " ".join(result.stdout.split())
    assert "one launch per role" not in output
    assert "read-only" in output
    assert not fake_runner.stdins
