from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.workspace import LocalHome
from cuanta.application.doctor import agents_md_check, session_check, user_forge_check
from cuanta.application.recent_runs import latest_with_requests
from cuanta.domain.ledger import LedgerEvent, Run
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


def test_home_agents_md_share_comes_from_the_latest_first_request(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_bytes(b"x" * 230)
    ledger = MemoryLedger()
    ledger.add_run(Run("R1", "mandate", "claude"))
    ledger.add_run(Run("R2", "mandate", "codex"))
    ledger.add_events([replace(event, run_id="R1") for event in user_run()])
    ledger.add_events(
        [
            LedgerEvent(
                run_id="R2",
                kind="api_request",
                ts="2026-09-25",
                input_tokens=50,
                cache_read_tokens=50,
            )
        ]
    )
    latest = latest_with_requests(ledger, 20)
    assert latest is not None and latest[0].id == "R2"
    claude = latest_with_requests(ledger, 20, "claude")
    assert claude is not None and claude[0].id == "R1"
    found = agents_md_check(LocalHome(tmp_path), lambda: ledger, lambda: True)(DETECTION)
    assert len(found) == 1
    assert found[0].name == "agents-md"
    assert found[0].status is Status.INFO
    assert "~/AGENTS.md: 230 bytes" in found[0].detail
    assert "0.1%" in found[0].detail
    assert "estimate" in found[0].detail


def test_latest_claude_request_is_not_hidden_by_newer_codex_runs(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_bytes(b"x" * 230)
    ledger = MemoryLedger()
    ledger.add_run(Run("A", "mandate", "claude"))
    ledger.add_events([replace(event, run_id="A") for event in user_run()])
    for index in range(25):
        ledger.add_run(Run(f"Z{index:02d}", "mandate", "codex"))
    latest = latest_with_requests(ledger, 20, "claude")
    assert latest is not None and latest[0].id == "A"
    unlimited = latest_with_requests(ledger, 0, "claude")
    assert unlimited is not None and unlimited[0].id == "A"
    found = agents_md_check(LocalHome(tmp_path), lambda: ledger, lambda: True)(DETECTION)
    assert "0.1%" in found[0].detail


def test_home_agents_md_fallback_path_and_unavailable_context(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "AGENTS.md").write_bytes(b"y" * 230)

    def forbidden() -> MemoryLedger:
        raise AssertionError("ledger should not be opened")

    check = agents_md_check(LocalHome(tmp_path), forbidden, lambda: False)
    found = check(DETECTION)
    assert found[0].status is Status.INFO
    assert "~/.claude/AGENTS.md: 230 bytes" in found[0].detail
    assert "unavailable" in found[0].detail
    (tmp_path / "AGENTS.md").write_bytes(b"x" * 120)
    assert "~/AGENTS.md: 120 bytes" in check(DETECTION)[0].detail


def test_absent_home_agents_md_reads_absent(tmp_path: Path) -> None:
    found = agents_md_check(LocalHome(tmp_path), MemoryLedger, lambda: False)(DETECTION)
    assert found[0].status is Status.INFO
    assert found[0].detail == "absent"


def test_local_home_size_bytes_counts_raw_file_bytes(tmp_path: Path) -> None:
    home = LocalHome(tmp_path)
    (tmp_path / "AGENTS.md").write_bytes(b"a\r\nb")
    (tmp_path / "folder").mkdir()
    assert home.size_bytes("AGENTS.md") == 4
    assert home.size_bytes("missing.md") is None
    assert home.size_bytes("folder") is None
