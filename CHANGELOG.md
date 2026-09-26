# Changelog

All notable changes to cuanta are documented here.
This project follows [Semantic Versioning](https://semver.org/). The vendored
claude-agent-forge keeps its own changelog in `src/cuanta/assets/forge/CHANGELOG.md`.

## [Unreleased]

## [0.3.0] - 2026-09-26

Engine guarantees, honest cost reporting, cache and turn limits, and the first PyPI release workflow.

### Added
- Engine guarantees in Team and CLI: spend caps, turn limits, read-only behavior and telemetry
  are identified as enforced, checked after the run or unavailable. Cross-engine launches show
  each role's guarantees and warn before launching an engine that cannot enforce the cap.
- A tag-triggered PyPI Trusted Publisher workflow with separate verification, build and publish
  jobs, followed by installation of the published version on Windows, Linux and macOS.
- User-only `scripts\git\push.cmd --tag` checks the release version and changelog, a clean
  `main` matching the remote, successful CI for that exact commit, and absent local/remote tags
  before creating and publishing an annotated version tag.
- PyPI installation instructions, a security reporting policy, and bug, feature and pull request
  templates linked to the contribution workflow.
- `docs/CONTRACTS.md` records external contracts with their version, status, date and evidence.
- Claude mandates have a depth-based turn limit, configurable through `CUANTA_MAX_TURNS`,
  `runs.max_turns` or `--max-turns`; the ledger records the limit and turns used.
- Single-context Claude investigations limit built-in tool definitions. An M0 A/B probe on
  Claude Code 2.1.282 cut first-request tokens from 30,588 to 15,454.
- `cuanta probe cache-ttl` measures a bounded prompt-cache window in a temporary project;
  without `--yes` it previews the plan. Measurements record authentication mode, engine version,
  model and date in user config when the result is usable.
- Result and Spectrum show whether a Claude run's first request found a warm or cold cache;
  Team and Home show an estimated prefix window, or unknown without a usable measurement.
- A per-test pytest timeout and disabled xdist worker restarts make hung or crashed tests fail
  with diagnostic output.
- Health reports the size of the home `AGENTS.md` and its estimated share of a first request.
- Public agent instructions linking the project rules and guarded Git workflow.
- Repository Git workflow on `main`: setup, guarded commit and user-only push scripts,
  a POSIX hook stripping AI attribution, a guard against committing ZIP archives,
  and temporary-repository integration tests.
- Parallel milestone gates with pytest-xdist and serialized fixed-port listener tests.
- `cuanta instinct use NAME --global` sets a user-level backend, and `instinct show`
  identifies whether the effective value came from the project, user or default config.
- Instinct fallback reasons appear in decisions, the wizard, Result and `doctor`; setup
  checks cover missing keys and key/base URL mismatches.
- Graphify health runs its launcher and degrades graph-dependent prompts and tools when
  the launcher is broken.
- `cuanta bench run|report`: runs fixed tasks under three conditions (plain `claude -p`,
  cuanta with routing off, cuanta with routing auto) with the engine version and main model
  pinned, randomized order, a fresh repository copy per run and caps per run and per bench.
  Hidden acceptance tests decide each run; a capped run counts as not accepted. The report is
  Markdown plus SVG charts (tokens per accepted task, success rate, cost) with medians and
  ranges; `--readme` refreshes a README section. `bench/tasks` ships the five-task `mini` suite and the `full` suite, which adds two public
  repositories (boltons, more-itertools) fetched over HTTPS at pinned commits with a checked
  sha256.
- A read-only Benchmark screen in the app (command palette).

- Every run keeps its complete final report in `.cuanta/runs/<id>/report.md` (never truncated)
  next to `run.json`. A Result screen opens when a run finishes: status, type, duration, cost,
  the split between the fixed session context and your request, the report rendered as
  Markdown with clickable `file:line` references, the changed files with their diff, and
  consumption per agent. It saves the report to `docs/` (investigations go to
  `docs/investigations/`), copies it, continues with the report's NEXT request prefilled,
  opens Spectrum and exports a Markdown run report. The Ledger opens any run's result.
- Mandates never run the pipeline template without Forge agents. The guided flow offers
  "Initialize the project first" (with the usual cost of an init) or a clearly labeled simple
  mode (one agent, no project knowledge); the CLI takes `--simple`. The header and Home show
  whether Forge is installed.
- A clarity check before Next and Launch when a request is very general, with Improve with AI,
  Add details and Launch anyway. A low-clarity request is capped at the standard tier and never
  raises the scope.
- Instinct decisions carry the run they belong to; decisions made while typing or previewing
  are flagged as previews, repeated ones are deduplicated, and outcomes are recorded on the
  run's decisions (ledger schema v5).
- Claude investigation mode: read-only (Read, Grep, Glob and graphify; Write and Edit are
  denied), one architecture-analyst run or one agent in simple mode, and a structured report —
  summary, findings with `file:line`, risks, open questions, suggested next step. The vendored
  Forge mandate template gains the same investigation variant. Investigations route the
  analyst to standard and docs to economy, and open on the Report tab with "Save to docs/"
  first.
- The guided flow asks each intent its own questions: an investigation asks what to understand,
  the questions to answer, the deliverable and the folders in scope; a feature asks what it
  should do and its acceptance criteria; a refactor asks what should improve and its
  invariants. Each field shows one good example as its placeholder.
- Lean sessions (`runs.session = lean`, the default; `--session lean|full` per run): runs cuanta
  launches on Claude Code get only the MCP servers they need (none) through a generated
  `--mcp-config` with `--strict-mcp-config`, and a `--settings` file with `disableAllHooks` and
  every installed plugin disabled. Credentials are never touched. Codex and OpenCode keep
  their configured plugins and MCP servers; their sandbox and permission guarantees differ.
- The fixed session overhead — the first request's context, the plugins, MCP servers and hooks
  that loaded, a server that failed to connect, the startup time — shows on the Result screen,
  in Spectrum and in `cuanta spectrum`. Health warns about MCP servers that fail to connect, a
  user-level claude-agent-forge older than the vendored one, and a slow session start.
- `cuanta bench run --session lean|full|both` (lean by default) and `--task`; the report shows
  each condition per session profile with the first-request context of every run.
- `cuanta mandate` and `pounce` print the run's report after the summary (Markdown rendered in
  pretty mode, raw in plain, `report_text` in `--json`) and where it was saved.
- `cuanta runs list | show <id> [--markdown] | open <id>`: recent runs with a stored report, a
  run's report, consumption and session overhead, the human run report, and the app opened on
  that run's Result screen. Run ids accept a unique prefix.
- Exports: CSV is one table per file and "all tables" writes a ZIP with one CSV each, in UTF-8
  with a BOM for Excel; JSON is pretty-printed and leaves out the raw event payloads unless
  you ask for `--include-raw` (a checkbox in the app); `cuanta ledger export --all`.

### Changed
- Codex investigations and analyst roles explicitly use `read-only`; editing runs and roles
  use `workspace-write`, with no extra writable roots or writable temporary directories.
  Only temporary copies owned by cuanta skip Codex's Git repository check.
- OpenCode investigations and read-only analyst roles are refused with a reason: the verified
  permission profile permits shell write bypasses when graphify is allowed.
- Missing costs remain `n/a` in the ledger, Result and totals, and `null` in JSON exports.
  Known token prices produce labelled estimates; capped cross-engine, benchmark and retry
  flows stop before more work when remaining spend cannot be calculated. Historical ledger
  values are preserved because old zeroes lack enough provenance to reinterpret safely.
- OpenCode runs stop when reported step costs reach the cap, or a capped step omits cost,
  retaining the ending reason and reported usage. A step can exceed the cap before reporting.
- CI and release actions use verified Node 24 implementations and immutable action pins.
- Investigation prompts request the report in the request's language, and displayed reports
  begin at their first heading while the raw report stays available. New runs store whether
  the shape was a single context or a pipeline; ambiguous older runs display unknown.
- A visual pass on the app: buttons are three rows tall (primary filled, secondary quiet) with
  a visible focus ring; button rows wrap instead of clipping their labels; cards separate by
  surface color instead of borders; the header facts are compact chips; label/value facts wrap
  inside their own column. Home shows the large Michi only when the project is empty, and the
  next step is one line with one button. The mandate mode is a labeled "Guided | Expert"
  control, completed wizard steps are clickable with a "Step 2 of 4" label, and the wizard's
  text areas grow with their content. Loading panels show a skeleton instead of a blank area.
- The heuristic scope is type-aware: a short investigation without evidence of breadth is
  trivial or normal, never complex. Instinct moves a role away from your default only when it
  is confident, and the reason is shown.

### Fixed
- Codex usage retains the requested model for pricing. Catalog models have standard price
  rows; cumulative token totals and cache/reasoning subsets are counted once. Missing rates
  stay unavailable, and estimates are not presented as actual subscription billing.
- Team previews route within the selected engine, matching the launched model plan.
- CLI help defers output setup until command execution; the app applies its responsive
  classes before mounting, avoiding a redundant first-frame layout pass.
- Windows descendant discovery excludes terminated process objects whose handles remain
  open while preserving live descendants. SQLite migration tests close their connections.
- TUI tests await deferred focus and mounting work before shutdown, and process tests wait
  for children to finish, keeping exact assertions and timing budgets unchanged.
- POSIX process discovery excludes its own `ps` probe, preventing false orphan reports.
- CI tests cover graph tooling present and absent without relying on host installations,
  and verify repair commands for Windows, Linux and macOS explicitly.
- Wizard Launch and Preview validate required fields before opening the pipeline;
  investigation questions are extracted from Spanish and English enumerations, with
  the story as a fallback.
- The pipeline displays only roles that run, and Team offers installed engines while
  preserving per-role model choices.
- Closing the app immediately after launch no longer processes late input events;
  test caches and reports are routed outside the repository root.
- `cuanta models probe --yes` now spends: `--yes` is a global flag and was never reaching the
  command.

## [0.2.0] - 2026-09-24

Model routing with Jev, a prompt assistant, and a friendlier app.

### Added
- A model catalog per engine (`cuanta models list|refresh|tier|stats|probe`): Claude Code
  aliases and ids with the `availableModels` allowlist and default model from your settings,
  Codex from `codex debug models` and `~/.codex/config.toml`, OpenCode from
  `opencode models --verbose`. Tiers (economy, standard, premium, frontier) ship as data in
  `assets/model_tiers.toml`; models without an anchor are placed by price, and any tier can be
  overridden per project. The catalog is cached in `.cuanta/models.json`.
- Model routing: a `[routing]` policy (mode auto/fixed/off, engine order, per-role tiers, caps,
  presets save/balanced/best, `min_confidence`), Instinct questions per role with redacted
  state, escalation after a persistent failure, and a learning log that prefers the cheapest tier
  that keeps succeeding. `cuanta route --dry-run` explains every choice.
- Routed mandates: Claude Code receives the Forge agents through `--agents` with only `model`
  (and `effort`) changed, and the orchestrator through `--model`; Codex and OpenCode run a single
  model. `mandate`/`pounce` accept `--route`, `--preset`, `--role-model role=model` and
  `--override-env-model`, and print the team before launching.
- A routing audit after every routed run compares the planned model with the one telemetry saw
  per agent, explains mismatches, and shows up in the mandate summary, the app and
  `cuanta spectrum`.
- A Models screen in the app: the catalog with a tier editor, refresh and the opt-in probe;
  the routing editor (mode, preset, a tier and an "Instinct decides" switch per role, engine
  order, budget caps, frontier models); and what worked per task, role and tier.
- The Instinct screen shows the Jev connection (key, endpoint, model, latency, this week's
  spend, Test connection), decisions as sentences ("Mandate scope → normal (72%)") and exactly
  what a remote backend receives.
- A guided four-step mandate (intent, description, limits, team and cost) with a live clarity
  meter, one-click chips (use the last failure, pick a file, split the task, see an example),
  suggestions from the graph and the rulebook, an optional "Improve with AI" rewrite shown as
  a diff, per-agent model overrides and a p50–p90 cost estimate. Expert mode keeps the form.
- A first-run welcome (what cuanta does, your engines, a routing preset), `?` help for every
  screen, tooltips on the key controls, and clickable engine and listener chips.
- An experimental cross-engine pipeline (`mandate --cross-engine`): each role runs as its own
  headless run on its routed engine and hands a JSON capsule to the next, under its own cap.
- `cuanta test --affected`: runs only the tests related to files changed since the last full
  run (file-name heuristics plus the graphify graph), last failures first; falls back to the
  full suite with a stated reason. Every mandate ends with one full-suite verdict.
- Jev through OpenRouter: `TYPESAFE_BASE_URL` (with `TYPESAFE_API_BASE` as an alias), cost from
  `usage.cost`, and a connection check that asks one `noul` question instead of trusting
  `/v1/models`. Jev `score` questions are supported.
- Settings for the app background (`ui.background = solid|terminal`) and the listener
  (`ui.listener = auto|manual`); the header's listener chip starts and stops it with a click.
- `cuanta ui --web` serves the first frame and a Michi favicon.
- Terminal detection by capability (console window class, VT support, ancestors), with a
  `CUANTA_TERMINAL=modern|legacy` override and a doctor check.
- A root `LICENSE` and this changelog.

### Changed
- Prompts reach the engines through stdin (`claude -p`, `codex exec -`) or an attached file
  (`opencode run --file`), never the command line. Long `--evidence` is kept to about 8,000
  characters inline; the full text is stored as a capsule the prompt points to. A command line
  over 30,000 characters fails with a cuanta message before anything is launched.
- A run whose cost the engine does not report is estimated from the price table, or shown as
  `n/a` when the model has no price; it is never `$0.00`. The ledger schema moves to v4
  (nullable costs, routing decisions, routing audits).
- The app is fully translated: application results carry message ids that the app renders in
  English or Spanish, while the CLI keeps its English output.
- Home offers "New mandate" as the primary action once a project is initialized, with
  "Refresh knowledge" next to it; the listener is no longer suggested as the next step.

## [0.1.0] - 2026-09-22

First release.

### Added
- Token observability for Claude Code, Codex and OpenCode: OTLP listener, SQLite ledger,
  `cuanta spectrum` token maps, leaks and utilization, import of Claude transcripts.
- The test gateway (`cuanta test`) that clusters failures into signatures and keeps full logs
  in capsules (`cuanta cat`).
- Mandates (`cuanta mandate`, `cuanta pounce`), the fix loop, and Instinct decision backends
  (heuristic, Jev, LLM).
- `cuanta init` and `cuanta refresh` with the vendored claude-agent-forge 0.4.0.
- The Textual app (`cuanta ui`) in English and Spanish.
