import json
import os
import pathlib
import sys
import urllib.request

HELP = """Usage: claude [options]
  -p, --print  --output-format <format> (choices: "text", "json", "stream-json")
  --verbose  --permission-mode <mode> (choices: "acceptEdits", "dontAsk", "plan")
  --allowedTools, --allowed-tools <tools...>  --disallowedTools, --disallowed-tools <tools...>
  --tools <tools...>
  --model <model>  --max-budget-usd <amount>  --agents <json-or-file>
  --strict-mcp-config  --mcp-config <configs...>  --settings <file-or-json>
  --effort <level>  --append-system-prompt <prompt>
  --exclude-dynamic-system-prompt-sections  --no-session-persistence
"""

AGENT = """---
name: {name}
description: agent for fixture
model: sonnet
---

You are {name}. Run `{test}` and report.
"""


GATEWAY = (
    "Run the suite with `cuanta test --json`; its output is bounded, never pipe it. "
    "Read failure detail with `cuanta cat <capsule> --level L2`.\n"
)


def gateway():
    return "" if os.environ.get("FAKE_FORGE_NO_GATEWAY") else GATEWAY


STAGED = False


def emit(record):
    sys.stdout.write(json.dumps(record) + "\n")
    sys.stdout.flush()


def agent_record(trace_id, agent, model):
    return {
        "timeUnixNano": "1767225600500000000",
        "traceId": trace_id,
        "body": {"stringValue": "claude_code.api_request"},
        "attributes": [
            {"key": "event.name", "value": {"stringValue": "api_request"}},
            {"key": "model", "value": {"stringValue": model}},
            {"key": "agent.name", "value": {"stringValue": agent}},
            {"key": "input_tokens", "value": {"intValue": 400}},
            {"key": "output_tokens", "value": {"intValue": 90}},
            {"key": "cost_usd", "value": {"doubleValue": 0.01}},
            {"key": "session.id", "value": {"stringValue": "fake-session"}},
        ],
    }


def post_telemetry(model, agents=None):
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    traceparent = os.environ.get("TRACEPARENT", "")
    if not endpoint or not traceparent:
        return
    trace_id = traceparent.split("-")[1]
    resource = [
        {"key": key, "value": {"stringValue": value}}
        for key, value in (
            item.split("=", 1) for item in os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "").split(",") if "=" in item
        )
    ]
    records = [
        {
            "timeUnixNano": "1767225600000000000",
            "traceId": trace_id,
            "body": {"stringValue": "claude_code.api_request"},
            "attributes": [
                {"key": "event.name", "value": {"stringValue": "api_request"}},
                {"key": "model", "value": {"stringValue": model}},
                {"key": "input_tokens", "value": {"intValue": 1200}},
                {"key": "output_tokens", "value": {"intValue": 300}},
                {"key": "cache_read_tokens", "value": {"intValue": 5000}},
                {"key": "cache_creation_tokens", "value": {"intValue": 700}},
                {"key": "cost_usd", "value": {"doubleValue": 0.05}},
                {"key": "query_source", "value": {"stringValue": "sdk"}},
                {"key": "session.id", "value": {"stringValue": "fake-session"}},
            ],
        },
        {
            "timeUnixNano": "1767225601000000000",
            "traceId": trace_id,
            "body": {"stringValue": "claude_code.tool_result"},
            "attributes": [
                {"key": "event.name", "value": {"stringValue": "tool_result"}},
                {"key": "tool_name", "value": {"stringValue": "Read"}},
                {"key": "success", "value": {"stringValue": "true"}},
                {"key": "tool_result_size_bytes", "value": {"stringValue": "4000"}},
                {"key": "tool_input", "value": {"stringValue": "{\"file_path\": \"src/app.py\"}"}},
                {"key": "session.id", "value": {"stringValue": "fake-session"}},
            ],
        },
    ]
    forced = os.environ.get("FAKE_CLAUDE_AGENT_MODEL", "")
    for name, spec in (agents or {}).items():
        records.append(agent_record(trace_id, name, forced or spec.get("model", model)))
    body = json.dumps({"resourceLogs": [{"resource": {"attributes": resource}, "scopeLogs": [{"logRecords": records}]}]})
    request = urllib.request.Request(
        f"{endpoint}/v1/logs", data=body.encode(), headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(request, timeout=5).read()
    except OSError:
        pass


def mandate_template(root):
    source = os.environ.get("FAKE_FORGE_TEMPLATE", "")
    if source and pathlib.Path(source).is_file():
        text = pathlib.Path(source).read_text(encoding="utf-8")
        return text.replace("{{PROJECT_NAME}}", root.name).replace("{{SENIOR_NAME}}", "python-senior")
    return gateway() + "# Mandate\n\n```\n# MANDATE\n\n=== REQUEST ===\n\nTYPE: x\n```\n"


def forge(root):
    test = "pytest"
    files = {
        "CLAUDE.md": "# fixture\n\n> **No code graph, deliberately.** Below roughly one hundred source files.\n",
        ".claude/agents/architecture-analyst.md": AGENT.format(name="architecture-analyst", test=test),
        ".claude/agents/python-senior.md": AGENT.format(name="python-senior", test=test),
        ".claude/agents/tester.md": AGENT.format(name="tester", test=test) + gateway(),
        ".claude/agents/docs-updater.md": AGENT.format(name="docs-updater", test=test),
        "AGENTS_GUIDE.md": "# Guide\n\n## Harness\n\n| Layer | Implemented by |\n",
        "HISTORIAS.md": "# Backlog\n",
        "docs/GROUND_TRUTH.md": "# Facts\n",
        "docs/CHANGELOG_INTERNAL.md": "# Changelog\n",
        "docs/FLAGS.md": "# Flags\n",
        "docs/RUN_LOG.md": "# Run log\n",
        "docs/IMPROVEMENTS.md": "# Improvements\n",
        "docs/LOOP.md": "# Loop\n\nVERIFY_TIER=strong: an unattended sequence is sanctioned.\n",
        "docs/MANDATE_TEMPLATE.md": mandate_template(root),
    }
    def writable(relative):
        if STAGED and relative.startswith(".claude/"):
            return root / ".cuanta" / "forge-out" / "claude" / relative.removeprefix(".claude/")
        return root / relative

    for relative, text in files.items():
        path = writable(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() and not (root / relative).exists():
            path.write_text(text, encoding="utf-8")
    source = root / ".claude" / "forge-state.json"
    state = json.loads(source.read_text(encoding="utf-8")) if source.exists() else {}
    state["phases_completed"] = ["0", "0.5", "1", "2A", "2B", "2.5", "3", "4", "5", "6"]
    target = writable(".claude/forge-state.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, indent=2), encoding="utf-8")


def mandate(root):
    fix = os.environ.get("FAKE_CLAUDE_FIX", "")
    if "|" not in fix:
        return
    relative, old, new = fix.split("|", 2)
    path = root / relative
    if not path.is_file():
        return
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


def main(argv):
    if "--version" in argv:
        print("9.9.9 (Claude Code)")
        return 0
    if "--help" in argv:
        print(HELP)
        return 0
    orphan = os.environ.get("FAKE_CLAUDE_ORPHAN", "")
    if orphan:
        import subprocess

        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        pathlib.Path(orphan).write_text(str(child.pid), encoding="utf-8")
    after = argv[argv.index("-p") + 1] if "-p" in argv and argv.index("-p") + 1 < len(argv) else ""
    prompt = after if after and not after.startswith("--") else sys.stdin.read()
    captured = os.environ.get("FAKE_CLAUDE_PROMPT_OUT", "")
    if captured:
        pathlib.Path(captured).write_text(prompt, encoding="utf-8")
    model = argv[argv.index("--model") + 1] if "--model" in argv else "claude-sonnet-5"
    root = pathlib.Path.cwd()
    emit({"type": "system", "subtype": "init", "session_id": "fake-session", "model": model,
          "apiKeySource": "none", "claude_code_version": "9.9.9"})
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "Phase 1 — AUDIT or SEED done. Phase 2A next."}]}, "parent_tool_use_id": None})
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Agent", "input": {"subagent_type": "architecture-analyst", "prompt": "plan"}}]}, "parent_tool_use_id": None})
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t2", "name": "Write", "input": {"file_path": "CLAUDE.md"}}]}, "parent_tool_use_id": "t1"})
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t3", "name": "Task", "input": {"subagent_type": "tester", "prompt": "run"}}]}, "parent_tool_use_id": None})
    if "=== REQUEST ===" in prompt or os.environ.get("FAKE_CLAUDE_BENCH"):
        mandate(root)
    else:
        global STAGED
        STAGED = ".cuanta/forge-out/" in prompt
        forge(root)
    agents = {}
    if "--agents" in argv:
        source = argv[argv.index("--agents") + 1]
        agents = json.loads(pathlib.Path(source).read_text(encoding="utf-8"))
        agents_out = os.environ.get("FAKE_CLAUDE_AGENTS_OUT", "")
        if agents_out:
            pathlib.Path(agents_out).write_text(json.dumps(agents), encoding="utf-8")
    post_telemetry(model, agents)
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "Phase 6 complete."}]}, "parent_tool_use_id": None})
    emit({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 7,
        "duration_ms": 1234,
        "session_id": "fake-session",
        "total_cost_usd": 0.42,
        "result": pathlib.Path(os.environ["FAKE_CLAUDE_REPORT"]).read_text(encoding="utf-8") if os.environ.get("FAKE_CLAUDE_REPORT") else "done",
        "modelUsage": {model: {"inputTokens": 1200, "outputTokens": 300, "cacheReadInputTokens": 5000, "cacheCreationInputTokens": 700, "costUSD": 0.42, "thinkingTokens": 0}},
    })
    return int(os.environ.get("FAKE_CLAUDE_EXIT", "0"))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
