from __future__ import annotations

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.application.doctor import session_check, user_forge_check
from cuanta.domain.ledger import Run
from cuanta.domain.plugins import InstalledPlugin
from cuanta.domain.progress import Status
from tests.tui.fakes import DETECTION
from tests.unit.test_overhead import user_run


def test_an_older_user_forge_is_flagged_with_the_disable_command() -> None:
    plugins = (
        InstalledPlugin("claude-agent-forge@claude-agent-forge", "0.3.1", "user"),
        InstalledPlugin("caveman@caveman", "63e797cd753b", "user"),
    )
    found = user_forge_check(lambda: plugins, lambda: "0.4.0")(DETECTION)
    assert len(found) == 1
    assert found[0].status is Status.WARN
    assert found[0].fix == "claude plugin disable claude-agent-forge@claude-agent-forge"
    assert "0.3.1" in found[0].detail
    assert user_forge_check(lambda: (), lambda: "0.4.0")(DETECTION) == []


def test_failed_mcp_servers_and_startup_time_come_from_the_last_run() -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("01M39WA3N3P9RNEDQX35DXTDJH", "mandate", "claude", status="ok"))
    ledger.add_events(user_run())
    found = session_check(lambda: ledger, lambda: True)(DETECTION)
    names = [item.name for item in found]
    assert names == ["mcp magic", "session startup"]
    assert "not authenticated" in found[0].detail
    assert "3.2 s" in found[0].detail
    assert found[1].status is Status.OK
    assert "5 plugins, 6 MCP servers, 4 hooks" in found[1].detail
    assert session_check(lambda: ledger, lambda: False)(DETECTION) == []
