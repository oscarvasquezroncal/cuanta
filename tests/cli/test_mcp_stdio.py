from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cuanta.cli.app import app
from tests.fakes import FakeRunner


def _messages(all_tools: bool = False) -> str:
    messages: list[dict[str, object]] = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "stdio-test", "version": "1.0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "page", "arguments": {"path": "cart.py", "lines": "1:2"}},
        },
        {"jsonrpc": "2.0", "id": 4, "method": "ping"},
    ]
    if all_tools:
        tools: tuple[tuple[str, dict[str, object]], ...] = (
            ("find", {"query": "checkout", "k": 5}),
            ("card", {"path": "cart.py"}),
            ("impact", {"path": "cart.py"}),
            ("impact", {"symbol": "checkout"}),
            ("facts", {"path": "cart.py"}),
            ("tests_for", {"path": "cart.py"}),
            (
                "note",
                {
                    "path": "cart.py",
                    "text": "Checkout returns the local value.",
                    "anchor": "checkout",
                },
            ),
            ("facts", {"path": "cart.py"}),
            ("page", {"path": "cart.py", "symbol": "checkout"}),
        )
        messages.extend(
            {
                "jsonrpc": "2.0",
                "id": identity,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
            for identity, (name, arguments) in enumerate(tools, 5)
        )
    return "\n".join(json.dumps(message) for message in messages) + "\n"


@pytest.mark.parametrize("output_flag", ["--plain", "--json"])
def test_mcp_stdio_bypasses_presenter_and_never_launches_engine(
    tmp_path: Path, fake_runner: FakeRunner, output_flag: str
) -> None:
    source = "def checkout():\n    return 1\n"
    (tmp_path / "cart.py").write_text(source, encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [output_flag, "--project", str(tmp_path), "mcp", "serve"],
        input=_messages(),
    )
    assert result.exit_code == 0, result.output
    messages = [json.loads(line) for line in result.stdout.splitlines()]
    assert [message["id"] for message in messages] == [1, 2, 3, 4]
    assert messages[0]["result"]["protocolVersion"] == "2025-06-18"
    assert {tool["name"] for tool in messages[1]["result"]["tools"]} == {
        "find",
        "card",
        "impact",
        "facts",
        "page",
        "tests_for",
        "note",
    }
    assert "checkout" in messages[2]["result"]["structuredContent"]["text"]
    assert messages[3]["result"] == {}
    assert result.stderr == "" and not fake_runner.calls
    assert (tmp_path / "cart.py").read_text(encoding="utf-8") == source
    assert not (tmp_path / ".cuanta" / "ledger.db").exists()


def test_mcp_real_pipe_is_utf8_newline_json_only_and_exits_at_eof(tmp_path: Path) -> None:
    source = "def checkout():\n    return 'España'\n"
    test_source = "from cart import checkout\n\ndef test_checkout():\n    assert checkout()\n"
    (tmp_path / "cart.py").write_text(source, encoding="utf-8")
    (tmp_path / "test_cart.py").write_text(test_source, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "cuanta", "--project", str(tmp_path), "mcp", "serve"],
        input=_messages(all_tools=True),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.endswith("\n") and completed.stderr == ""
    messages = [json.loads(line) for line in completed.stdout.splitlines()]
    assert [message["id"] for message in messages] == list(range(1, 14))
    results = {message["id"]: message["result"] for message in messages}
    assert all(
        not result["isError"] for identity, result in results.items() if identity not in (1, 2, 4)
    )
    assert "España" in results[3]["structuredContent"]["text"]
    assert any(hit["path"] == "cart.py" for hit in results[5]["structuredContent"]["hits"])
    assert results[6]["structuredContent"]["card"]["path"] == "cart.py"
    assert results[7]["structuredContent"] == results[8]["structuredContent"]
    assert any(
        row["path"] == "test_cart.py" for row in results[7]["structuredContent"]["neighbours"]
    )
    assert results[9]["structuredContent"]["facts"] == []
    assert any(row["path"] == "test_cart.py" for row in results[10]["structuredContent"]["tests"])
    note = results[11]["structuredContent"]["note"]
    assert note["line"] == 1 and note["end_line"] == 2 and not note["stale"]
    assert results[12]["structuredContent"]["facts"] == [note]
    assert results[13]["structuredContent"]["text"] == results[3]["structuredContent"]["text"]
    assert (tmp_path / "cart.py").read_text(encoding="utf-8") == source
    assert (tmp_path / "test_cart.py").read_text(encoding="utf-8") == test_source
    assert not (tmp_path / ".cuanta" / "ledger.db").exists()


def test_mcp_cli_setup_failure_uses_only_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cuanta.bootstrap import Container

    def unavailable(self: Container, run_id: str) -> object:
        raise OSError("private local detail")

    monkeypatch.setattr(Container, "mcp_server", unavailable)
    result = CliRunner().invoke(app, ["--project", str(tmp_path), "mcp", "serve"])
    assert result.exit_code == 1 and result.stdout == ""
    assert result.stderr == "cuanta MCP server failed\n"
