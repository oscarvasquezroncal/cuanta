from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.application.session_profile import record_hook_event
from cuanta.bootstrap import Container
from cuanta.cli import hooks
from cuanta.domain.ledger import LedgerEvent, Run
from cuanta.domain.read_discipline import POST_CONTEXT
from cuanta.ports.ledger import EventQuery


def _specific(output: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], output["hookSpecificOutput"])


@pytest.mark.parametrize("agent", [{}, {"agent_id": "child", "agent_type": "tester"}])
def test_main_and_subagent_large_read_denials_and_post_context_are_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: dict[str, object]
) -> None:
    monkeypatch.delenv("CUANTA_RUN_ID", raising=False)
    source = tmp_path / "large.ts"
    source.write_text("private source line\n" * 401, encoding="utf-8")
    payload: dict[str, object] = {
        "cwd": str(tmp_path),
        "tool_name": "Read",
        "tool_input": {"file_path": str(source)},
        **agent,
    }
    result = _specific(hooks.hook_output(payload, tmp_path, "pre"))
    assert result["permissionDecision"] == "deny"
    assert "private source" not in json.dumps(result)
    post = _specific(hooks.hook_output(payload, tmp_path, "post"))
    assert post == {"hookEventName": "PostToolUse", "additionalContext": POST_CONTEXT}
    assert not (tmp_path / ".cuanta").exists()


@pytest.mark.parametrize("path", ["../outside.ts", "nested/../../outside.ts"])
def test_hook_rejects_paths_outside_current_copy(tmp_path: Path, path: str) -> None:
    payload = {"tool_name": "Read", "tool_input": {"file_path": path}}
    assert _specific(hooks.hook_output(payload, tmp_path, "pre"))["permissionDecision"] == "deny"


def test_hook_does_not_traverse_file_or_directory_links(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"outside-{tmp_path.name}.ts"
    outside.write_text("external private source\n" * 500, encoding="utf-8")
    (tmp_path / "linked.ts").symlink_to(outside)
    result = hooks.hook_output(
        {"tool_name": "Read", "tool_input": {"file_path": "linked.ts"}}, tmp_path, "pre"
    )
    assert (
        _specific(result)["permissionDecisionReason"] == "Invalid or uncontained hook tool input."
    )
    assert outside.read_text(encoding="utf-8").startswith("external private source")


def test_hook_rejects_link_plus_parent_traversal_before_path_normalization(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    (outside / "child").mkdir(parents=True)
    (outside / "private.ts").write_text("external private source", encoding="utf-8")
    (tmp_path / "private.ts").write_text("decoy", encoding="utf-8")
    (tmp_path / "linked").symlink_to(outside / "child", target_is_directory=True)
    result = hooks.hook_output(
        {"tool_name": "Read", "tool_input": {"file_path": "linked/../private.ts"}},
        tmp_path,
        "pre",
    )
    assert _specific(result)["permissionDecision"] == "deny"
    assert (outside / "private.ts").read_text(encoding="utf-8") == "external private source"


def test_absolute_root_grep_and_tool_rewrite_keep_protocol_fields(tmp_path: Path) -> None:
    denied = hooks.hook_output(
        {"tool_name": "Grep", "tool_input": {"path": str(tmp_path), "output_mode": "content"}},
        tmp_path,
        "pre",
    )
    assert _specific(denied)["permissionDecision"] == "deny"
    redirected = hooks.hook_output(
        {"tool_name": "Bash", "tool_input": {"command": "pytest -q", "timeout": 40}},
        tmp_path,
        "pre",
    )
    assert _specific(redirected)["updatedInput"] == {"command": "cuanta test", "timeout": 40}


@pytest.mark.parametrize(
    "payload", [None, [], {"tool_name": 3}, {"tool_name": "Read", "tool_input": []}]
)
def test_malformed_hook_input_fails_closed_without_source_output(
    tmp_path: Path, payload: object
) -> None:
    assert _specific(hooks.hook_output(payload, tmp_path, "pre"))["permissionDecision"] == "deny"
    assert hooks.hook_output(payload, tmp_path, "post") == {}


def test_hook_event_deduplicates_by_run_session_tool_id_and_records_no_source() -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("RUN", "mandate"))
    event = LedgerEvent(
        run_id="RUN",
        session_id="session",
        source="cuanta_hook",
        kind="read_discipline",
        tool_use_id="tool1",
        raw='{"avoided_tokens_estimate":2000}',
    )
    assert record_hook_event(ledger, event) == 1
    assert record_hook_event(ledger, event) == 0
    assert record_hook_event(ledger, replace(event, session_id="other")) == 1
    assert record_hook_event(ledger, replace(event, tool_use_id="tool2")) == 1
    assert record_hook_event(ledger, replace(event, run_id="missing")) == 0
    assert record_hook_event(ledger, replace(event, session_id="")) == 0


def test_hook_logs_only_metadata_to_the_delegated_run_ledger_and_deduplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copy = tmp_path / "copy"
    state = tmp_path / "state"
    copy.mkdir()
    state.mkdir()
    monkeypatch.setenv("CUANTA_STATE_ROOT", str(state))
    monkeypatch.setenv("CUANTA_RUN_ID", "RUN")
    source = copy / "large.ts"
    source.write_text("secret source text\n" * 401, encoding="utf-8")
    container = Container.for_project(copy)
    try:
        ledger = container.ledger()
        ledger.add_run(Run("RUN", "mandate"))
        payload: dict[str, object] = {
            "tool_name": "Read",
            "tool_input": {"file_path": "large.ts"},
            "session_id": "session",
            "tool_use_id": "tool1",
            "agent_type": "tester",
        }
        hooks.hook_output(payload, copy, "pre")
        hooks.hook_output(payload, copy, "pre")
        hooks.hook_output(payload, copy, "post")
        events = ledger.events(EventQuery(run_id="RUN"))
        assert len(events) == 1
        event = events[0]
        assert event.source == "cuanta_hook" and event.agent == "tester"
        assert event.file_path == "large.ts" and event.command == ""
        assert event.total_tokens == 0 and event.cost_usd is None
        assert json.loads(event.raw) == {"avoided_tokens_estimate": 1905}
        assert "secret source" not in str(event)
        assert (state / ".cuanta/ledger.db").is_file()
        assert not (copy / ".cuanta").exists()
    finally:
        container.close()


def test_module_main_reads_json_and_emits_only_hook_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CUANTA_RUN_ID", raising=False)
    monkeypatch.setattr("sys.argv", ["hooks", "pre"])
    monkeypatch.setattr(
        "sys.stdin", io.StringIO('{"tool_name":"Bash","tool_input":{"command":"pytest -q"}}')
    )
    assert hooks.main() == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out)["hookSpecificOutput"]["updatedInput"]["command"] == "cuanta test"
