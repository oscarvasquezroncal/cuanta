from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

from cuanta.domain.ledger import LedgerEvent

FIXTURE = Path(__file__).parents[1] / "fixtures" / "telemetry" / "real_investigation.json"
RAW_KEYS = frozenset({"attributes", "attributed", "cuanta.parameters", "value"})
PARAMETER_KEYS = frozenset(
    {"subagent_type", "spawned_agent", "agent_type", "file_path", "path", "notebook_path"}
)


def test_real_export_keeps_events_usage_and_relative_subagent_timing() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    events = tuple(LedgerEvent(**item) for item in data["events"])
    assert data["provenance"]["source_run_id"] == "01M3ASK1YZAAD65TTMV0VVPSGZ"
    assert len(events) == 127
    counts = Counter(event.kind for event in events)
    assert counts["api_request"] == 12
    assert counts["tool_decision"] == counts["tool_result"] == 32
    assert counts["subagent_completed"] == 1
    assert sum(
        event.cost_usd or 0 for event in events if event.kind == "api_request"
    ) == pytest.approx(0.54546)
    timestamps = [datetime.fromisoformat(event.ts.replace("Z", "+00:00")) for event in events]
    assert timestamps[0].isoformat() == "2026-01-01T00:00:00+00:00"
    assert (timestamps[-1] - timestamps[0]).total_seconds() == pytest.approx(129.163)
    start = next(
        event for event in events if event.kind == "tool_decision" and event.tool_name == "Agent"
    )
    finish = next(event for event in events if event.kind == "subagent_completed")
    tools = [
        event for event in events if event.kind == "tool_result" and start.ts < event.ts < finish.ts
    ]
    assert Counter(event.tool_name for event in tools) == {
        "Read": 25,
        "Glob": 2,
        "Grep": 3,
        "Bash": 1,
    }
    assert sum(event.tool_result_bytes for event in tools) == 56_725


def test_real_export_contains_only_scrubbed_paths_and_allowlisted_metadata() -> None:
    text = FIXTURE.read_text(encoding="utf-8")
    assert not re.search(r"[A-Za-z]:[\\/]", text)
    for item in json.loads(text)["events"]:
        assert item["command"] == ""
        raw = json.loads(item["raw"])
        assert raw.keys() <= RAW_KEYS
        parameters = raw.get("cuanta.parameters", {})
        assert parameters.keys() <= PARAMETER_KEYS
        paths = [
            item["file_path"],
            *(parameters.get(key, "") for key in ("file_path", "path", "notebook_path")),
        ]
        for path in paths:
            if path:
                parts = path.strip("/").split("/")
                assert all(re.fullmatch(r"dir-\d{3}", part) for part in parts[:-1])
                assert re.fullmatch(r"file-\d{3}(?:\.[\w-]+)?", parts[-1])
