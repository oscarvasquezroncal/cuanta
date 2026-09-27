from __future__ import annotations

import pytest

from cuanta.domain.read_discipline import decide_read_discipline


@pytest.mark.parametrize(
    "window",
    [{}, {"offset": 1}, {"limit": 100}, {"offset": True, "limit": 100}, {"offset": 1, "limit": 0}],
)
def test_large_reads_need_a_positive_explicit_window(window: dict[str, object]) -> None:
    decision = decide_read_discipline("Read", {"file_path": "large.ts", **window}, 401, 8000)
    assert decision.permission == "deny"
    assert decision.avoided_tokens == 2000
    assert "offset" in decision.reason and "page" in decision.reason


def test_small_and_explicit_window_reads_keep_normal_permissions() -> None:
    assert decide_read_discipline("Read", {}, 400).permission == ""
    assert decide_read_discipline("Read", {"offset": 1, "limit": 100}, 1000).permission == ""


@pytest.mark.parametrize("scope", [{"path": "src"}, {"glob": "*.tsx"}, {"type": "py"}])
def test_scoped_content_searches_are_not_blocked(scope: dict[str, object]) -> None:
    assert decide_read_discipline("Grep", {"output_mode": "content", **scope}).permission == ""


@pytest.mark.parametrize("glob", ["", "*", "**", "**/*"])
def test_unscoped_root_content_search_is_denied(glob: str) -> None:
    assert (
        decide_read_discipline("Grep", {"output_mode": "content", "glob": glob}).permission
        == "deny"
    )
    assert decide_read_discipline("Grep", {"output_mode": "files_with_matches"}).permission == ""


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q tests/unit",
        "uv run python -m pytest",
        "npm test",
        "npm run test:unit",
        "npx vitest run",
        "go test ./...",
        "cargo test",
    ],
)
def test_simple_raw_tests_redirect_to_a_valid_gate_and_preserve_tool_fields(command: str) -> None:
    inputs: dict[str, object] = {"command": command, "timeout": 5000, "description": "verify"}
    decision = decide_read_discipline("Bash", inputs)
    assert decision.permission == "allow"
    assert decision.updated_input == {
        "command": "cuanta test",
        "timeout": 5000,
        "description": "verify",
    }
    assert inputs["command"] == command


@pytest.mark.parametrize(
    "command",
    [
        "cd src && pytest -q",
        "pytest; echo done",
        "npm test | head",
        "pytest > test.log",
        "pytest $(pwd)",
        "pytest\necho done",
        "(pytest -q)",
    ],
)
def test_compound_raw_tests_are_denied_without_dropping_other_actions(command: str) -> None:
    decision = decide_read_discipline("Bash", {"command": command})
    assert decision.permission == "deny"
    assert decision.updated_input is None


def test_other_shell_commands_and_the_gateway_keep_normal_permissions() -> None:
    for command in ("cuanta test --affected", "npm run build", "git status", "echo hello"):
        assert decide_read_discipline("Bash", {"command": command}).permission == ""
