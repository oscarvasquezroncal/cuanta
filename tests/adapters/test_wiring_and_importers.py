from __future__ import annotations

import json
from pathlib import Path

import tomlkit

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.telemetry.config_editors import (
    Backups,
    ClaudeSettingsWiring,
    CodexConfigWiring,
)
from cuanta.adapters.telemetry.transcript_importers import (
    CLAUDE_SOURCE,
    claude_project_dir,
    import_claude,
    import_codex,
)
from cuanta.application.telemetry import TranscriptImport
from cuanta.domain.telemetry import WiringState
from cuanta.ports.ledger import EventQuery


def test_claude_settings_on_off_restores_original(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.local.json"
    settings.parent.mkdir()
    original = {"permissions": {"allow": ["Read"]}, "env": {"FOO": "bar"}}
    settings.write_text(json.dumps(original), encoding="utf-8")
    wiring = ClaudeSettingsWiring(tmp_path, Backups(tmp_path / ".cuanta" / "backups"))
    report = wiring.enable(4318, "shop")
    assert report.state is WiringState.ON
    written = json.loads(settings.read_text(encoding="utf-8"))
    assert written["env"]["FOO"] == "bar"
    assert written["env"]["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://127.0.0.1:4318"
    assert written["permissions"] == original["permissions"]
    assert wiring.status(4318).state is WiringState.ON
    assert wiring.status(5000).state is WiringState.OTHER
    wiring.enable(4318, "shop")
    wiring.disable()
    assert json.loads(settings.read_text(encoding="utf-8")) == original
    assert wiring.status(4318).state is WiringState.OFF


def test_claude_settings_created_then_removed(tmp_path: Path) -> None:
    wiring = ClaudeSettingsWiring(tmp_path, Backups(tmp_path / "b"))
    wiring.enable(4318, "x")
    assert (tmp_path / ".claude" / "settings.local.json").exists()
    wiring.disable()
    assert not (tmp_path / ".claude" / "settings.local.json").exists()


def test_codex_config_preserves_formatting_and_restores(tmp_path: Path) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    original = (
        '# my codex\nmodel = "gpt-5-codex"  # pinned\n\n[mcp_servers.docs]\ncommand = "docs"\n'
    )
    config.write_text(original, encoding="utf-8")
    (tmp_path / ".codex" / "auth.json").write_text("{}", encoding="utf-8")
    wiring = CodexConfigWiring(tmp_path, Backups(tmp_path / "b"), installed=True)
    assert wiring.enable(4318, "x").state is WiringState.ON
    text = config.read_text(encoding="utf-8")
    assert text.startswith('# my codex\nmodel = "gpt-5-codex"  # pinned')
    otel = tomlkit.parse(text)["otel"]
    assert otel["log_user_prompt"] is False
    assert otel["exporter"]["otlp-http"]["protocol"] == "json"
    assert otel["metrics_exporter"]["otlp-http"]["endpoint"] == "http://127.0.0.1:4318/v1/metrics"
    assert wiring.status(4318).state is WiringState.ON
    wiring.disable()
    assert config.read_text(encoding="utf-8") == original
    assert (tmp_path / ".codex" / "auth.json").read_text(encoding="utf-8") == "{}"


def test_codex_not_installed_is_unavailable(tmp_path: Path) -> None:
    wiring = CodexConfigWiring(tmp_path, Backups(tmp_path / "b"), installed=False)
    assert wiring.enable(4318, "x").state is WiringState.UNAVAILABLE
    assert wiring.status(4318).state is WiringState.UNAVAILABLE


def _claude_transcript(home: Path, project: Path) -> Path:
    folder = claude_project_dir(home, project)
    (folder / "s1" / "subagents").mkdir(parents=True)
    usage = {
        "input_tokens": 5,
        "output_tokens": 7,
        "cache_read_input_tokens": 100,
        "cache_creation_input_tokens": 20,
    }
    lines = [
        {
            "type": "user",
            "sessionId": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "message": {"role": "user", "content": "hi"},
        },
        {
            "type": "assistant",
            "sessionId": "s1",
            "timestamp": "2026-01-01T00:00:01Z",
            "message": {
                "id": "m1",
                "model": "claude-x",
                "usage": usage,
                "content": [{"type": "thinking"}],
            },
        },
        {
            "type": "assistant",
            "sessionId": "s1",
            "timestamp": "2026-01-01T00:00:01Z",
            "message": {
                "id": "m1",
                "model": "claude-x",
                "usage": usage,
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Read",
                        "input": {"file_path": "src/a.py"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "sessionId": "s1",
            "timestamp": "2026-01-01T00:00:02Z",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x" * 400}]
            },
        },
        {
            "type": "system",
            "subtype": "compact_boundary",
            "sessionId": "s1",
            "timestamp": "2026-01-01T00:00:03Z",
        },
        "not an object",
    ]
    main = folder / "s1.jsonl"
    main.write_text("\n".join(json.dumps(line) for line in lines) + "\n{broken\n", encoding="utf-8")
    sub = [
        {
            "type": "assistant",
            "isSidechain": True,
            "sessionId": "s1",
            "timestamp": "2026-01-01T00:00:05Z",
            "message": {"id": "m2", "model": "claude-y", "usage": usage, "content": []},
        }
    ]
    (folder / "s1" / "subagents" / "agent-tester.jsonl").write_text(
        json.dumps(sub[0]) + "\n", encoding="utf-8"
    )
    return folder


def test_claude_importer_dedupes_and_attributes(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _claude_transcript(tmp_path, project)
    events, counts = import_claude(tmp_path, project, {})
    api = [event for event in events if event.kind == "api_request"]
    assert len(api) == 2
    assert api[0].cache_read_tokens == 100
    assert {event.agent for event in api} == {"main", "subagent teste…"}
    result = next(event for event in events if event.kind == "tool_result")
    assert (result.tool_name, result.file_path, result.tool_result_bytes) == (
        "Read",
        "src/a.py",
        400,
    )
    assert not any(event.kind == "tool_use" for event in events)
    assert any(event.kind == "compaction" for event in events)
    assert counts.files == 2
    assert counts.skipped >= 2


def test_subagent_names_come_from_meta_then_parent(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    folder = _claude_transcript(tmp_path, project)
    subagents = folder / "s1" / "subagents"
    (subagents / "agent-tester.meta.json").write_text('{"agentType": "tester"}', encoding="utf-8")
    events, _ = import_claude(tmp_path, project, {})
    assert {event.agent for event in events if event.kind == "api_request"} == {"main", "tester"}
    (subagents / "agent-tester.meta.json").unlink()
    parent = [
        {
            "type": "assistant",
            "sessionId": "s1",
            "timestamp": "2026-01-01T00:00:06Z",
            "message": {
                "id": "m9",
                "model": "claude-x",
                "usage": {},
                "content": [
                    {
                        "type": "tool_use",
                        "id": "spawn",
                        "name": "Agent",
                        "input": {"subagent_type": "python-senior"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "sessionId": "s1",
            "timestamp": "2026-01-01T00:00:09Z",
            "toolUseResult": {"agentId": "tester"},
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "spawn", "content": "done"}]
            },
        },
    ]
    (folder / "s2.jsonl").write_text(
        "\n".join(json.dumps(line) for line in parent), encoding="utf-8"
    )
    events, _ = import_claude(tmp_path, project, {})
    agents = {event.agent for event in events if event.kind == "api_request"}
    assert "python-senior" in agents
    spawn = next(event for event in events if event.tool_name == "Agent")
    assert spawn.kind == "tool_result"
    assert json.loads(spawn.raw) == {"spawned_agent": "python-senior"}


def test_import_is_idempotent_through_watermarks(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _claude_transcript(tmp_path, project)
    ledger = MemoryLedger()
    service = TranscriptImport(
        ledger, [(CLAUDE_SOURCE, lambda since: import_claude(tmp_path, project, since))]
    )
    service.run()
    first = len(ledger.events(EventQuery()))
    assert first > 0
    service.run()
    assert len(ledger.events(EventQuery())) == first


def test_codex_importer_filters_cwd_and_dedupes(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    folder = tmp_path / ".codex" / "sessions" / "2026" / "01" / "01"
    folder.mkdir(parents=True)
    usage = {
        "input_tokens": 1000,
        "cached_input_tokens": 600,
        "output_tokens": 50,
        "reasoning_output_tokens": 10,
        "total_tokens": 1050,
    }
    lines = [
        {"timestamp": "t0", "type": "session_meta", "payload": {"id": "sess", "cwd": str(project)}},
        {"timestamp": "t1", "type": "turn_context", "payload": {"model": "gpt-5-codex"}},
        {
            "timestamp": "t2",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {"total_token_usage": usage, "last_token_usage": usage},
            },
        },
        {
            "timestamp": "t3",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {"total_token_usage": usage, "last_token_usage": usage},
            },
        },
        {"timestamp": "t4", "type": "compacted", "payload": {}},
    ]
    (folder / "rollout-a.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines), encoding="utf-8"
    )
    other = [
        {"timestamp": "t0", "type": "session_meta", "payload": {"id": "x", "cwd": "/elsewhere"}},
        lines[2],
    ]
    (folder / "rollout-b.jsonl").write_text(
        "\n".join(json.dumps(line) for line in other), encoding="utf-8"
    )
    events, counts = import_codex(tmp_path, project, {})
    api = [event for event in events if event.kind == "api_request"]
    assert len(api) == 1
    assert (api[0].input_tokens, api[0].cache_read_tokens, api[0].reasoning_tokens) == (
        400,
        600,
        10,
    )
    assert api[0].model == "gpt-5-codex"
    assert counts.files == 2


def test_rebuild_replaces_stale_imported_events(tmp_path: Path) -> None:
    from dataclasses import replace

    from cuanta.domain.ledger import LedgerEvent

    project = tmp_path / "repo"
    _claude_transcript(tmp_path, project)
    ledger = MemoryLedger()
    stale = LedgerEvent(
        source=CLAUDE_SOURCE, session_id="s1", kind="tool_result", tool_name="tool", ts="2026"
    )
    ledger.add_events([stale, replace(stale, agent="a3071f2c9d")])
    ledger.add_events([LedgerEvent(source="claude_code", kind="api_request", ts="2026")])
    service = TranscriptImport(
        ledger, [(CLAUDE_SOURCE, lambda since: import_claude(tmp_path, project, since))]
    )
    service.run(rebuild=True)
    assert service.removed == {CLAUDE_SOURCE: 2}
    events = ledger.events(EventQuery())
    imported = [event for event in events if event.source == CLAUDE_SOURCE]
    assert imported
    assert all(event.tool_name != "tool" for event in imported)
    assert all(event.agent != "a3071f2c9d" for event in imported)
    assert [event.source for event in events].count("claude_code") == 1
    first = len(imported)
    service.run(rebuild=True)
    again = [event for event in ledger.events(EventQuery()) if event.source == CLAUDE_SOURCE]
    assert len(again) == first
    assert len({event.id for event in ledger.events(EventQuery())}) == len(
        ledger.events(EventQuery())
    )


def test_sqlite_delete_events_by_source(tmp_path: Path) -> None:
    from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
    from cuanta.domain.ledger import LedgerEvent

    ledger = SqliteLedger(tmp_path / "ledger.db")
    try:
        ledger.add_events(
            [
                LedgerEvent(source=CLAUDE_SOURCE, kind="api_request"),
                LedgerEvent(source=CLAUDE_SOURCE, kind="tool_result"),
                LedgerEvent(source="claude_code", kind="api_request"),
            ]
        )
        assert ledger.delete_events(CLAUDE_SOURCE) == 2
        assert ledger.delete_events(CLAUDE_SOURCE) == 0
        assert [event.source for event in ledger.events(EventQuery())] == ["claude_code"]
    finally:
        ledger.close()
