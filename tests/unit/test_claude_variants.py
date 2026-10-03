from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuanta.adapters.engines.claude_code import build_command
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.session_profile import LeanProfile
from cuanta.domain.claude_variants import (
    EFFORTS,
    pure_environment,
    resolve_variant,
    session_denied_tools,
    variant_settings,
)
from cuanta.domain.engine import EngineRequest
from cuanta.domain.errors import DomainFailure


@pytest.mark.parametrize("effort", EFFORTS)
@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-sonnet-5"])
def test_verified_efforts_are_preserved(effort: str, model: str) -> None:
    assert resolve_variant(effort, model).effort == effort


def test_variants_reject_unknown_values_and_unsupported_fast_output() -> None:
    with pytest.raises(DomainFailure, match="unknown Claude variant"):
        resolve_variant("extreme", "opus")
    with pytest.raises(DomainFailure, match="requires a supported Opus"):
        resolve_variant("fast", "sonnet")
    assert resolve_variant("fast", "opus").fast
    assert not resolve_variant("", "opus").effort
    assert resolve_variant("ultracode", "sonnet").ultracode
    assert resolve_variant("ultracode", "sonnet").effort == "xhigh"


def test_pure_environment_pins_helpers_and_forces_subagent_model() -> None:
    env = pure_environment("sonnet")
    assert env["ANTHROPIC_SMALL_FAST_MODEL"] == "claude-sonnet-5"
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "claude-sonnet-5"
    assert env["CLAUDE_CODE_SUBAGENT_MODEL"] == "claude-sonnet-5"
    assert env["CLAUDE_CODE_SUBAGENT_MODEL_FORCE"] == "1"
    assert env["CLAUDE_CODE_DISABLE_TERMINAL_TITLE"] == "1"
    assert env["CLAUDE_CODE_NO_MODEL_FALLBACK"] == "1"
    with pytest.raises(DomainFailure, match="require a model"):
        pure_environment("")


def test_owned_profile_contains_pure_pins_and_ultracode_settings(tmp_path: Path) -> None:
    profile = LeanProfile(LocalWorkspace(tmp_path), tuple)
    _, path = profile.files(model="sonnet", pure=True, variant="ultracode")
    settings = json.loads(Path(path).read_text(encoding="utf-8"))
    assert settings["model"] == "claude-sonnet-5"
    assert settings["env"]["ANTHROPIC_SMALL_FAST_MODEL"] == "claude-sonnet-5"
    assert settings["ultracode"]
    assert settings["enableWorkflows"]
    assert settings["effortLevel"] == "xhigh"
    assert not settings["fastMode"]
    assert not settings["promptSuggestionEnabled"]
    assert not settings["awaySummaryEnabled"]
    assert not settings["precomputeCompactionEnabled"]


def test_fast_output_is_independent_of_implementation_profile() -> None:
    settings = variant_settings("opus", "fast", True)
    assert settings["fastMode"]
    assert not settings["ultracode"]
    assert "effortLevel" not in settings
    assert variant_settings("sonnet", "low", False)["effortLevel"] == "low"


@pytest.mark.parametrize("effort", EFFORTS)
def test_fast_output_combines_with_each_effort(effort: str) -> None:
    choice = resolve_variant(f"fast-{effort}", "opus")
    assert choice.fast and choice.effort == effort
    assert resolve_variant("fast-ultracode", "opus").ultracode
    with pytest.raises(DomainFailure, match="unknown Claude variant"):
        resolve_variant("fast-invalid", "opus")


def test_adapter_applies_owned_variant_settings_and_disables_prompt_predictions() -> None:
    request = EngineRequest("implement", ".", {}, model="opus", pure=True, variant="fast-low")
    command = build_command(("claude",), request)
    assert command[command.index("--effort") + 1] == "low"
    assert command[command.index("--prompt-suggestions") + 1] == "false"
    settings = json.loads(command[command.index("--settings") + 1])
    assert settings["fastMode"]
    assert settings["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "claude-opus-5-5"


def test_max_effort_uses_the_cli_without_invalidating_the_settings_schema() -> None:
    request = EngineRequest("implement", ".", {}, model="opus", pure=True, variant="max")
    command = build_command(("claude",), request)
    assert command[command.index("--effort") + 1] == "max"
    assert "effortLevel" not in json.loads(command[command.index("--settings") + 1])


def test_one_session_denies_delegation_and_scheduling_but_ultracode_keeps_workflows() -> None:
    fast = session_denied_tools("low")
    assert {"Agent", "Task", "Skill", "Workflow", "SendMessage"}.issubset(fast)
    assert {"RemoteTrigger", "CronCreate", "ScheduleWakeup", "EnterWorktree"}.issubset(fast)
    assert {"Read", "Edit", "Write", "Bash"}.isdisjoint(fast)
    ultracode = session_denied_tools("fast-ultracode")
    assert {"Agent", "Workflow", "SendMessage"}.isdisjoint(ultracode)
    assert {"RemoteTrigger", "CronCreate"}.issubset(ultracode)


def test_a_session_that_is_not_pure_checks_fast_output_against_its_own_model(
    tmp_path: Path,
) -> None:
    profile = LeanProfile(LocalWorkspace(tmp_path), tuple)
    _, path = profile.files(model="claude-opus-5-5", variant="fast-low")
    settings = json.loads(Path(path).read_text(encoding="utf-8"))
    assert settings["fastMode"] is True
    assert settings["effortLevel"] == "low"
    assert "model" not in settings and "env" not in settings
    with pytest.raises(DomainFailure, match="supported Opus model"):
        profile.files(model="claude-sonnet-5", variant="fast-low")
