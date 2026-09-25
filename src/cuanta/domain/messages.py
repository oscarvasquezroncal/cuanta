from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

PLACEHOLDER = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True, slots=True)
class Message:
    key: str
    params: tuple[tuple[str, str | Message], ...] = ()

    def values(self, translate: Callable[[Message], str]) -> dict[str, str]:
        return {
            name: translate(value) if isinstance(value, Message) else value
            for name, value in self.params
        }


def msg(key: str, **params: object) -> Message:
    return Message(
        key,
        tuple(
            (name, value if isinstance(value, Message) else str(value))
            for name, value in params.items()
        ),
    )


def placeholders(template: str) -> frozenset[str]:
    return frozenset(PLACEHOLDER.findall(template))


def render(template: str, values: Mapping[str, str]) -> str:
    return PLACEHOLDER.sub(lambda match: values.get(match.group(1), match.group(0)), template)


ENGLISH: dict[str, str] = {
    "mandate.missing_fields": "Missing required fields: {fields}.",
    "mandate.fill_fields": "Fill them in and try again.",
    "mandate.field_type": "What do you want to do?",
    "mandate.field_out_of_scope": "What must not change?",
    "mandate.field_bug_what": "What should change?",
    "mandate.field_bug_second": "What shows the problem?",
    "mandate.field_feature_what": "What should it do?",
    "mandate.field_feature_second": "Acceptance criteria",
    "mandate.field_refactor_what": "What should improve without changing behaviour?",
    "mandate.field_refactor_second": "Invariants",
    "mandate.field_investigation_what": "What do you want to understand?",
    "mandate.field_investigation_second": "Questions it must answer",
    "doctor.python.ok": "{version}",
    "doctor.python.old": "{version} — cuanta needs Python 3.12+",
    "doctor.engine.missing": "not found",
    "doctor.engine.found": "{version}",
    "doctor.whiskers.strong": "VERIFY_TIER={tier} — {evidence}",
    "doctor.whiskers.weak": (
        "VERIFY_TIER={tier} — {evidence}; add a test runner and a typecheck/build step "
        "to reach strong"
    ),
    "doctor.graph.small": "GRAPH_MODE={mode} (small tier: no graph needed)",
    "doctor.graph.none": "GRAPH_MODE=none — cuanta init installs graphify (graphifyy)",
    "doctor.graph.ok": "GRAPH_MODE={mode} ({evidence})",
    "doctor.graph.broken": "GRAPH_MODE=broken — graphify --help failed: {evidence}",
    "doctor.absent": "absent",
    "doctor.forge_state.corrupt": (
        "corrupt: {problem} — delete .claude/forge-state.json and run cuanta init"
    ),
    "doctor.forge_state.drift": "verify_tier {recorded} → {current} drifted",
    "doctor.forge_state.complete": "complete",
    "doctor.forge_state.next": "next phase {phase}",
    "doctor.cuanta_dir.size": "{megabytes} MB",
    "doctor.flags.missing": (
        "missing {flags}; upgrade {engine} — cuanta verifies these before every run"
    ),
    "doctor.flags.ok": "verified against --help",
    "doctor.forge.missing": "not installed (vendored {version})",
    "doctor.forge.same": "installed = vendored {version}",
    "doctor.forge.differs": "installed differs from vendored {version}",
    "doctor.placeholders.found": "{hits}",
    "doctor.placeholders.clean": "{count} agent files clean",
    "doctor.listener.on": "127.0.0.1:{port} · {written} written",
    "doctor.listener.off": "not running (runs cuanta launches start their own)",
    "doctor.terminal.modern": "modern terminal ({evidence})",
    "doctor.terminal.legacy": (
        "legacy console ({evidence}) — Windows Terminal shows the full theme"
    ),
    "doctor.ledger.none": "no ledger yet",
    "doctor.ledger.ok": "schema v{version}",
    "doctor.ledger.mismatch": "schema v{version}, expected v{expected} — upgrade cuanta",
    "verify.root_missing": "root CLAUDE.md missing",
    "verify.root_ok": "CLAUDE.md {lines}/{cap} lines",
    "verify.root_flagged": "CLAUDE.md {lines}/{cap} lines, flagged",
    "verify.root_over": "CLAUDE.md {lines}/{cap} lines, no warning",
    "verify.directory_over": "{path} {lines}/{cap} lines",
    "verify.graph": "graph wired or skipped with reason",
    "verify.agents_missing": "agents missing: {items}",
    "verify.agents_ok": "four agents present",
    "verify.support_missing": "support artifacts missing: {items}",
    "verify.support_ok": "support artifacts present",
    "verify.harness_missing": "harness missing: {items}",
    "verify.harness_ok": "harness + loop present",
    "verify.phases_incomplete": "forge-state incomplete: {items}",
    "verify.phases_ok": "forge-state: ten phases",
    "verify.gateway_ok": "{path} routes tests through the gateway",
    "verify.gateway_missing": "{path} lacks the gateway instructions: file missing",
    "verify.gateway_lacks": "{path} lacks the gateway instructions: {items}",
    "verify.gateway_piped": (
        "{path} lacks the gateway instructions: gateway output piped to tail/head"
    ),
    "verify.placeholders": "placeholders left: {items}",
    "verify.placeholders_clean": "placeholder scan clean",
    "verify.kept": "kept yours, wrote {path}",
    "verify.nothing": "forge did not run · nothing to verify yet",
    "wiring.none": "not wired",
    "wiring.no_backup": "no backup recorded",
    "wiring.restored": "restored from {backup}",
    "wiring.removed": "removed (did not exist before)",
    "wiring.env_block": "env block → {endpoint}",
    "wiring.interactive_off": "interactive sessions not wired",
    "wiring.target": "→ {target}",
    "wiring.exports": "exports to {target}",
    "wiring.codex_missing": "codex not installed",
    "wiring.otel": "[otel] → {endpoint}",
    "wiring.no_codex_config": "no ~/.codex/config.toml",
    "wiring.unreadable": "unreadable: {error}",
    "wiring.no_otel": "no [otel] table",
    "wiring.exporter": "exporter {target}",
    "graph.skip_none": "skipped (small tier; no graph tooling present)",
    "graph.skip": "skipped (small tier; {evidence})",
    "graph.defer": "deferred to MCP ({evidence})",
    "graph.update": "graphify present ({evidence}) → update",
    "graph.broken": "skipped: graphify launcher is broken ({evidence})",
    "graph.install": "no graph tooling and {size} tier → install + index",
    "graph.deferred": "deferred to MCP",
    "graph.failed": "FAILED",
    "graph.failed_detail": "FAILED — {detail}",
    "graph.updated": "updated",
    "graph.installed": "installed",
    "stage.detect": "detect",
    "stage.graph": "graph bootstrap",
    "stage.telemetry": "telemetry wiring",
    "stage.forge": "forge",
    "stage.verify": "verify and baseline",
    "stage.graph_reindex": "graph reindex",
    "stage.forge_refresh": "forge refresh",
    "stage.detect.done": "{files} files · {size}",
    "stage.detect.ok": "",
    "stage.resume": "nine lives: resuming at {stage}",
    "stage.refresh_route": "forge: FORGE_STATE=initialized — routing to refresh semantics",
    "stage.previous_life": "done in a previous life",
    "stage.skip_telemetry": "skipped (--skip-telemetry)",
    "stage.skip_forge": "skipped (--skip-forge)",
    "stage.unavailable": "not available",
    "stage.error": "{error}",
    "stage.planned": "planned",
    "stage.claude_missing": "claude not found",
    "stage.claude_failed": "claude exited {code}: {detail}",
    "stage.claude_exit": "claude exited {code}",
    "stage.run": "run {run}",
    "stage.run_cost": "run {run} · ${cost}",
    "stage.forge_skipped": "forge did not run",
    "stage.verified": "{clean}/{total} checks · {stored} baselines",
    "stage.no_consent": "no consent",
    "stage.port": "port {port}",
    "plan.run": "run: {command}",
    "plan.install_graph": "run: uv tool install graphifyy (fallback pip install graphifyy)",
    "plan.forge_state": "write: {path} (phases 0, 0.5; producer cuanta)",
    "plan.write": "write: {path}",
    "plan.gitignore": "edit: .gitignore (+ {entries})",
    "plan.install": "install: {path}",
    "plan.write_differs": "write: {path} (yours differs)",
    "plan.forge_run": "run: claude -p <init-agents body> --output-format stream-json …",
    "plan.verify": "verify: placeholders, ceilings, artifacts; store baselines",
    "plan.telemetry": "edit: .claude/settings.local.json env block (OTLP → 127.0.0.1)",
    "registration.deferred": (
        "deferred — the Skill tool was denied; Forge read SKILL.md off disk instead"
    ),
    "registration.used": "ok · Skill tool used {calls}x",
    "registration.ok": "ok · Skill tool allowed",
    "forge.phase": "forge · Phase {phase}",
    "loop.test": "test",
    "loop.suite_green": "green",
    "loop.suite_red": "red",
    "loop.suite_persistent_failure": "persistent_failure",
    "loop.nap": "already green · nap",
    "loop.pounce": "pounce {number}/{total}",
    "loop.cost": "${cost}",
    "loop.cost_unknown": "cost n/a",
    "stage.run_unpriced": "run {run} · cost n/a",
    "mandate.handoff": "handoff · {agents}",
    "mandate.halt": "engine reported a halt",
    "instinct.heuristic_ready": "offline, deterministic",
    "instinct.key_missing": "{env} is not set; after setx, open a new terminal",
    "instinct.key_openrouter_mismatch": (
        "apikey_ key with OpenRouter URL; check TYPESAFE_BASE_URL and TYPESAFE_API_KEY"
    ),
    "instinct.key_typesafe_mismatch": (
        "sk-or- key with TypeSafe URL; check TYPESAFE_BASE_URL and TYPESAFE_API_KEY"
    ),
    "instinct.fallback": "{backend} failed: {error}; heuristic answered",
    "doctor.instinct.recent_fallback": "Jev configured, recent decisions used heuristic: {error}",
    "instinct.endpoint": "{endpoint} ({model})",
    "instinct.claude_missing": "claude not found",
    "instinct.llm_ready": "claude -p --model {model}",
    "instinct.connected": "Connected: {model} via {provider} · {latency} ms",
    "instinct.unreachable": "not reachable: {error}",
    "question.change_scope": "How big is this change: trivial, normal or complex?",
    "question.mandate_scope": "How large is this mandate: trivial, normal or complex?",
    "question.network": "Is this failure likely caused by the network?",
    "question.rename_risk": "How risky is renaming a public function used in {files} files (0-10)?",
    "question.route_tier": "Which model tier should the {role} use for this mandate?",
    "question.route_risk": "How risky is this change for the rest of the codebase (0-2)?",
    "question.triage": "Why does signature {signature} fail?",
    "option.trivial": "trivial",
    "option.normal": "normal",
    "option.complex": "complex",
    "option.caused_by_this_run": "caused by this run",
    "option.pre_existing": "pre-existing",
    "option.flaky": "flaky",
    "option.environment": "environment",
    "probe.choice": "{option} (p={p})",
    "probe.noul": "p_yes={p}",
    "probe.score": "{value} (confidence {confidence})",
    "primitive.choose": "choose",
    "primitive.noul": "noul",
    "primitive.score": "score",
    "leak.amplification": "{tool} result {bytes} bytes re-sent",
    "leak.repeated_read": "read {count} times",
    "leak.test_output": "{bytes} bytes of raw test output",
    "leak.compaction": "context compacted",
    "leak.model_switch": "model switch invalidates the prompt cache",
    "suggest.amplification": (
        "keep bulky output out of {agent}'s context: route it through "
        "cuanta test / cuanta cat capsules"
    ),
    "suggest.repeated_read": (
        "reindex the graph (graphify update .) so {agent} queries instead of re-reading {subject}"
    ),
    "suggest.test_output": "use cuanta test: gateway output is bounded to one line per hairball",
    "suggest.compaction": "split the mandate: smaller REQUEST blocks finish before compaction",
    "suggest.model_switch": (
        "lower the effort of {agent} or pin one model per agent instead of switching"
    ),
    "spectrum.no_snapshots": (
        "no file snapshots: only runs cuanta launches are snapshotted, imported sessions are not"
    ),
    "spectrum.no_tokens": "no token events for this selection",
    "cost.no_usage": "no usage",
    "cost.engine": "reported by the engine",
    "cost.no_table": "no price table",
    "cost.unknown_price": "unknown model price",
    "cost.table": "{table}",
    "spectrum.tool": "tool {name}",
    "spectrum.file": "file {name}",
    "selection.story": "{story} · {count} runs",
    "selection.run": "run {run} · {kind}",
    "selection.run_story": "run {run} · {kind} · {story}",
    "selection.session": "session {session}",
    "selection.all": "all imported sessions",
    "selection.since": "since {since}",
    "run_status.running": "running",
    "run_status.ok": "ok",
    "run_status.failed": "failed",
    "run_status.interrupted": "interrupted",
    "run_kind.init": "init",
    "run_kind.loop": "loop",
    "run_kind.mandate": "mandate",
    "run_kind.refresh": "refresh",
    "run_kind.test": "test",
    "run_kind.probe": "probe",
    "run_kind.import": "import",
    "leak_kind.amplification": "amplification",
    "leak_kind.repeated_read": "repeated read",
    "leak_kind.test_output": "test output",
    "leak_kind.compaction": "compaction",
    "leak_kind.model_switch": "model switch",
    "check.python": "python",
    "check.engine": "engine",
    "check.whiskers": "whiskers",
    "check.graph": "graph",
    "check.flags": "flags",
    "check.forge_state": "forge-state",
    "check.forge": "forge",
    "check.placeholders": "placeholders",
    "check.ceilings": "ceilings",
    "check.listener": "listener",
    "check.telemetry": "telemetry",
    "check.terminal": "terminal",
    "check.ledger": "ledger",
    "check.cuanta": ".cuanta",
    "run_status.completed": "completed",
    "affected.no_baseline": "no baseline yet: the first run is the full suite",
    "affected.unsupported": "{runner} has no per-file selection: running the full suite",
    "affected.nothing_changed": "nothing changed since the baseline: running the full suite",
    "affected.no_related": "no tests relate to the {count} changed files: running the full suite",
    "affected.too_many": "{count} related tests: running the full suite instead",
    "affected.selected": "{tests} related tests for {changed} changed files, last failed first",
    "mandate.verdict": "final verdict: full suite",
    "mandate.verdict_skipped": "no test runner: no verdict",
    "mandate.verdict_status": "full suite {status}",
    "route.policy": "policy default: {tier}",
    "route.instinct": (
        "Instinct chose {tier} ({confidence}): policy {default}, {scope} scope, "
        "{radius} files in reach"
    ),
    "route.fixed_role": "you set this role to {tier}",
    "route.pinned": "pinned by you: {model}",
    "route.capped": "asked for {requested}, capped at {cap}",
    "route.stepped_down": "no {requested} model available: stepped down to {tier}",
    "route.stepped_up": "no {requested} model available: stepped up to {tier}",
    "route.none": "no model available for {tier} within the caps",
    "route.low_confidence": (
        "Instinct was {confidence} sure (below {minimum}): policy default {tier}"
    ),
    "route.escalation_capped": "persistent failure, but {tier} is already the cap",
    "route.escalated": "persistent failure: raised from {previous} to {tier}",
    "route.learned": (
        "{tier} succeeded {rate} of {samples} similar runs: cheapest tier that works"
    ),
    "route.risk": "risk {risk} of 2: tester raised to {tier}",
    "route.off": "routing off: engine default model",
    "route.single": "single model for this engine: {model}",
    "audit.ok": "ran on the planned model",
    "audit.not_run": "not delegated to in this run",
    "audit.not_seen": (
        "no telemetry for this agent (it may not have run, or the listener was off)"
    ),
    "audit.name_mismatch": (
        "no events under this name; telemetry saw {agents}: the agent name may not match"
    ),
    "audit.env": "{variable} is set and overrides the planned model",
    "audit.invocation": "Claude passed model {model} when it spawned this agent",
    "audit.version": "same family, other version: planned {planned}, ran {actual}",
    "audit.unknown": "ran on another model; cause unknown",
    "audit.summary": "{matched}/{total} agents ran on their planned model",
    "sentence.decision": "{subject} → {answer} ({confidence})",
    "sentence.scope": "Mandate scope",
    "sentence.risk": "Risk",
    "sentence.triage": "Failure cause",
    "sentence.network": "Network failure",
    "sentence.tier": "{role} tier",
    "sentence.other": "{question}",
    "role.orchestrator": "Orchestrator",
    "role.analyst": "Analyst",
    "role.senior": "Senior",
    "role.tester": "Tester",
    "role.docs": "Docs",
    "question.clarity": "How clear and actionable is this request for a coding agent (0-2)?",
    "question.gap_evidence": (
        "Does the request include concrete evidence, such as an error message, "
        "a log line or a failing test?"
    ),
    "question.gap_place": "Does the request name the place in the code where the change belongs?",
    "question.gap_expected": (
        "Does the request state the expected behaviour or how to prove it works?"
    ),
    "question.gap_split": "Does the request bundle more than one independent task?",
    "chip.evidence": "Missing the exact error",
    "chip.place": "Where does it happen?",
    "chip.expected": "What should happen instead?",
    "chip.split": "Looks like two tasks: split",
    "cross.step": "{role} on {engine} ({model})",
    "cross.done": "run {run}",
    "cross.skipped": "{role}: no model routed, skipped",
    "cross.budget": "the cross-engine budget is spent",
    "cross.no_engine": "{engine} is not available",
    "cross.failed": "{role} failed: the pipeline stopped",
    "route.low_clarity": "request unclear (clarity {clarity} of 2): capped at {tier}",
    "overhead.context": (
        "fixed session context ≈ {fixed} tokens ({share}) of the first request's {total}"
    ),
    "overhead.plugins": "{count} plugins loaded: {names}",
    "overhead.servers": "{count} MCP servers: {names}",
    "overhead.failed": "{name} failed to connect after {seconds} s: {error}",
    "overhead.hooks": "{count} hooks: {ms} ms and {chars} characters of added context",
    "overhead.startup": (
        "start-up {before} before the first request (spawn → session {spawn} · session setup "
        "{setup} · prompt → request {queue}); the first request took {request}, first token "
        "after {ttft}, {output} output tokens"
    ),
    "overhead.context_total": "the first request carried {total} tokens of context",
    "doctor.user_forge.old": (
        "a user-level claude-agent-forge {version} is installed; cuanta ships {vendored} per "
        "project and the old one can load in every session"
    ),
    "doctor.mcp.failed": (
        "MCP server {name} failed to connect after {seconds} s: {error}. Lean sessions skip it; "
        "fix it with claude mcp or remove it"
    ),
    "doctor.startup": (
        "last session ({plugins} plugins, {servers} MCP servers, {hooks} hooks): {detail}"
    ),
    "bench.budget": "bench budget reached after ${spent}: remaining runs skipped",
    "bench.run": "{order}/{total} {task} · {condition} · rep {rep}",
    "bench.accepted": "accepted",
    "bench.rejected": "not accepted",
    "home.fresh": "this folder has no agent system yet",
    "terminal.override": "{setting}",
    "terminal.platform": "platform {platform}",
    "terminal.conpty": "console window {window} (ConPTY)",
    "terminal.conhost": "console window {window} (conhost)",
    "terminal.vt_off": "virtual terminal processing unavailable",
    "estimate.range": "Similar mandates cost {low} to {high} (p50–p90 of {count}).",
    "estimate.one": "Based on 1 similar run: {cost}",
    "estimate.few": "Based on {count} similar runs: {low} to {high}",
    "estimate.plan": "Estimate from the plan: ~{cost}",
    "estimate.none": "No prices yet to estimate this plan.",
    "route.policy_role": "{role} on {tier} because your policy uses {tier} for {purpose}",
    "route.instinct_scope": "Instinct picked {tier} for a {scope} request",
    "route.instinct_reach": (
        "Instinct picked {tier}: a {scope} request reaching about {radius} files"
    ),
    "purpose.orchestrator": "coordination",
    "purpose.analyst": "analysis",
    "purpose.senior": "development",
    "purpose.tester": "testing",
    "purpose.docs": "documentation",
    "tier.economy": "economy",
    "tier.standard": "standard",
    "tier.premium": "premium",
    "tier.frontier": "frontier",
    "scope.trivial": "trivial",
    "scope.normal": "normal",
    "scope.complex": "complex",
    "question.intake_type": "What kind of task is this: bug, feature, refactor or investigation?",
    "question.intake_read_only": "Does the request ask to change no files at all?",
    "question.intake_depth": "How deep does this task need to go: quick, normal or deep?",
    "question.intake_gap_evidence": (
        "Does the request include evidence of the failure (error, log or test)?"
    ),
    "question.intake_gap_expected": (
        "Does the request say what should happen instead of the failure?"
    ),
    "question.intake_gap_acceptance": "Does the request say how to know the feature is done?",
    "question.intake_gap_invariants": (
        "Does the request say what must stay the same after the refactor?"
    ),
    "question.intake_gap_deliverable": (
        "Does the request name the deliverable it expects (summary, report, diagram or risks)?"
    ),
    "terminal.host": "started from {host}",
    "terminal.hints": "{names} set",
    "terminal.vt_on": "virtual terminal processing enabled",
    "terminal.no_evidence": "no evidence of a legacy console",
}


def keyed(prefix: str, value: str) -> Message | str:
    key = f"{prefix}.{re.sub(r'[^a-z0-9]+', '_', value.lower()).strip('_')}"
    return msg(key) if key in ENGLISH else value


def option_message(value: str) -> Message | str:
    return keyed("option", value)


def question_message(text: str) -> Message | None:
    return parse_english(text, "question.")


def parse_english(text: str, prefix: str) -> Message | None:
    for key, template in ENGLISH.items():
        if not key.startswith(prefix):
            continue
        parts = PLACEHOLDER.split(template)
        pattern = "".join(
            f"(?P<{part}>.+?)" if index % 2 else re.escape(part) for index, part in enumerate(parts)
        )
        found = re.fullmatch(pattern, text)
        if found:
            return msg(key, **found.groupdict())
    return None


def english(message: Message) -> str:
    template = ENGLISH.get(message.key)
    if template is None:
        return message.key
    return render(template, message.values(english))
