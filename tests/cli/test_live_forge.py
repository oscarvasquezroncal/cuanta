from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.fakes import copy_repo
from tests.support import invoke

LIVE_BUDGET_USD = os.environ.get("CUANTA_LIVE_BUDGET_USD", "5")


@pytest.mark.live
def test_live_init_allows_skill(tmp_path: Path) -> None:
    root = copy_repo("python_strong", tmp_path)
    result = invoke(
        ["init", str(root), "--yes", "--json"],
        env={"CUANTA_BUDGET_USD": LIVE_BUDGET_USD, "CUANTA_PORT": "47600"},
    )
    out = os.environ.get("CUANTA_LIVE_OUT")
    if out:
        Path(out).write_text(result.stdout + "\n---stderr---\n" + result.stderr, encoding="utf-8")
    document = json.loads(result.stdout)
    stages = {stage["stage"]: stage for stage in document["stages"]}
    assert stages["forge"]["status"] in {"ok", "fail"}, stages
    assert "Skill" not in document["denials"], document["registration"]
    assert not document["registration"].startswith("deferred")
    failing = [item["text"] for item in document["verify"] if item["status"] == "fail"]
    assert failing == []
    agents = sorted(path.name for path in (root / ".claude" / "agents").glob("*.md"))
    assert len(agents) == 4, agents
    state = json.loads((root / ".claude" / "forge-state.json").read_text(encoding="utf-8"))
    assert "6" in state["phases_completed"], state["phases_completed"]
    assert document["ok"] is True
