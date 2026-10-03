from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cuanta.adapters.engines.base import HELP_TIMEOUT_S, LineParser, StreamingEngine, as_dict
from cuanta.domain.engine import (
    AssistantText,
    EngineEvent,
    EngineRequest,
    ModelUsage,
    RunResult,
    SessionStarted,
    ToolCall,
)
from cuanta.domain.sandbox import STATE_DIR, STATE_ROOT_ENV
from cuanta.ports.system import ProcessRunner

WRITABLE_ROOTS = "sandbox_workspace_write.writable_roots="
INDEX_SERVER = "mcp_servers.cuanta"
NO_SUBAGENTS = ("features.multi_agent=false", "features.multi_agent_v2=false")
RESUME_HELP_ARGS = ("exec", "resume", "--help")
RESUME_TOKENS = (
    "exec resume",
    "SESSION_ID",
    "--json",
    "--config",
    "--model",
    "--skip-git-repo-check",
)
SANDBOX_CONFIG = (
    'approval_policy="never"',
    f"{WRITABLE_ROOTS}[]",
    "sandbox_workspace_write.exclude_tmpdir_env_var=true",
    "sandbox_workspace_write.exclude_slash_tmp=true",
    "sandbox_workspace_write.network_access=false",
)


def _int(value: Any) -> int:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


class CodexParser(LineParser):
    def __init__(self, model: str) -> None:
        self._model = model or "codex"
        self._session = ""
        self._usage = ModelUsage(self._model)
        self._failed = False
        self._turns = 0
        self._last_text = ""

    def feed(self, line: str) -> list[EngineEvent]:
        stripped = line.strip()
        if not stripped.startswith("{"):
            return []
        try:
            data = json.loads(stripped)
        except ValueError:
            return []
        if not isinstance(data, dict):
            return []
        kind = data.get("type")
        if kind == "thread.started":
            self._session = str(data.get("thread_id") or "")
            return [SessionStarted(self._session, self._model)]
        if kind == "turn.completed":
            self._turns += 1
            usage = as_dict(data.get("usage"))
            cached = _int(usage.get("cached_input_tokens"))
            written = _int(usage.get("cache_write_input_tokens"))
            reasoning = _int(usage.get("reasoning_output_tokens"))
            self._usage = ModelUsage(
                self._model,
                input_tokens=max(_int(usage.get("input_tokens")) - cached - written, 0),
                output_tokens=max(_int(usage.get("output_tokens")) - reasoning, 0),
                cache_read_tokens=cached,
                cache_write_tokens=written,
                reasoning_tokens=reasoning,
            )
            return []
        if kind == "turn.failed":
            self._failed = True
            return []
        if kind == "item.completed":
            item = as_dict(data.get("item"))
            item_type = item.get("type")
            if item_type == "agent_message" and isinstance(item.get("text"), str):
                self._last_text = item["text"]
                return [AssistantText(item["text"])]
            if item_type == "command_execution":
                return [
                    ToolCall(
                        "shell", str(item.get("id") or ""), {"command": item.get("command", "")}
                    )
                ]
            if item_type in {"file_change", "mcp_tool_call", "web_search"}:
                return [ToolCall(str(item_type), str(item.get("id") or ""), {})]
        return []

    def turn_completed(self) -> bool:
        return self._turns > 0

    def finish(self, exit_code: int) -> RunResult | None:
        return RunResult(
            ok=exit_code == 0 and not self._failed,
            subtype="success" if exit_code == 0 and not self._failed else "error",
            cost_usd=None,
            num_turns=self._turns,
            session_id=self._session,
            models=(self._usage,),
            text=self._last_text,
        )


class CodexEngine(StreamingEngine):
    engine_name = "codex"
    default_binary = "codex"
    binary_env = "CUANTA_CODEX_BIN"
    help_args = ("exec", "--help")
    required_tokens = (
        "--json",
        "--sandbox",
        "read-only",
        "workspace-write",
        "--skip-git-repo-check",
        "--config",
    )

    def __init__(self, runner: ProcessRunner) -> None:
        super().__init__(runner)
        self._resume_help: str | None = None

    def resume_help(self) -> str:
        if self._resume_help is None:
            completed = self._runner.run(
                [*self.binary(), *RESUME_HELP_ARGS], timeout=HELP_TIMEOUT_S
            )
            self._resume_help = completed.stdout + completed.stderr
        return self._resume_help

    def resumable(self) -> bool:
        text = self.resume_help()
        return all(token in text for token in RESUME_TOKENS)

    def command(self, request: EngineRequest) -> list[str]:
        mode = "read-only" if request.read_only else "workspace-write"
        command = (
            [*self.binary(), "exec", "resume", "--json", "--config", f'sandbox_mode="{mode}"']
            if request.resume_session
            else [*self.binary(), "exec", "--json", "--sandbox", mode]
        )
        state = request.env.get(STATE_ROOT_ENV, "") if request.temporary_copy else ""
        writable = json.dumps([str(Path(state) / STATE_DIR)], ensure_ascii=False)
        roots = f"{WRITABLE_ROOTS}{writable}" if state else ""
        for setting in SANDBOX_CONFIG:
            chosen = roots if roots and setting.startswith(WRITABLE_ROOTS) else setting
            command.extend(["--config", chosen])
        for setting in NO_SUBAGENTS:
            command.extend(["--config", setting])
        if request.index_server:
            server, *arguments = request.index_server
            command.extend(
                [
                    "--config",
                    f"{INDEX_SERVER}.command={json.dumps(server, ensure_ascii=False)}",
                    "--config",
                    f"{INDEX_SERVER}.args={json.dumps(arguments, ensure_ascii=False)}",
                    "--config",
                    f"{INDEX_SERVER}.startup_timeout_sec=30",
                ]
            )
            if state:
                shared = json.dumps(state, ensure_ascii=False)
                command.extend(["--config", f"{INDEX_SERVER}.env={{{STATE_ROOT_ENV}={shared}}}"])
        if request.temporary_copy:
            command.append("--skip-git-repo-check")
        if request.model:
            command.extend(["--model", request.model])
        if request.resume_session:
            command.append(request.resume_session)
        command.append("-")
        return command

    def parser(self, request: EngineRequest) -> CodexParser:
        return CodexParser(request.model)
