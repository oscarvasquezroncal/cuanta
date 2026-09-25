from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from cuanta.adapters.engines.claude_code import build_command
from cuanta.adapters.engines.claude_plugins import installed_plugins
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.engine_run import EngineLauncher, LaunchSpec
from cuanta.application.session_profile import LeanProfile
from cuanta.domain.engine import EngineEvent, EngineOutcome, EngineRequest
from cuanta.domain.plugins import (
    InstalledPlugin,
    lean_settings,
    older_forge,
    parse_installed,
    version_tuple,
)

PLUGIN_KEYS = ("claude-mem@thedotmack", "caveman@caveman", "claude-agent-forge@claude-agent-forge")
VERSIONS = ("12.1.0", "63e797cd753b", "0.3.1")
INSTALLED = {
    "version": 2,
    "plugins": {
        key: [{"scope": "user", "version": version}]
        for key, version in zip(PLUGIN_KEYS, VERSIONS, strict=True)
    },
}


class NamedEngine:
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def available(self) -> bool:
        return True

    def version(self) -> str:
        return "1"

    def missing_flags(self) -> tuple[str, ...]:
        return ()

    def cancel(self) -> None:
        return None

    def command(self, request: EngineRequest) -> list[str]:
        return build_command((self._name,), request)

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        return EngineOutcome(0, None, 0)


def launcher(engine: str, lean: Callable[[], tuple[str, str]], session: str) -> EngineLauncher:
    return EngineLauncher(
        NamedEngine(engine),
        MemoryLedger(),
        FixedClock(),
        lambda: "RUN",
        lambda size: b"\x01" * size,
        "shop",
        4318,
        None,
        lean_files=lean,
        default_session=session,
    )


def test_installed_plugins_are_read_from_the_user_home(tmp_path: Path) -> None:
    folder = tmp_path / ".claude" / "plugins"
    folder.mkdir(parents=True)
    (folder / "installed_plugins.json").write_text(json.dumps(INSTALLED), encoding="utf-8")
    plugins = installed_plugins(tmp_path)
    assert {plugin.key for plugin in plugins} == set(PLUGIN_KEYS)
    assert installed_plugins(tmp_path / "nowhere") == ()
    assert parse_installed("not json") == ()


def test_lean_profile_disables_hooks_every_plugin_and_every_mcp_server(tmp_path: Path) -> None:
    plugins = parse_installed(json.dumps(INSTALLED))
    mcp, settings = LeanProfile(LocalWorkspace(tmp_path), lambda: plugins).files()
    assert json.loads(Path(mcp).read_text(encoding="utf-8")) == {"mcpServers": {}}
    written = json.loads(Path(settings).read_text(encoding="utf-8"))
    assert written["disableAllHooks"] is True
    assert written["enabledPlugins"] == dict.fromkeys((*PLUGIN_KEYS, "agents-md@builtin"), False)
    assert lean_settings(()) == {
        "disableAllHooks": True,
        "enabledPlugins": {"agents-md@builtin": False},
    }


def test_a_lean_claude_command_is_strict_about_mcp_and_carries_the_settings() -> None:
    request = EngineRequest(
        prompt="p", cwd=".", env={}, mcp_config="/p/lean-mcp.json", settings_file="/p/s.json"
    )
    command = build_command(("claude",), request)
    assert command[-5:] == [
        "--strict-mcp-config",
        "--mcp-config",
        "/p/lean-mcp.json",
        "--settings",
        "/p/s.json",
    ]


def test_the_launcher_applies_the_session_profile_to_claude_only() -> None:
    files = ("/p/mcp.json", "/p/settings.json")
    spec = LaunchSpec(kind="mandate", prompt="p", cwd=".", allowed_tools=())
    lean = launcher("claude", lambda: files, "lean").request(spec, "RUN", "00-x", None)
    assert (lean.mcp_config, lean.settings_file) == files
    full = launcher("claude", lambda: files, "full").request(spec, "RUN", "00-x", None)
    assert (full.mcp_config, full.settings_file) == ("", "")
    forced = launcher("claude", lambda: files, "full").request(
        LaunchSpec(kind="mandate", prompt="p", cwd=".", allowed_tools=(), session="lean"),
        "RUN",
        "00-x",
        None,
    )
    assert forced.mcp_config == files[0]
    codex = launcher("codex", lambda: files, "lean").request(spec, "RUN", "00-x", None)
    assert codex.mcp_config == ""


def test_an_older_user_forge_is_detected() -> None:
    plugins = parse_installed(json.dumps(INSTALLED))
    stale = older_forge(plugins, "0.4.0")
    assert [plugin.version for plugin in stale] == ["0.3.1"]
    assert older_forge(plugins, "0.3.0") == ()
    assert older_forge((InstalledPlugin("claude-agent-forge@x", "abc123", "user"),), "0.4.0") == ()
    assert version_tuple("0.4.0") == (0, 4, 0)
    assert version_tuple("63e797cd753b") is None


def test_lean_files_are_sorted_compact_and_content_addressed(tmp_path: Path) -> None:
    plugins = parse_installed(json.dumps(INSTALLED))
    first = LeanProfile(LocalWorkspace(tmp_path), lambda: plugins).files()
    reversed_order = LeanProfile(LocalWorkspace(tmp_path), lambda: tuple(reversed(plugins)))
    second = reversed_order.files()
    assert first == second
    settings = Path(first[1]).read_bytes()
    assert b": " not in settings
    keys = list(json.loads(settings)["enabledPlugins"])
    assert keys == sorted(keys)
    assert Path(first[0]).name.startswith("lean-mcp-")
    assert Path(first[1]).name.startswith("lean-settings-")
