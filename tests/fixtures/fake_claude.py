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


def investigation_paths(root):
    paths = (
        "src/components/cart-drawer.tsx",
        "src/stores/cart-store.ts",
        "src/domain/cart.ts",
        "src/lib/create-checkout.ts",
    ) if (root / "src/lib/create-checkout.ts").is_file() else ("src/shop/pricing.py",)
    return tuple(path for path in paths if (root / path).is_file())


def investigation_citation(root, path, needle, span=0):
    lines = (root / path).read_text(encoding="utf-8").splitlines()
    line = next((number for number, text in enumerate(lines, 1) if needle in text), 0)
    if not line or line + span > len(lines):
        return ""
    return f"{path}:{line}" + (f"-{line + span}" if span else "")


def investigation_answer(root):
    if (root / "src/lib/create-checkout.ts").is_file():
        paths = investigation_paths(root)
        if len(paths) != 4:
            return "Investigation fixture unavailable."
        citations = (
            investigation_citation(root, paths[0], "createCheckout(cartStore.items)"),
            investigation_citation(root, paths[1], "export const cartStore", 6),
            investigation_citation(root, paths[2], "export function cartTotal", 1),
            investigation_citation(root, paths[3], "export function createCheckout", 2),
        )
        if not all(citations):
            return "Investigation source contract unavailable."
        return (
            "CartDrawer links Checkout to createCheckout(cartStore.items) "
            f"at {citations[0]}. cartStore holds items "
            f"and its total() calls cartTotal at {citations[1]}. "
            f"cartTotal sums price times quantity at {citations[2]}. "
            "createCheckout builds /checkout?total= with total.toFixed(2) "
            f"at {citations[3]}."
        )
    if (root / "src/shop/pricing.py").is_file():
        cart = investigation_citation(root, "src/shop/pricing.py", "def cart_total(", 2)
        invoice = investigation_citation(root, "src/shop/pricing.py", "def invoice_total(", 2)
        if not cart or not invoice:
            return "Investigation source contract unavailable."
        return (
            "cart_total sums prices, applies percent discount, and rounds to two "
            f"decimals at {cart}. invoice_total discounts amount, "
            f"adds shipping, then rounds at {invoice}. Both repeat "
            "discount maths; invoice_total includes shipping and cart_total does not."
        )
    return "Investigation fixture unavailable."


def investigation_record(trace_id, second, kind, values):
    values = {"event.name": kind, "session.id": "fake-session", **values}
    attributes = []
    for key, value in values.items():
        field = "boolValue" if isinstance(value, bool) else "intValue" if isinstance(value, int) else "doubleValue" if isinstance(value, float) else "stringValue"
        attributes.append({"key": key, "value": {field: value}})
    return {
        "timeUnixNano": str(1767225600000000000 + second * 1000000000),
        "traceId": trace_id,
        "body": {"stringValue": "claude_code." + kind},
        "attributes": attributes,
    }


def investigation_records(trace_id, model, agents, root):
    records = []
    analyst = next((name for name in agents if "analyst" in name), "")

    def record(second, kind, **values):
        records.append(investigation_record(trace_id, second, kind, values))

    def api(second, agent, fresh, output, cache_read=0, cache_write=0, cost=0.01):
        record(second, "api_request", **{
            "agent.name": agent,
            "model": agents.get(agent, {}).get("model", model),
            "input_tokens": fresh,
            "output_tokens": output,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_write,
            "cost_usd": cost,
        })

    api(0, "main", 1200, 100, 5000, 700, 0.05)
    second = 1
    if analyst:
        record(second, "tool_decision", **{
            "agent.name": "main", "tool_name": "Agent", "tool_use_id": "investigation-agent",
            "decision": "allowed", "tool_input": json.dumps({"subagent_type": analyst}),
        })
        second += 1
        api(second, analyst, 400, 90, 800, 100)
        second += 1
    owner = analyst or "main"
    for number, path in enumerate(investigation_paths(root)):
        record(second, "tool_result", **{
            "tool_name": "Read", "tool_use_id": f"investigation-read-{number}",
            "success": True, "tool_result_size_bytes": len((root / path).read_bytes()),
            "tool_input": json.dumps({"file_path": path}),
        })
        second += 1
        if number == 0:
            api(second, owner, 25, 5, 800 if analyst else 5000, cost=0.005)
            second += 1
    api(second, owner, 10, 360 if analyst else 600, 800 if analyst else 5000)
    if analyst:
        second += 1
        record(second, "subagent_completed", agent_type=analyst, output_tokens=360)
        second += 1
        api(second, "main", 20, 100, 5000, 360, 0.02)
        second += 1
        api(second, "main", 10, 600, 5000)
    return records


def investigation_usage(records, model):
    keys = {"input_tokens": "inputTokens", "output_tokens": "outputTokens", "cache_read_tokens": "cacheReadInputTokens", "cache_creation_tokens": "cacheCreationInputTokens", "cost_usd": "costUSD"}
    usage = {}
    for record in records:
        values = {item["key"]: next(iter(item["value"].values())) for item in record["attributes"]}
        if values["event.name"] != "api_request":
            continue
        row = usage.setdefault(values.get("model", model), {key: 0 for key in (*keys.values(), "thinkingTokens")})
        for source, target in keys.items():
            row[target] += values.get(source, 0)
    return usage


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
    if os.environ.get("FAKE_CLAUDE_INVESTIGATION") == "1":
        records = investigation_records(trace_id, model, agents or {}, pathlib.Path.cwd())
    else:
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


def investigation_main(argv, root, model):
    agents = {}
    if "--agents" in argv:
        source = argv[argv.index("--agents") + 1]
        agents = json.loads(pathlib.Path(source).read_text(encoding="utf-8"))
        if output := os.environ.get("FAKE_CLAUDE_AGENTS_OUT"):
            pathlib.Path(output).write_text(json.dumps(agents), encoding="utf-8")
    analyst = next((name for name in agents if "analyst" in name), "")
    if analyst:
        emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "investigation-agent", "name": "Agent", "input": {"subagent_type": analyst, "prompt": "Investigate the source and cite file:line."}}]}, "parent_tool_use_id": None})
    for number, path in enumerate(investigation_paths(root)):
        emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"investigation-read-{number}", "name": "Read", "input": {"file_path": path}}]}, "parent_tool_use_id": "investigation-agent" if analyst else None})
    post_telemetry(model, agents)
    report = os.environ.get("FAKE_CLAUDE_REPORT")
    answer = pathlib.Path(report).read_text(encoding="utf-8") if report else investigation_answer(root)
    usage = investigation_usage(investigation_records("", model, agents, root), model)
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": answer}]}, "parent_tool_use_id": None})
    emit({
        "type": "result", "subtype": "success", "is_error": False,
        "num_turns": 6 if analyst else 3, "duration_ms": 1234,
        "session_id": "fake-session", "total_cost_usd": sum(row["costUSD"] for row in usage.values()),
        "result": answer, "modelUsage": usage,
    })
    return int(os.environ.get("FAKE_CLAUDE_EXIT", "0"))


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
    if os.environ.get("FAKE_CLAUDE_INVESTIGATION") == "1":
        return investigation_main(argv, root, model)
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
