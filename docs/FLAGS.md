# Flags and config — cuanta

**Budget:** the full registry. `CLAUDE.md` §Flags mirrors only rows marked `Surprising? = yes`.
What does NOT belong here: rules about how to use a flag (rulebook), history of why it exists
(changelog). Defaults are not copied here — they drift; read them from the source cited.

| Key | Where read | Purpose | Surprising? |
|---|---|---|---|
| `CUANTA_ENGINE` | `src/cuanta/domain/config.py` | default agent engine | no |
| `CUANTA_INSTINCT` | `src/cuanta/domain/config.py` | instinct backend (heuristic / jev / llm) | no |
| `cuanta instinct use NAME --global` | `src/cuanta/cli/commands/instinct.py` | set the user-level instinct backend; the project setting takes precedence | no |
| `CUANTA_EMOJI` | `src/cuanta/domain/config.py` | emoji output toggle | no |
| `CUANTA_THEME` | `src/cuanta/domain/config.py` | UI theme | no |
| `CUANTA_LANG` | `src/cuanta/domain/config.py` | UI language (locales in `tui/locales`) | no |
| `CUANTA_PORT` | `src/cuanta/domain/config.py` | local listener port | no |
| `CUANTA_TEST_COMMAND` | `src/cuanta/domain/config.py` | override detected test command | no |
| `CUANTA_TEST_RUNNER` | `src/cuanta/domain/config.py` | force a test-runner plugin | no |
| `CUANTA_BUDGET_USD` | `src/cuanta/domain/config.py` | spend budget | no |
| `CUANTA_MAX_TURNS` / `runs.max_turns` (`config.toml`) / `cuanta mandate --max-turns` | `src/cuanta/domain/config.py`, `domain/depth.py`, `cli/commands/mandate.py` | Claude turn limit; zero or unset uses the depth limit, and the CLI option overrides config | yes |
| `cache.ttl_s` (`config.toml`) | `src/cuanta/domain/config.py`, `bootstrap.py` | conservative observed warm lower bound in seconds, or nominal TTL inside a measured warm/cold bracket; zero means unknown | yes |
| `cache.auth` (`config.toml`) | `src/cuanta/domain/config.py`, `bootstrap.py` | authentication mode of the TTL measurement | no |
| `cache.engine_version` (`config.toml`) | `src/cuanta/domain/config.py`, `bootstrap.py` | Claude Code version used for the TTL measurement | no |
| `cache.measured_on` (`config.toml`) | `src/cuanta/domain/config.py`, `bootstrap.py` | local date of the TTL measurement | no |
| `cache.model` (`config.toml`) | `src/cuanta/domain/config.py`, `bootstrap.py` | model used for the TTL measurement | no |
| `CUANTA_HOME` | `src/cuanta/adapters/system/platform.py` | data directory override | no |
| `CUANTA_CONFIG_DIR` | `src/cuanta/adapters/system/platform.py` | config directory override | no |
| `CUANTA_CLAUDE_BIN` / `CUANTA_CODEX_BIN` / `CUANTA_OPENCODE_BIN` | `src/cuanta/adapters/engines/*.py` | engine binary path override | no |
| `CUANTA_ENGINE_BIN` | `src/cuanta/adapters/engines/base.py` | [UNVERIFIED: generic binary override semantics] | no |
| `CUANTA_SHELL` | `src/cuanta/adapters/system/shell.py` | force shell detection | no |
| `CUANTA_TERMINAL` | `src/cuanta/domain/terminal.py` | override terminal classification with `modern` or `legacy` | no |
| `TYPESAFE_API_KEY` | `src/cuanta/adapters/instinct/jev.py` | credential for the Jev instinct backend | no |
| `TYPESAFE_BASE_URL` | `src/cuanta/adapters/instinct/jev.py` | Jev endpoint override; takes precedence over `TYPESAFE_API_BASE` | no |
| `TYPESAFE_API_BASE` | `src/cuanta/adapters/instinct/jev.py` | fallback Jev endpoint override when `TYPESAFE_BASE_URL` is absent | no |
| `CUANTA_RUN_ID` | `src/cuanta/domain/telemetry.py`, `cli/commands/test.py`, `tui/services.py` | correlate a run's telemetry | no |
| `CUANTA_FORCE_TTY` | `src/cuanta/cli/runtime.py` | force interactive terminal behavior | no |
| `CUANTA_NO_ANIMATION` | `src/cuanta/cli/commands/ui.py` | disable TUI launch animation | no |
| `NO_COLOR` | `src/cuanta/cli/runtime.py`, `cli/app.py` | standard no-color; also suppresses auto-launch of the TUI | yes |
| `runs.session` (`.cuanta/config.toml`), `--session` | `src/cuanta/domain/config.py`, `application/engine_run.py` | runs cuanta launches are `lean` (no user plugins, hooks or MCP servers) or `full` | yes |
| `cuanta mandate --sandbox` / `--keep` | `src/cuanta/cli/commands/mandate.py`, `application/sandbox.py`, `adapters/system/sandbox.py` | run in an isolated copy of the project; the patch and after-images land in `.cuanta/trials/<run-id>/`, and the copy is deleted unless `--keep` | yes |
| `CUANTA_STATE_ROOT` | `src/cuanta/bootstrap.py`, `domain/sandbox.py` | set by cuanta for engines running in an isolated copy, so `cuanta test` and `cuanta cat` inside the copy use the original project's ledger and capsules | yes |
| `git.workflow` (`config.toml`) | `src/cuanta/domain/config.py`, `domain/handoff.py`, `cli/commands/runs.py`, `tui/services.py` | `branches` suggests a branch per change type, `trunk` a commit on the current branch; cuanta only prints the git commands | yes |
| `cuanta runs apply` / `discard` / `branch` | `src/cuanta/cli/commands/runs.py`, `application/trials.py`, `domain/handoff.py` | apply an isolated-copy run after a drift check, reject it, or print the git hand-off for the configured workflow | no |
| `cuanta runs accept` / `reject --reason` | `src/cuanta/cli/commands/runs.py`, `application/outcomes.py` | record a run's outcome once; a cross-engine role resolves to its pipeline; rejecting an isolated-copy run discards it | no |
| `cuanta costs --since` | `src/cuanta/cli/commands/costs.py`, `application/costs.py`, `domain/real_costs.py` | first day to count (UTC); the default window is the last 30 days | no |
| `CUANTA_HELP_BUDGET_S` / `CUANTA_PAINT_BUDGET_S` | perf tests; set in `.github/workflows/ci.yml` | loosen perf budgets | yes |
| `cuanta probe cache-ttl` / `--gaps` / `--long` / `--budget-usd` / `--per-run-usd` / `--tools` / `--model` / `--keep` / `--no-save` / `--yes` | `src/cuanta/cli/commands/probe.py`, `application/cache_probe.py` | measure Claude cache expiry in a temporary project within a spend cap; without `--yes`, show the plan without spending | yes |
| pytest `addopts -m "not live"` | `pyproject.toml` | live tests excluded by default | yes |
| pytest `timeout` / `PYTEST_TIMEOUT` / `--timeout` | `pyproject.toml`, `tests/conftest.py`, `pytest-timeout` | bound ordinary tests and allow an override; live tests are unbounded unless explicitly marked | yes |
| pytest `--max-worker-restart=0` | `pyproject.toml`, `pytest-xdist` | fail after a crashed worker instead of restarting it | yes |
| `[tool.coverage.report] fail_under` | `pyproject.toml` | coverage floor on domain + application | no |
| extra `web` | `pyproject.toml` | installs `textual-serve` for `cuanta ui --web` | no |
| focus `--snapshot-update` | `scripts/dev/focus.py` | updates intended Textual snapshots only with explicit TUI test paths | no |
| trial `simple` (TOML) | `scripts/dev/spec.py`, `trial.py` | runs small probes without requiring Forge agents; incompatible with cross-engine mode | no |
| `cuanta index --rebuild` / `--status` | `cli/commands/index.py`, `application/code_index.py` | hashes source, styles and explicit documentation/config inputs into copy-local `.cuanta/index.db`; rebuild preserves an old database backup and never changes the ledger; status reads the stored inventory | no |
| Index structural coverage | `adapters/graph/index_ast.py`, `file_graph.py` | deterministic AST syntax for Python, TS/JS/TSX and Go, package verification scripts, fresh graph provenance; unsupported/invalid syntax explicitly reduces coverage; an existing stale medium-or-larger graph can refresh in a detached AST-only worker after executable health succeeds | no |
| Index knowledge freshness | `domain/index_rules.py`, `index_facts.py`, `adapters/system/index_knowledge.py` | section-scoped rulebooks, importing tests and verify commands, decayed per-task history, report findings and agent notes; report-end line hashes survive unrelated edits, changed anchors stay stored as stale for revalidation; historical reports without original source proof remain unverified; an existing ledger is read-only and incompatible schemas show unavailable history without migration | no |
| `cuanta find` / `card` / `impact` / `facts --stale` | `cli/commands/index_read.py`, `application/index_read.py` | local BM25/path/identifier search with graph and decayed-history reasons; bounded handling cards; fresh facts by default and stale records for revalidation | no |
| `find --rerank`, `instinct.share_paths` | `domain/config.py`, `application/index_read.py` | optional one-batch Jev rerank of 30–60 candidates, only with explicit path-sharing and remote consent; sanitized paths and symbol names, no code or free-form notes; deterministic fallback | no |
| `index --summaries`, global `--yes` | `application/index_summaries.py`, `cli/commands/index.py` | show estimate/model/paths before confirmation; one Claude economy batch with no tools, source-hash cache, no launch for unknown estimates or a padded cap above $0.25 | no |
| `cuanta plan --for`, `--type`, `--why`, `--where`, `--constraints`, `--out-of-scope` | `cli/commands/plan.py`, `domain/change_plan.py` | compile indexed edit/read/guard/verify sets with confidence, exact writer denies and zero spend; exclusions in English and Spanish; Confirm choices reach launch | no |
| `runs.read_discipline` | `domain/config.py`, `bootstrap.py`, `cli/hooks.py` | opt-in Cuanta PreToolUse/PostToolUse hooks, isolated from user/project settings with `--setting-sources` empty; unbounded large reads and whole-tree content grep denied, direct tests redirected through the gateway; off until installed-runtime contracts are proved | no |
| Strict protected runs | `application/engine_run.py`, `domain/agents.py`, `application/trials.py` | Claude guarded/read-only runs use explicit builtin tool definitions without unrestricted execution, writer/path denies in both settings and argv, sanitized agent JSON, and post-run path/hash/mode checks; Codex path protection remains best-effort; protected trial writes cannot be applied | no |
| `cuanta pack --for` / `--depth quick\|normal\|deep` | `cli/commands/pack.py`, `application/context_pack.py`, `domain/pack.py` | deterministic indexed cards, verified fresh facts and bounded numbered excerpts; 2,000/4,000/6,000 estimated-token budgets include headers and request; inclusion reasons and zero model spend; bounded process-memory cache hashes index content, request, depth, role and confirmed plan, without persisting private prompts | no |
| `cuanta mcp serve`, `--run-id` | `application/mcp.py`, `cli/commands/mcp.py`, `application/index_tools.py` | bounded stdio index tools: find, card, impact, facts, page, tests_for and anchored note; protocol-only stdout, negotiated lifecycle and clean EOF; call byte/token estimates and latency use an existing run ledger without storing query or source payload | no |
| `runs.index_tools` | `domain/config.py`, `bootstrap.py`, `application/engine_run.py` | opt-in owned Cuanta MCP server for lean or protected Claude launches, inherited by generated roles with index-first instructions; off by default until complete runtime permission coverage is verified; full unprotected sessions and Codex text fallback retain their prior behavior | no |
| `runs.index_enabled` | `domain/config.py`, `bootstrap.py` | controls automatic index refresh, context packs, learning and owned MCP loading; disabling it retains lexical change-plan protection and leaves explicit index commands available | no |
| `cuanta bench run --index on\|off` | `cli/commands/bench.py`, `application/bench.py`, `domain/bench.py`, `bootstrap.py` | persists index mode for code and investigation tasks; baseline remains off; reports exploration estimates, raw reads, index calls, acceptance and actual protected or out-of-plan edits | no |
| Map command palette / `g` | `tui/views/map.py`, `application/map.py`, `tui/commands.py` | local ranked search, handling cards, impact, fresh/stale facts, anchor revalidation and reversible Rebuild; indexed count, coverage, update time and semantic off are explicit; existing numeric navigation stays unchanged | no |
| Result / Spectrum index metrics | `domain/index_metrics.py`, `application/index_reporting.py` | owned Cuanta exploration calls divided by observed indexed and Read/Grep/Glob calls; returned tokens are estimates distinct from API consumption; failed executed attempts count, note/handshake do not; provider mirrors and repeated events are deduplicated; guards/out-of-plan paths use final manifest evidence | no |
| Automatic Map learning | `application/index_learning.py`, `bootstrap.py` | refresh report/history after final native metadata, sandbox recording and outcome changes; preserve copy notes only with verified source anchors, revalidate against the original source and retain changed facts as stale; reuse the index and readonly ledger history, with no duplicate priors store | no |

`cuanta spectrum --json` adds an `anatomy` key with exclusive start, exploration,
writing and handoff requests, per-agent totals and a heuristic description. Result
Consumption and Spectrum show the same phases. Cross-engine anatomy includes observed
child roles; existing Spectrum totals and grouping retain their selection scope.
`cuanta costs --json` adds `phase_medians` by task type and coverage. These summarize
observed priced requests, independently of the billed attempt totals. Missing events,
unknown request prices or a shortfall against a known positive turn count exclude an
attempt from all phase medians; an absent phase is zero only in a covered attempt.

Spectrum and `runs show --json` also expose `read_efficiency`: unique successful source reads,
cited/edited/useful paths, counts, availability and the exact formula. Investigations use
cited files among observed reads divided by files read; code work uses edited or cited files
among observed reads divided by files read. This file utilization v2 counts raw reads and
`cuanta.page`, excluding failed/pending calls and search/card metadata. Source and saved sandbox
copy roots normalize to the same logical paths. Missing pipeline reports, including historical
unknown shapes, retain heuristic v1: useful tool tokens divided by total API tokens. Both
versions display their formula. Missing read/report observations remain unavailable.

