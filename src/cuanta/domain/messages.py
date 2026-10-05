from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache

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


def message_payload(message: Message) -> dict[str, object]:
    return {
        "key": message.key,
        "params": {
            name: message_payload(value) if isinstance(value, Message) else value
            for name, value in message.params
        },
    }


def parse_message(value: object) -> Message | None:
    if not isinstance(value, dict):
        return None
    key, params = value.get("key"), value.get("params", {})
    if not isinstance(key, str) or not key or not isinstance(params, dict):
        return None
    found: list[tuple[str, str | Message]] = []
    for name, item in params.items():
        nested = parse_message(item)
        if nested is None and not isinstance(item, str):
            return None
        found.append((str(name), nested if nested is not None else str(item)))
    return Message(key, tuple(found))


def placeholders(template: str) -> frozenset[str]:
    return frozenset(PLACEHOLDER.findall(template))


def render(template: str, values: Mapping[str, str]) -> str:
    return PLACEHOLDER.sub(lambda match: values.get(match.group(1), match.group(0)), template)


ENGLISH: dict[str, str] = {
    "verify.label": "Verification",
    "verify.skipped": "skipped",
    "verify.off": "off",
    "verify.readonly": "skipped (read-only)",
    "verify.affected": "affected tests",
    "verify.full": "full suite",
    "verify.wall": "max {time}",
    "verify.unbounded": "no time limit",
    "verify.policy": "{kind}, {wall}",
    "verify.running": "verifying · {runner}",
    "verify.live": "verifying · {runner} · {elapsed}",
    "verify.timeout": "inconclusive: timed out after {time}. Run by hand: {command}",
    "verify.times": "Preparation {preparation} · Agent {agent} · Verification {verification}",
    "verify.in_session": "in agent session",
    "run_error.pack_depth": "Unknown pack depth",
    "run_error.pack_depth_hint": "use quick, normal or deep",
    "pipeline.feed_session": "session started with {model}",
    "pipeline.feed_handoff": "handoff to {agent}",
    "run_error.engine_missing": "{name} not found on PATH",
    "run_error.engine_install": "install it or pick another engine",
    "run_error.engine_flags": "{name} lacks flags cuanta needs: {flags}",
    "run_error.engine_upgrade": "upgrade {name}",
    "run_error.engine_unknown": "unknown engine {engine_name}",
    "run_error.engine_choices": "use claude, codex or opencode",
    "run_error.fields": "missing required fields: {names}",
    "run_error.fields_hint": "pass them as flags",
    "run_error.type": "unknown type {type}",
    "run_error.type_hint": "use one of {choices}",
    "run_error.forge": "this project has no Forge agents yet (.claude/agents)",
    "run_error.forge_hint": "run cuanta init first, or use simple mode (--simple)",
    "run_error.gateway": "no gateway result yet",
    "run_error.gateway_hint": "run cuanta test first",
    "run_error.green": "last cuanta test was green \u00b7 nap",
    "run_error.green_hint": "nothing to fix",
    "run_error.keep": "--keep only applies to --sandbox runs",
    "run_error.keep_hint": "add --sandbox",
    "run_error.classic": "--classic runs without a scout",
    "run_error.classic_hint": "drop --shape scout or --classic",
    "run_error.cross": "{engine} cannot run a team of separate launches",
    "run_error.cross_hint": "use {choices}",
    "run_error.estimated": "{cost} (estimated)",
    "envelope.time": "Time forecast: P50 {p50} · P90 {p90} · n={samples}",
    "read_efficiency.no_reads": "No successful source reads were observed.",
    "read_efficiency.missing_report": "The report needed to count cited files is unavailable.",
    "leak.second_context": (
        "Delegated context initialized with {tokens} fresh/cache-write tokens (observed)."
    ),
    "leak.re_summary": "Possible child handoff adds {tokens} cache-write tokens (heuristic).",
    "suggest.second_context": "Check whether {agent} needs a separate context.",
    "suggest.re_summary": "Keep the child handoff concise; this match is heuristic.",
    "suggest.single_context": "Run the investigation as a single context.",
    "index_metrics.title": "Map exploration",
    "index_metrics.hit_rate": "Index hit rate: {value}",
    "index_metrics.exploration": "{index} index calls, {raw} raw reads, {total} exploration calls",
    "index_metrics.tokens_estimate": "Estimated exploration tokens: {count} (returned bytes ÷ 4)",
    "index_metrics.stale": "Stale facts: {count}",
    "index_metrics.guard": "Protected-path edits: {count}",
    "index_metrics.out_of_plan": "Edits outside the plan: {count}",
    "engine.cost_unknown": (
        "Stopped because the engine did not report the step cost needed to enforce the budget."
    ),
    "engine.budget_stopped": "Stopped at the spend cap or recorded an over-cap result.",
    "engine.command_line": (
        "the {engine} command line would be {length} characters (limit {limit})"
    ),
    "engine.command_line_hint": (
        "the request travels on stdin or in a prompt file and does not count; shorten the "
        "system prompt (the analyst agent's body) or the protected paths"
    ),
    "stop.finished": "the agent finished",
    "stop.finished_on_request": (
        "the agent finished after the governor asked it to wrap up at {spent} of {limit}"
    ),
    "stop.repair_time_limit": "stopped by the repair time limit of {seconds} s",
    "stop.time_limit": "stopped by the time limit of {seconds} s",
    "stop.wall_limit": "stopped by the time limit of {minutes} min",
    "stop.turn_limit": "stopped at the turn limit of {turns} turns",
    "stop.turn_limit_one": "stopped at the turn limit of 1 turn",
    "stop.cost_limit": "stopped at the spend cap of {cap}",
    "stop.cost_unknown": (
        "stopped because the engine did not report the cost needed to enforce the {cap} cap"
    ),
    "stop.governor": "stopped by the governor to stay within the {cap} cap",
    "stop.repair_limit": "stopped after {rounds} repair rounds with new errors left",
    "stop.repair_limit_one": "stopped after 1 repair round with new errors left",
    "stop.verification_failed": "stopped because verification found {count} new errors",
    "stop.verification_failed_one": "stopped because verification found 1 new error",
    "stop.unfinished": "stopped before cuanta could verify step {step} of {total}",
    "stop.session_closed": "stopped because the session closed before the next turn",
    "stop.invalid_step_plan": "stopped because the step plan could not be read",
    "stop.planning_changes": "stopped because files changed before a step was authorized",
    "stop.engine_failed": "stopped because the engine ended with {subtype}",
    "stop.engine_exited": "stopped because the engine exited with code {code} before its result",
    "stop.no_result": "stopped because the engine ended without a result",
    "stop.raised": "stopped by an error before the engine returned a result",
    "stop.user": "stopped by the user",
    "stop.interrupted": "interrupted before the run finished",
    "stop.running": "still running",
    "stop.failed": "the run failed",
    "stop.team_partial": "the team stopped before every role finished",
    "stop.team_failed": "the team run failed",
    "result.partial_cost": "{cost} (partial)",
    "result.partial_estimated_cost": "{cost} (estimated, partial)",
    "result.partial_turns": "{turns} (partial)",
    "limits.none": "no limits",
    "limits.active": "limits: {items}",
    "limits.join": "{first} · {rest}",
    "limits.spend": "spend cap {cap}",
    "limits.turns": "{turns} turns",
    "limits.turns_one": "1 turn",
    "limits.wall": "{minutes} min",
    "telemetry.unreadable": "{count} telemetry records could not be read; details in {path}",
    "telemetry.unreadable_one": "1 telemetry record could not be read; details in {path}",
    "telemetry.already_wired": "127.0.0.1:{port} · claude on · already wired",
    "guarantee.row": "{name}: {status} ({detail})",
    "guarantee.spend": "Spend cap",
    "guarantee.turns": "Turn limit",
    "guarantee.readonly": "Read-only",
    "guarantee.telemetry": "Telemetry",
    "guarantee.enforced": "enforced",
    "guarantee.checked": "checked after the run",
    "guarantee.unavailable": "not available",
    "guarantee.claude_spend": "native budget limit",
    "guarantee.claude_turns": "native turn limit",
    "guarantee.claude_readonly": "write tools denied; file changes checked, no verified OS sandbox",
    "guarantee.claude_readonly_unchecked": (
        "write tools denied; no filesystem check between cross-engine roles"
    ),
    "guarantee.claude_telemetry": "reported tokens and cost; missing values stay n/a",
    "guarantee.codex_spend": "token-priced estimate; no native spend limit",
    "guarantee.codex_turns": "no supported turn limit",
    "guarantee.codex_readonly": "explicit sandbox for investigations and analyst roles",
    "guarantee.codex_telemetry": "JSONL tokens; cost is estimated when prices are known",
    "guarantee.opencode_spend": "stop at reported step boundaries; a step can exceed the cap",
    "guarantee.opencode_turns": "no supported turn limit",
    "guarantee.opencode_readonly": "investigations and analyst roles refused",
    "guarantee.opencode_telemetry": "step tokens and cost; missing values stay n/a",
    "guarantee.unknown_spend": "engine has no verified spend guarantee",
    "guarantee.unknown_turns": "engine has no verified turn guarantee",
    "guarantee.unknown_readonly": "engine has no verified read-only guarantee",
    "guarantee.unknown_telemetry": "engine has no verified telemetry guarantee",
    "guarantee.cap_warning": (
        "{engine} cannot enforce the spend cap. Cost is checked after the run; "
        "the run may exceed the cap."
    ),
    "guarantee.step_cap_warning": (
        "OpenCode stops when reported step cost reaches the cap. "
        "An in-flight step can exceed it; missing step cost stops the run."
    ),
    "guarantee.opencode_refused": (
        "OpenCode investigations and analyst roles are unavailable: this integration cannot "
        "enforce read-only access while allowing graphify shell commands. "
        "Choose Codex or Claude for this role."
    ),
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
    "mandate_file.type_not_stated": "type not stated: {type}; use -t/--type",
    "mandate_file.missing": "mandate file not found: {path}",
    "mandate_file.missing_hint": "check the path, or pass the request with --what",
    "mandate_file.unreadable": "the mandate file could not be read: {path} ({error})",
    "mandate_file.empty": "the mandate file is empty: {path}",
    "mandate_file.empty_hint": "write the request in the file, or pass it with --what",
    "mandate_file.with_evidence": "--from and --evidence both give the evidence",
    "mandate_file.with_evidence_hint": (
        "put the log in the mandate file, or add a short note with --why"
    ),
    "mandate_file.labelled": "fields from the file's labels: {fields}",
    "mandate_file.over_file": "{options}: the command line wins over the file",
    "mandate_file.what_moved": "--what: the file's what moves to the top of the evidence",
    "mandate_file.why_added": "--why: added after the file's evidence",
    "mandate_file.join": "{first}; {rest}",
    "mandate_file.type_overridden": (
        "the file says {stated}; cuanta {command} runs it as {kind}; "
        "cuanta run FILE keeps the file's type"
    ),
    "mandate_file.type_unknown_overridden": (
        "the file says {stated}, which is not a type; cuanta {command} runs it as {kind}"
    ),
    "evidence.missing": "evidence file not found: {path}",
    "evidence.missing_hint": "check the path, or paste the text with --why",
    "evidence.unreadable": "the evidence file could not be read: {path} ({error})",
    "mandate_file.not_text": "{path} is not UTF-8 text",
    "mandate_file.not_text_hint": "save it as UTF-8",
    "mandate_file.unknown_type": "unknown type {type} in the file's TYPE line",
    "mandate_file.unknown_type_hint": "use one of {types}, or pass -t/--type",
    "shortcut.needs_file": "cuanta run needs a mandate file",
    "shortcut.needs_file_hint": "cuanta run mandate.md",
    "shortcut.one_file": "cuanta run takes one mandate file; it got {count}",
    "shortcut.one_file_hint": "run each file on its own, or queue them with cuanta queue add -f",
    "shortcut.file_twice": "a mandate file was given as an argument and with --from",
    "shortcut.text_is_file": "{path} is a file: cuanta {command} -f {path} runs it as the mandate",
    "shortcut.text_twice": "the request was given twice: as text and with --what",
    "shortcut.twice_hint": "keep one of them",
    "shortcut.needs_text": "cuanta {command} needs the request",
    "shortcut.needs_text_hint": (
        'cuanta {command} "<what to do>", --what "<text>" when it starts with -, or --from <file>'
    ),
    "guide.title": "cuanta · what people do",
    "guide.app": "Open the app",
    "guide.app_command": "cuanta  (or cuanta ui)",
    "guide.file": "Run a mandate from a file: the file is the whole mandate",
    "guide.file_command": "cuanta run mandate.md  (or cuanta mandate -f mandate.md)",
    "guide.oneline": "A feature, a fix or an audit in one line",
    "guide.oneline_command": 'cuanta feat "…" · cuanta fix "…" · cuanta audit "…"',
    "guide.result": "See a result",
    "guide.result_command": "cuanta runs show  (the last run) · cuanta runs  (the last ten)",
    "guide.costs": "See costs",
    "guide.costs_command": "cuanta costs · cuanta spectrum <run id>",
    "guide.limits": "Set limits (a run has none unless you set them)",
    "guide.limits_command": (
        "cuanta run mandate.md --max-budget-usd 5 --max-turns 50 --max-wall 30"
    ),
    "guide.limits_more": "Also: [limits] in .cuanta/config.toml, or the Limits switch in the app.",
    "guide.more": "Every option: cuanta <command> --help",
    "interrupt.stopped": "interrupted",
    "interrupt.run": "interrupted · run {run}",
    "interrupt.see": "see it: cuanta runs show {run}",
    "interrupt.resume": "nine lives: re-run to resume",
    "runs.none_yet": "no runs yet",
    "runs.none_yet_hint": "start one: cuanta run mandate.md",
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
    "doctor.cuanta_dir.large": (
        "{megabytes} MB, over {limit} MB; largest file {path} ({file_megabytes} MB)"
    ),
    "doctor.flags.missing": (
        "missing {flags}; upgrade {engine} — cuanta verifies these before every run"
    ),
    "doctor.flags.ok": "verified against --help",
    "doctor.forge.missing": "not installed (vendored {version})",
    "doctor.forge.same": "installed = vendored {version}",
    "doctor.forge.differs": "installed differs from vendored {version}",
    "doctor.template.ready": "{path} ready",
    "doctor.template.restorable": (
        "{path} missing: cuanta writes it from the vendored Forge {version} when a mandate needs it"
    ),
    "doctor.template.incomplete": (
        "{path} missing and the Forge team is incomplete (missing {agents})"
    ),
    "doctor.template.unusable": "{problem}: {hint}",
    "template.broken": "{path} has no fenced === REQUEST === block",
    "template.unreadable": "{path} cannot be read as a plain file inside the project",
    "template.unusable_hint": "fix it, or delete it and run cuanta init --template",
    "template.needs_team": "{path} needs the four Forge agents; missing {agents}",
    "template.needs_team_hint": "run cuanta init",
    "template.write_failed": "could not write {path} ({error})",
    "template.write_failed_hint": "make sure docs is a folder you can write to",
    "doctor.gateway.ok": "{paths} route tests through the gateway",
    "doctor.gateway.gap": "{problem} · fix: {fix}",
    "doctor.suggestions.beside": (
        "{count} files an older init left beside yours in .claude/: {paths}; "
        "cuanta init moves them to .cuanta/forge-suggested/"
    ),
    "doctor.suggestions.beside_one": (
        "1 file an older init left beside yours in .claude/: {paths}; "
        "cuanta init moves it to .cuanta/forge-suggested/"
    ),
    "doctor.suggestions.waiting": (
        "{count} Forge suggestions wait in .cuanta/forge-suggested/; "
        "compare them in the app's Init view or copy one over your file"
    ),
    "doctor.suggestions.waiting_one": (
        "1 Forge suggestion waits in .cuanta/forge-suggested/; "
        "compare it in the app's Init view or copy it over your file"
    ),
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
    "verify.nothing": "forge did not run · nothing to verify yet",
    "verify.fix_gateway": "add {items} to the test step in {path}",
    "verify.fix_unpipe": (
        "remove the | tail or | head after cuanta test in {path}; its output is already bounded"
    ),
    "verify.fix_adopt": (
        "Forge's version has them: copy {suggestion} over {path}, "
        "or choose Use new in the app's Init view"
    ),
    "verify.fix_template": "run cuanta init --template to write it from the vendored Forge copy",
    "verify.fix_regenerate": (
        "run cuanta init --refresh-forge to have Forge write it (uses the model)"
    ),
    "verify.fix_root": (
        "move rules out of CLAUDE.md until it fits {cap} lines, "
        "or add a line under its title that says Over ceiling"
    ),
    "verify.fix_directory": (
        "move rules out of {path} until it fits {cap} lines, "
        "or add a line under its title that says Over cap"
    ),
    "verify.suggested": "{original}: Forge's version is in {path} (+{added}/-{removed} lines)",
    "verify.suggested_elsewhere": (
        "Forge's version of a file you changed is in {path}; yours was kept"
    ),
    "verify.suggested_note": (
        "Your files were not changed. To adopt a suggestion, copy it over your file, or compare "
        "it in the app's Init view and choose Use new; delete .cuanta/forge-suggested/ to "
        "discard them."
    ),
    "verify.moved": "moved {count} files an older init left beside yours out of .claude/: {paths}",
    "verify.moved_one": "moved 1 file an older init left beside yours out of .claude/: {paths}",
    "verify.sibling": (
        "{path} is still beside your file: .cuanta/forge-suggested/ already holds other versions"
    ),
    "verify.fix_sibling": "compare {path} with your file, keep what you need, then delete {path}",
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
    "graph.scheduled": "updating in the background · {log}",
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
    "stage.telemetry_wired": "port {port} · already wired",
    "stage.skip_graph": "skipped (--skip-graph)",
    "stage.forge_kept": "kept: FORGE_STATE=initialized · --refresh-forge runs Forge again",
    "stage.forge_restored": (
        "kept: FORGE_STATE=initialized · restored {files} · --refresh-forge runs Forge again"
    ),
    "plan.run": "run: {command}",
    "plan.install_graph": "run: uv tool install graphifyy (fallback pip install graphifyy)",
    "plan.forge_state": "write: {path} (phases 0, 0.5; producer cuanta)",
    "plan.write": "write: {path}",
    "plan.gitignore": "edit: .gitignore (+ {entries})",
    "plan.install": "install: {path}",
    "plan.suggest": "suggest: {path} (yours differs, kept)",
    "plan.move": "move: {path} → {target}",
    "plan.template": "write: {path} from the vendored Forge {version}",
    "plan.forge_run": "run: claude -p <init-agents body> --output-format stream-json …",
    "plan.verify": "verify: placeholders, ceilings, artifacts; store baselines",
    "plan.telemetry": "edit: .claude/settings.local.json env block (OTLP → 127.0.0.1)",
    "plan.run_background": "run in the background: {command}",
    "plan.forge_kept": (
        "keep: Forge is initialized, no model runs (--refresh-forge runs Forge again)"
    ),
    "registration.deferred": (
        "deferred — the Skill tool was denied; Forge read SKILL.md off disk instead"
    ),
    "registration.used": "ok · Skill tool used {calls}x",
    "registration.ok": "ok · Skill tool allowed",
    "forge.phase": "forge · Phase {phase}",
    "forge.rulebook_rewritten": (
        "CLAUDE.md: Forge rewrote it in place ({before} → {after} lines); "
        "compare it with your previous version in {backup}"
    ),
    "forge.kept": "kept ({command} runs Forge again)",
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
    "mandate.template_written": "{path} was missing: wrote it from the vendored Forge {version}",
    "mandate.template_unwritable": (
        "{path} is missing and could not be written ({error}): this run uses the vendored "
        "Forge {version} copy"
    ),
    "mandate.template_borrowed": (
        "{path} is missing: this run uses the vendored Forge {version} copy; "
        "cuanta init --template keeps it"
    ),
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
    "question.envelope_risk": (
        "How much costlier than planned is this change likely to be? 1 means as planned."
    ),
    "question.envelope_exploration": (
        "How much more reading than planned will this change need? 1 means as planned."
    ),
    "question.envelope_tier": "Which model tier fits the {role} role for this change?",
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
    "cost.missing_usage": "cost unavailable: the engine did not report complete usage",
    "cost.table": "{table}",
    "cost.governor_stop": (
        "estimated by the governor from items and elapsed time; the stopped turn reported no usage"
    ),
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
    "leak_kind.second_context": "second context",
    "leak_kind.re_summary": "possible handoff",
    "check.python": "python",
    "check.engine": "engine",
    "check.whiskers": "whiskers",
    "check.graph": "graph",
    "check.flags": "flags",
    "check.forge_state": "forge-state",
    "check.forge": "forge",
    "check.template": "template",
    "check.gateway": "gateway",
    "check.suggestions": "suggestions",
    "check.placeholders": "placeholders",
    "check.ceilings": "ceilings",
    "check.listener": "listener",
    "check.telemetry": "telemetry",
    "check.terminal": "terminal",
    "check.ledger": "ledger",
    "check.cuanta": ".cuanta",
    "check.agents_md": "home AGENTS.md",
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
    "route.pure": "pure: --pure runs every role on {model}",
    "route.main_model": "the main session runs on the chosen model {model}",
    "route.main_pin_conflict": (
        "the orchestrator is pinned to {pin}, but the main session runs on {model}"
    ),
    "route.main_pin_hint": "pin the orchestrator to the main session model, or drop the pin",
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
    "sentence.exploration": "Exploration",
    "sentence.tier": "{role} tier",
    "sentence.other": "{question}",
    "role.orchestrator": "Orchestrator",
    "role.analyst": "Analyst",
    "role.senior": "Senior",
    "role.tester": "Tester",
    "role.docs": "Docs",
    "role.scout": "Scout",
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
    "cross.role_budget": "{role} has no remaining reserved budget",
    "cross.share": "{role} budget share: ${cap}",
    "route.pin_unknown": (
        "{role} is pinned to {model}, which is not in the model catalog; run cuanta models"
    ),
    "route.pin_engine": (
        "{role} is pinned to {model}, but this launch can only use {engines}; pin a model of "
        "{engines} or leave it unpinned"
    ),
    "route.pin_provider": (
        "{role}: {model} belongs to another provider; a team uses one provider ({provider})"
    ),
    "route.pin_lost": "{role} is pinned to {model}, but the route chose another model",
    "route.pin_absent": (
        "{role} is pinned to {model}, but {role} does not run in this shape; "
        "drop the pin or the --shape"
    ),
    "route.pins_rejected": "role pins cannot be honored",
    "route.fast_pin_conflict": (
        "fast implementation runs one model ({model}), and a role pin names another"
    ),
    "provider.claude": "Claude team",
    "provider.codex": "GPT team",
    "team.role": "{role}: {engine} {model}, share ${share}",
    "team.role_unshared": "{role}: {engine} {model}",
    "team.context_index": "Context: index tools and the anchored handoff chain",
    "team.context_text": "Context: a text pack and the anchored handoff chain",
    "team.warning": "Warning: {warning}",
    "team.recommended": (
        "Measured {type} runs recommend the {provider}: ${cost} per accepted change "
        "(attempts: {attempts})"
    ),
    "cross.blocked": "{role} reported that it is blocked: {reason}",
    "cross.writer_salvaged": (
        "{role} stopped at its budget share before finishing; the change may be incomplete"
    ),
    "cross.verify_unresolved": "the checks still fail after {role}; the change is not verified",
    "cross.role_budget_overrun": (
        "{role} has no remaining budget because {other} on {engine} overran its share by ${over}"
    ),
    "cross.salvaged": (
        "{role} stopped at its budget share; cuanta saved a partial handoff and the next role "
        "continues"
    ),
    "cross.partial_budget": (
        "{role} stopped at its budget share and ${left} left cannot cover the next roles' ${need} "
        "floors"
    ),
    "cross.optional_skipped": "{role} skipped: ${cap} left is below its ${floor} floor",
    "cross.tester_skipped": (
        "tester skipped: verification passed and ${cap} left is below its ${floor} floor"
    ),
    "cross.guard_role": (
        "{role} changed protected files ({paths}); the pipeline stopped before the next role"
    ),
    "cross.verify": (
        "verification after {role}: {passed} of {total} commands passed in {seconds}s, $0 model "
        "spend"
    ),
    "cross.repair": "{role} gets one repair turn for the failing checks",
    "cross.repair_reserve": "fix budget: ${cap} is held for one repair turn",
    "cross.repair_reserve_docs": (
        "fix budget: ${cap} is held for one repair turn, funded from the docs share (docs is off)"
    ),
    "cross.verify_handed": (
        "verification still fails after the repair; the tester receives the results"
    ),
    "cross.unreadable": "Codex created files cuanta cannot read: {paths}",
    "cross.prepared": (
        "cuanta created {count} planned new files before the Codex writer so they stay readable"
    ),
    "cross.overrun": (
        "{role} on {engine} spent ${cost} against its ${cap} share; the ${over} overrun comes out "
        "of the remaining budget"
    ),
    "cross.native_cap": "{role} native cap ${cap}, ${margin} below its ${share} share",
    "governor.finish": (
        "{role}: the governor sent the finish turn ({trigger}) at ${spent} of ${limit}"
    ),
    "governor.finish_team": (
        "team session: the governor sent the finish turn ({trigger}) at ${spent} of the ${limit} "
        "cap; the main agent reads it when the running subagent returns"
    ),
    "governor.finish_unsent": (
        "{role}: the finish turn could not be sent; the native cap and salvage stop it instead"
    ),
    "governor.checkpoint": (
        "{role}: the governor asked for a checkpoint note; a fresh session should save about "
        "${saving}"
    ),
    "governor.checkpoint_unsent": (
        "{role}: the checkpoint request could not be sent (estimated saving ${saving}); the "
        "session continues"
    ),
    "governor.rotated": (
        "{role} restarts in a fresh session with its checkpoint note: ${left} of its share left, "
        "native cap ${cap}, estimated saving ${saving}"
    ),
    "governor.rotation_skipped": (
        "{role}: no fresh session after the checkpoint; its note is the handoff"
    ),
    "governor.trigger.share": "its share is nearly spent",
    "governor.trigger.headroom": "two more steps would reach its limit",
    "governor.trigger.projection": "the cap would arrive before the plan is done",
    "governor.trigger.fresh_session": "a fresh session would cost less",
    "governor.codex_stop": (
        "{role}: the governor stopped Codex at an estimated ${spent} of its ${share} share"
    ),
    "governor.codex_stop_unsent": (
        "{role}: the governor could not stop Codex (estimated ${spent} of ${share}); it runs "
        "to the end and any overrun comes out of the remainder"
    ),
    "governor.resuming": "{role}: a short finish turn resumes the stopped thread",
    "governor.resumed": (
        "{role}: the resumed thread wrote its handoff; the stopped turn cost about ${cost} "
        "(estimate), ${left} of its share left"
    ),
    "governor.resume_unavailable": (
        "{role}: the stopped thread cannot be resumed; salvage builds its handoff from the "
        "changed files"
    ),
    "governor.resume_failed": (
        "{role}: the finish turn after the stop failed; salvage builds its handoff from the "
        "changed files"
    ),
    "cross.verify_commands": "cuanta will run these checks after each writing role: {commands}",
    "completion.complete": "complete",
    "completion.complete_skipped": "complete, optional roles skipped",
    "completion.partial": "partial",
    "completion.failed": "failed",
    "sandbox.record_failed": (
        "sandbox changes could not be collected ({error}); copy kept, result cannot be applied"
    ),
    "guarantee.codex_builds": (
        "Codex cannot run builds on this Windows host; cuanta verifies instead"
    ),
    "cross.cost_unknown": (
        "the next role cannot start because a previous cost is unknown "
        "and the remaining budget cannot be calculated"
    ),
    "cross.no_engine": "{engine} is not available",
    "cross.failed": "{role} failed: the pipeline stopped",
    "cross.stopped": "the pipeline was stopped on request; later roles did not run",
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
    "overhead.cache_warm": (
        "the first request hit a warm cache: {read} of {total} tokens read from cache ({share})"
    ),
    "overhead.cache_cold": (
        "the first request hit a cold cache: nothing read from cache, "
        "{written} of {total} tokens written to it"
    ),
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
    "doctor.agents_md.share": (
        "{path}: {bytes} bytes ≈ {tokens} tokens · estimated {share} of the last session's "
        "fixed context ({fixed} tokens; estimate at {per_token} bytes per token)"
    ),
    "doctor.agents_md.unavailable": (
        "{path}: {bytes} bytes · share of the fixed context unavailable: no first-request data yet"
    ),
    "bench.budget": "bench budget reached after ${spent}: remaining runs skipped",
    "bench.cost_unknown": (
        "the next run cannot start because a previous cost is unknown "
        "and the remaining budget cannot be calculated"
    ),
    "bench.cost_partial": (
        "the next run cannot start because a previous run stopped early and its cost "
        "is only a lower bound, so the remaining budget cannot be calculated"
    ),
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
    "estimate.calibrated": "Estimate from the plan: ~{cost} · factor {factor} from {count} runs",
    "estimate.none": "No prices yet to estimate this plan.",
    "envelope.suggest.depth": "Use {depth} depth: P90 {p90}",
    "envelope.suggest.tier": "Run the {role} on {model}: P90 {p90}",
    "envelope.suggest.where": "Narrow WHERE to about {files} files: P90 {p90}",
    "envelope.suggest.scout": "Use the scout and senior shape: P90 {p90}",
    "envelope.line": "Forecast {p50} (P90 {p90}), margin {margin}, {cache}",
    "envelope.line_uncapped": "Forecast {p50} (P90 {p90}), no cap, {cache}",
    "envelope.line_unknown": "Forecast n/a: a role has no price, {cache}",
    "envelope.phases": "This mandate has {count} phases; the forecast covers one change",
    "envelope.steps": "This mandate has {count} steps; the forecast covers one change",
    "envelope.cache_warm": "warm cache ({share})",
    "envelope.cache_cold": "cold cache",
    "envelope.cache_unknown": "cache unknown",
    "envelope.tight": "Tight: the P90 is within 15% of the {cap} cap or over it.",
    "envelope.infeasible": "Infeasible: the P50 {p50} is over the {cap} cap.",
    "envelope.try": "Try: {suggestion}",
    "envelope.jev": (
        "Jev adjusted the forecast: risk ×{risk}, exploration ×{exploration}, weight {weight}"
    ),
    "envelope.jev_refused": "Jev was not asked: the call would cost {cost}, over the {cap} cap.",
    "envelope.jev_unpriced": "Jev was not asked: its call cannot be priced.",
    "envelope.jev_consent": "Jev was not asked: run cuanta instinct use jev to give consent.",
    "envelope.jev_unavailable": "Jev was not asked: {reason}",
    "envelope.jev_failed": "Jev did not answer, the plain forecast is kept: {error}",
    "envelope.jev_tier": "Jev suggests the {tier} tier for the {role}",
    "envelope.failed": "Forecast unavailable, the launch goes ahead without one: {error}",
    "scout.mode.native": "as a subagent in the session",
    "scout.mode.launch": "as its own read-only launch",
    "scout.shape_forced": "Shape: scout and senior (forced); the scout runs {mode}",
    "scout.shape_auto": (
        "Shape: scout and senior, because exploration is {share} of the forecast, above "
        "{threshold}; the scout runs {mode}"
    ),
    "scout.shape_pinned": (
        "Shape: scout and senior, because the scout is pinned; the scout runs {mode}"
    ),
    "scout.shape_pure": (
        "Shape: pipeline without a scout: --pure runs every role on {model}, so a scout costs "
        "as much as the senior; the senior works from cuanta's context pack (--shape scout "
        "adds one anyway)"
    ),
    "scout.refused": "--shape scout applies to features, fixes and refactors",
    "scout.refused_simple": "--shape scout needs a team; simple mode runs one agent",
    "scout.refused_engine": "--shape scout needs a Claude or GPT team",
    "scout.refused_routing": "--shape scout needs routing: the scout is a routed role",
    "scout.pack": (
        "{role} evidence pack: {tokens} of {budget} tokens, {facts} facts, {snippets} snippets, "
        "edit set {edit}; cuanta cat {capsule}"
    ),
    "scout.pack_trimmed": (
        "evidence pack trimmed to fit: {snippets} snippets and {facts} facts dropped; the edit "
        "set is kept"
    ),
    "scout.pack_over": (
        "evidence pack still {tokens} tokens after trimming, over its {budget}-token budget; "
        "the edit set is kept"
    ),
    "scout.pack_invalid": "{count} evidence pack items did not match the working copy",
    "scout.pack_fallback": (
        "the {role} returned no evidence pack; the senior gets its reads and cuanta's edit set"
    ),
    "scout.pack_plan_edit": (
        "the {role} confirmed no edit set; the senior uses cuanta's change plan: {paths}"
    ),
    "scout.leaks": "{role} read {count} files outside the pack and the edit set: {paths}",
    "scout.named": "{role} edited files outside the edit set and named them: {paths}",
    "scout.unnamed": (
        "{role} edited files outside the edit set without naming them in its handoff: {paths}"
    ),
    "docs.requested": "Docs: on, the request asks for docs",
    "docs.not_requested": "Docs: off, the request does not ask for docs",
    "docs.trial": "Docs: off in trials",
    "docs.forced_on": "Docs: on (runs.docs = on)",
    "docs.forced_off": "Docs: off (runs.docs = off)",
    "docs.pinned": "Docs: on, the docs role is pinned",
    "docs.agent": "Docs: on, the request names the docs agent",
    "docs.path": "Docs: on, the request names a docs path",
    "docs.requested_in": "Docs: on, the request asks for docs in {field} ({term})",
    "docs.agent_in": "Docs: on, the request names the {term} agent in {field}",
    "docs.path_in": "Docs: on, the request names the docs path {term} in {field}",
    "docs.flag_on": "Docs: on (--docs on)",
    "docs.flag_off": "Docs: off (--docs off)",
    "docs.field.what": "the description",
    "docs.field.why": "the evidence",
    "docs.field.where": "the location",
    "docs.field.tests": "the expected tests",
    "docs.refused_classic": "--classic runs with docs on",
    "docs.refused_pin": "--docs off and a docs pin disagree",
    "docs.refused_simple": "simple mode runs without the docs role",
    "docs.refused_investigation": "investigations run without the docs role",
    "docs.refused_fast": "fast implementation runs one writer without the docs role",
    "docs.funds_repair": (
        "repair budget: ${cap} is held for one repair turn, funded from the docs share (docs is "
        "off)"
    ),
    "pack.anchors_missing": "File:line references not found among the indexed files: {anchors}",
    "pack.anchors_ambiguous": (
        "File:line references that match more than one indexed file: {anchors}"
    ),
    "pack.anchors_out_of_range": (
        "File:line references past the last line of their file: {anchors}"
    ),
    "pack.anchors_stale": (
        "Referenced files changed after indexing and were left out of the pack: {anchors}"
    ),
    "pack.anchors_partial": (
        "Referenced ranges longer than the pack window, packed in part (range → packed lines): "
        "{anchors}"
    ),
    "pack.anchors_omitted": (
        "Referenced ranges left out by the {budget}-token pack budget: {anchors}"
    ),
    "pack.anchors_protected": (
        "Referenced files the change plan protects (context only, not editable): {anchors}"
    ),
    "change_plan.read_only_phrase": ('Change plan: read-only, because the request says "{phrase}"'),
    "change_plan.guard_released": (
        "Change plan: {paths} stays editable: a phase says not to touch it, but the request "
        "anchors or names it elsewhere; put it in Out of scope to protect it"
    ),
    "queue.warm_until": "warm prefix until {time}",
    "queue.cold_since": "prefix cold since {time}; the next mandate writes it again",
    "queue.warm_unknown": (
        "warm prefix: unknown (needs Claude, a cache TTL saved for this auth mode and a recent "
        "request)"
    ),
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
    "purpose.scout": "exploration",
    "tier.economy": "economy",
    "tier.standard": "standard",
    "tier.premium": "premium",
    "tier.frontier": "frontier",
    "depth.quick": "quick",
    "depth.normal": "normal",
    "depth.deep": "deep",
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
    "cache_probe.seed": (
        "seed run on {model}: first request read {read} and wrote {written} cache tokens"
    ),
    "cache_probe.waiting": "waiting {seconds} s before the next run",
    "cache_probe.warm": "after {gap} s: warm (read {read}, wrote {written})",
    "cache_probe.cold": "after {gap} s: cold (read {read}, wrote {written})",
    "cache_probe.unknown": "after {gap} s: no usable reading ({reason})",
    "cache_probe.skipped": "the ${budget} cap does not cover the {gap} s check: skipped",
    "cache_probe.verdict.measured": ("cache TTL {ttl} s (warm at {lower} s, cold at {upper} s)"),
    "cache_probe.verdict.consistent": (
        "cache TTL {ttl} s as the engine declares; warm at every gap up to {lower} s"
    ),
    "cache_probe.verdict.lower_bound": "cache TTL of at least {lower} s",
    "cache_probe.verdict.uncacheable": (
        "the prefix is below the model's minimum cacheable length; no TTL recorded"
    ),
    "cache_probe.verdict.unstable": ("the prefix changed between identical runs; no TTL recorded"),
    "cache_probe.verdict.inconclusive": "inconclusive: no usable bracket; no TTL recorded",
    "sandbox.copied": (
        "isolated copy ready: {files} files copied and {linked} dependency files linked "
        "in {seconds} s at {path}"
    ),
    "sandbox.kept": "isolated copy kept at {path}",
    "sandbox.remove_failed": "could not remove the isolated copy at {path}; delete it by hand",
    "sandbox.trial": "{files} files changed in the copy; patch saved to {path}",
    "sandbox.no_changes": "the run changed no files in the copy",
    "sandbox.skipped_links": (
        "{count} links point outside the project and were left out of the copy: {paths}"
    ),
    "sandbox.outside_dependencies": (
        "dependencies above the project folder are not in the copy, so installs or builds "
        "that need them can fail: {paths}"
    ),
    "sandbox.skipped_outputs": "build outputs ignored by .gitignore were not copied: {paths}",
    "sandbox.stopped_before_launch": "stopped before the engine started; nothing was spent",
    "sandbox.ignored_changes": (
        "{count} files ignored by .gitignore changed in the copy and were left out: {paths}"
    ),
    "sandbox.read_only_breach": (
        "the investigation changed {count} files in the copy; nothing will be applied"
    ),
    "sandbox.base_missing": (
        "{count} files changed in the project while the run worked; this run cannot be applied"
    ),
    "sandbox.dependencies_changed": (
        "node_modules in the project changed during the run ({count} files); "
        "run npm ci in the project to restore it"
    ),
    "sandbox.state_changed": (
        "cuanta's own files in the project changed during the run ({count}): {paths}; "
        "review .cuanta/config.toml and discard pending isolated-copy results you did not expect"
    ),
    "sandbox.unreadable": (
        "{count} files ignored by .gitignore could not be read and are not in the copy: {paths}"
    ),
    "index.too_large": (
        "the index stopped before reading any file: {files} files to index, over the limit of"
        " {limit}. Largest folders: {folders}"
    ),
    "index.no_folders": "none, every file is at the top level",
    "index.too_large_fix": (
        "leave out what is not your code in .gitignore, or put these lines in"
        " .cuanta/config.toml (replace an existing [detect] exclude):\n{lines}"
    ),
    "index.busy": (
        "{path} is open in another process, so it was not rebuilt; the index is unchanged"
    ),
    "index.busy_fix": (
        "finish or stop the cuanta run that uses it (its index tools keep it open),"
        " then run {command} again"
    ),
    "index.read_only": "{path} is read-only, so it was not rebuilt; the index is unchanged",
    "index.read_only_fix": (
        "make it writable (clear its read-only attribute), then run {command} again"
    ),
    "index.recovery_busy": (
        "{path} must be recreated (it is damaged or from another version) but is open in"
        " another process, so it was left as it was"
    ),
    "index.recovery_busy_fix": (
        "finish or stop the cuanta run that uses it (its index tools keep it open), then try again"
    ),
    "progress.index": "index",
    "progress.plan": "plan",
    "progress.forecast": "forecast",
    "progress.files": "{files} files",
    "progress.files_one": "1 file",
    "progress.done": "{detail} · {seconds} s",
    "progress.took": "{seconds} s",
    "init.complete": "init complete",
    "init.problems": "init finished with problems",
    "init.warnings": "{count} warnings above, each with its fix",
    "init.warnings_one": "1 warning above, with its fix",
    "init.refresh_skip": "--refresh-forge runs Forge and --skip-forge skips it",
    "init.refresh_skip_hint": "pass only one of them",
}


def counted(key: str, name: str, count: int) -> Message:
    return msg(f"{key}_one") if count == 1 else msg(key, **{name: count})


def keyed(prefix: str, value: str) -> Message | str:
    key = f"{prefix}.{re.sub(r'[^a-z0-9]+', '_', value.lower()).strip('_')}"
    return msg(key) if key in ENGLISH else value


def option_message(value: str) -> Message | str:
    return keyed("option", value)


def question_message(text: str) -> Message | None:
    return parse_english(text, "question.")


def parse_english(text: str, prefix: str) -> Message | None:
    for key, template in ENGLISH.items():
        if key.startswith(prefix) and template == text and not PLACEHOLDER.search(template):
            return msg(key)
    for key, template in ENGLISH.items():
        if not key.startswith(prefix) or not PLACEHOLDER.sub("", template).strip():
            continue
        found = _english_pattern(template).fullmatch(text)
        if found:
            return msg(key, **found.groupdict())
    return None


def english(message: Message) -> str:
    template = ENGLISH.get(message.key)
    if template is None:
        return message.key
    return render(template, message.values(english))


@lru_cache(maxsize=2048)
def _english_pattern(template: str) -> re.Pattern[str]:
    seen: set[str] = set()
    parts: list[str] = []
    for index, part in enumerate(PLACEHOLDER.split(template)):
        if not index % 2:
            parts.append(re.escape(part))
        elif part in seen:
            parts.append(f"(?P={part})")
        else:
            seen.add(part)
            parts.append(f"(?P<{part}>.+?)")
    return re.compile("".join(parts), re.DOTALL)
