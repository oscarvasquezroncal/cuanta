# Internal changelog — cuanta

**Budget:** unbounded, never auto-loaded. Holds the NARRATIVE — what shipped, why, what was tried
and rejected. What does NOT belong here: rules (`CLAUDE.md`), external facts
(`docs/GROUND_TRUTH.md`), open gaps (`docs/IMPROVEMENTS.md`), per-run telemetry
(`docs/RUN_LOG.md`).

**Entry format** (newest first):

```
## <YYYY-MM-DD> — <title>
- Shipped: <what>
- Why: <reason>
- Tried and rejected: <what, and why not>
- Rule extracted: <the one-line rule that went to CLAUDE.md, or "none">
```

---

## 2026-10-04 — H4: readable runs
- Shipped: shared es/en console catalogs and global language override, short launch/result output, streamed live status for native and per-role runs including isolated copies, Result details fold, common three-line failures and daily traceback logs. JSON retains a single final document. Known route differences remain technical detail; unexplained model changes stay visible.
- Why: the real-run result exposed internal diagnostics, interrupted the session with a traceback and obscured partial accounting and the stop reason.
- Tried and rejected: emitting the launch as an empty JSON document (regression test keeps the final result on stdout); repeating forecast notes after the launch card; one result row for every senior agent instead of aggregating its role.
- Rule extracted: none. Local milestone evidence and the deferred H5 boundary stay under `.cuanta/`.

## 2026-10-03 — H3: index and init, fast, honest and visible
- Shipped: the inventory, detection and snapshots honor `.gitignore`, `.git/info/exclude`, the global excludes and `.git/index` read as files (tracked files stay; virtualenvs found by `pyvenv.cfg` or `conda-meta`); a 20,000-file ceiling that stops before reading and prints the `[detect]` lines to paste; slow-step progress (`index · N files · T s`, `plan`, `forecast`) past two seconds; `index --rebuild` writing a new database and reclaiming space; a doctor size row for `.cuanta`; init that keeps an initialized Forge with no model, a background graph update, stage times and a total; `.cuanta/forge-suggested/` instead of `.new.md` siblings; gateway and ceiling gaps as warnings.
- Why: the real run's dry run was silent for 1.5 h (the old inventory read 23,922 candidates in 21.1 s before parsing on a shaped copy; the real tree held tens of thousands of virtualenv and checkout files), `.cuanta` stayed at 872.6 MB after a rebuild that kept the old index as a backup, and init took 40 minutes running Forge phases although Forge was initialized, then ended "finished with problems" for a missing gateway line and wrote four `.new.md` agents that the native team most likely loaded in place of the user's tuned ones. Measured after: the stop takes 0.62 s on a 23,802-file real-run-shaped tree, 802 files index in 7.2 s once excluded, and an initialized init returns in under a second while the graph updates in the background.
- Tried and rejected: asking git (`git ls-files`) for the file list (it breaks the README promise, fails in sandbox copies and on dubious-ownership repositories); vacuuming after every update; a config key for the ceiling; gating init on FORGE_STATE without the resume input; a background graph when Forge runs in the same init; installing differing kit files as siblings on the kept path; patching the vendored Forge text.
- Rule extracted: the index limit and git-file reading, and init keeping Forge and never writing beside the user's files (CLAUDE.md §7); slow steps and their timer thread (CLAUDE.md §8).

## 2026-10-03 — H2: long mandates in, from the console and the app
- Shipped: `--from`/`-f` on `mandate` and `queue add` (English and Spanish labels, or the first heading as the what and the rest as evidence); `cuanta run`, `feat`, `fix` and `audit`; `cuanta help`/`ayuda`; short forms with `-V` for verbose; `cuanta runs` and `runs show` defaults; grouped mandate help; optional fields filled with "none stated"; no truncation; the mandate template written on demand, `init --template` and a doctor/Home row; `Ctrl+C` that names only recorded runs; the app's file box, whole-mandate split, balanced Team options and an Auto chip.
- Why: the real run needed `--evidence mandato.md` with a placeholder `--what` (cmd's 8,191-character limit), the app's paste hit Windows Terminal's 5 KiB dialog, the first launch stopped on `docs/MANDATE_TEMPLATE.md not found → run cuanta init first` with all four agents installed, and `Ctrl+C` printed "nine lives: re-run to resume" with nothing to resume.
- Tried and rejected: another letter for `--variant` (the plan asks for `-v`, so verbose moved to `-V`); guessing the interrupted run from a time window (only ids the command's own container issued are named); writing the template only at `cuanta init` (the run would still stop); relaxing the app's short-story questions (the guided interview keeps "What must not change?"; whole mandates need only type and what).
- After review (five lenses, adversarial verification; 4 blockers): a file is split into fields only with a known TYPE, a WHAT or WHY label or a REQUEST block (one label-like line in prose had moved whole phases into OUT OF SCOPE and guarded the file to fix); `--why` with `--from` is added after the file's evidence instead of replacing it; a file with no type stays on the balanced team under auto; a `#` line in a short app story no longer makes it a whole mandate. Also: a stand-alone title rule, CommonMark fences, a strict UTF-8 reader, absolute queue paths, the mandate file left out of its own plan and pack, the template written only at launch (never by dry runs, previews or isolated copies, never through links), Ctrl+C settling every run it names, the orchestrator on the chosen main model, fast output on an Opus balanced team, and select labels that follow the profile (one placeholder value per profile, because Textual's `set_options` keeps the old label when the value does not change).
- Second review (three resolution checks, two regression sweeps, verified): no blockers; 64 first-review findings resolved; fixed the regressions the first fixes caused (a bare-fenced log or a fence-less copy of the template, a lone unknown TYPE line, Windows `~\` paths, the expanduser RuntimeError, 'Effort from depth' taking over the session profile, fast-output variants where the launch refuses them) and the app side of the request-file exclusion; a forced main-session model no longer teaches the router; a pre-engine error settles the run failed; queue run and init name their run on a late `Ctrl+C`; Home's next step never clips. A final check of every fix found two more edges (notes with a label and a fence after a template copy; `Type: integer` after a title), fixed by the lead.
- Rule extracted: `-V` is the global verbose and `-v` is `--variant` (CLAUDE.md §8); `-f` files are whole mandates and the template is written at launch (CLAUDE.md §7).

## 2026-10-02 — H1: nothing kills or starves a real run
- Shipped: the 15-minute launch timer is gone (`runs.repair_timeout_s` bounds repair rounds only); limits are opt-in (`--max-budget-usd`, `--max-turns`, `--max-wall`, `[limits]`, `[runs] limits = "depth"`, the app's Límites switch); runs cut before their result get a partial cost and turn count; the pack and the change plan read anchors from the whole request and the native scout gets the pack; phase-local read-only words no longer freeze a writing request; the docs decision reads the evidence and `--docs` overrides it; `--pure` plans pure; the listener never prints a traceback; settled steps and a one-line stop reason; the forecast counts the request and names its phases.
- Why: the first real mandate on a real Python backend (12 KB, five phases, anchors in the evidence) was killed at 15 minutes by a hidden repair timeout, started from an empty pack and an empty edit set (most likely a phase-local "solo lectura" that froze the change plan), ran with docs off and a scout on another model, and reported `cost n/a`, `turns 0` and a raw traceback after 11.9M tokens.
- Tried and rejected: halting a repair round when its time runs out (kills again what the user wanted finished); a depth default for the wall limit (the old 900 s timer was the bug); reading CONSTRAINTS and OUT OF SCOPE for docs (they say what must not change); guessing a forced pure model's tier from its name (the catalog knows it).
- After review (five lenses, adversarial verification): a wall halt after a settled result is dropped; team deadlines stop repair, resume and rotation launches (1 s floor); user stops win over kept results; per-role partial labels and totals; guards for unindexed out-of-scope files restored; phase-local prohibitions; a narrower docs rule in WHY; config pins keep auto balanced; the app's switch applies exactly the configured limits; the background listener's console output moved to `listener.out.log`.
- Second review (resolution check plus two regression sweeps, verified): every first-review finding resolved; fixed the pending-turn rule for governed runs, walled team repairs and resumes, partial costs in the trial and bench guards, kept per-role picks in the app, phase spans that end at the next heading of their level, a docs negation scoped to its verb, and per-record OTLP decode guards.
- Rule extracted: limits are opt-in; `runs.repair_timeout_s` bounds repair rounds only (CLAUDE.md §7).

## 2026-09-28 — Teams by provider
- Shipped: a team runs on one provider (Claude or GPT); per-tier defaults in `model_tiers.toml` (Claude haiku/sonnet/opus, GPT gpt-6-luna/gpt-6-sol/gpt-6-sol) win over the generic search; a codex pipeline mandate runs one launch per role through the per-role runner; pins to the other provider are refused with `route.pin_provider`; the tester asks for standard; Team offers provider chips, per-role cards with price per million tokens and a preview with each role's model; `docs/TEAMS.md` replaces `docs/MIXED_TEAMS.md`; `--mix`, its presets and the Windows rule that kept a cross tester on Claude are gone. After review: the CLI caps a GPT team with `--max-budget-usd`, the depth cap, `budget.usd` or $1.00 instead of the fixed `--cross-budget-usd` default, and decides per-role after the prompts complete the request; the app's Stop reaches per-role runs; `cuanta costs` files a single-provider per-role run under its provider; Team's provider count leaves out single-session Codex pipelines; trials treat a codex pipeline as per-role.
- Why: X3 measured the mixed presets above Claude only per accepted feature ($1.18 and $1.99 against $0.91), and without per-tier defaults a GPT team routed to the older `gpt-5.6-*` anchors.
- Tried and rejected: Codex native subagents for the GPT team (CX-11 verified, but child usage appears only in rollout files, so cost accounting would miss it); routing `cuanta loop --engine codex` fixes through the per-role runner (the loop's fix step needs one run id and cost; its fixes stay one session and the docs say so); letting a user tier override change the defaults (it could put a GPT team back on `gpt-5.6-*`; pin instead); keeping the Codex tester off Windows builds by moving it to Claude (that is mixing; the tester stays on Codex and is told not to run builds).
- Rule extracted: none.

## 2026-09-27 — Mixed teams on the landing page
- Shipped: X3 matrix of three frozen requests by three presets in sandbox copies; blocked roles stop the pipeline as partial and name the cause; roles after a partial handoff are told to continue; role history counts budget-stopped runs; native caps are recorded per role so the learned margin measures the right cap; Team recommends a preset from measured cost per accepted change.
- Why: the first Claude plans, Codex writes trial showed a Codex senior refusing a salvaged analyst handoff while later roles kept spending; the learned margin read the pipeline cap on root roles and measured no overrun.
- Tried and rejected: repeating the blocked trial (the remaining matrix cap could not absorb a Codex overrun); changing the margin model mid-matrix (a USD-based margin fits the observed one-request overshoot better, recorded for V5).
- Rule extracted: none.

## 2026-09-27 — Mixed teams that finish
- Shipped: structured, accumulating role handoffs with anchored facts and stale marks; cuanta-run verification between roles with one repair turn; role floors, history-based shares, learned soft caps, salvage handoffs and optional-role skips; guards after every role; Codex overrun accounting; readable new files for Codex writers on Windows; pins honored or rejected; `--mix` presets and Team cards; completion states in results and costs; index tools by default for pipeline roles.
- Why: in X1 and V4 the mixed pipeline stopped at the first role that hit its share, later roles rediscovered the analyst's work from plain text, only an agent could check builds, and file guards ran after the last role. V4's feature and fix pipelines had index tools and a connected server but called only `note`, because the index guidance came last in the prompt.
- Tried and rejected: running model-suggested verify commands (they run outside any sandbox); passing verification outputs into the recorded patch; lowering Codex's Windows sandbox mode to run builds (K1: unelevated mode still cannot spawn piped children).
- Rule extracted: none.

## 2026-09-27 — Compiled change plans and protected paths
- Shipped: local change-plan compiler and `plan --for`, confidence and indexed regression commands, bilingual exclusions, movable Confirm chips and Team counts, consistent override composition, strict Claude tool/settings/agent policies, actual edit/guard telemetry and refusal to apply protected trial edits. Snapshots cover styles, documents, hidden project paths and file modes; sandbox manifests remain authoritative.
- Why: preserve explicit exclusions through launch and detect real edits independently of model claims.
- Tried and rejected: unrestricted execution in guarded runs, inherited agent hooks/skills/MCP overrides, source-only change scans, and activation of unproved hook contracts. Read discipline is opt-in pending the installed CLI probe; native Windows has no verified Claude OS sandbox and managed policy may suppress hooks.
- Rule extracted: none.

## 2026-09-27 — Local handling cards and search
- Shipped: bounded file cards with current knowledge and source rules, BM25 identifier/path
  search with graph/history reasons, and find/card/impact/facts commands. Jev reranking is
  an explicit consented path/symbol metadata batch; tool-free Claude economy summaries
  require estimate and confirmation. Free-form notes are kept local to avoid sharing code.
- Why: agents need useful file context before spending on broad exploration.
- Tried and rejected: a default embedding dependency and download. MiniLM's English model
  and additional runtime do not yet have a verified local cold-start fit for bilingual use.
- Rule extracted: none.
## 2026-09-27 — Anchored index knowledge
- Shipped: section-scoped root and nested rulebooks, explicit documentation references,
  importing test links with verification commands, and deduplicated ledger history with
  outcome, retry and failure evidence. Reports and agent notes preserve original anchors.
- Why: a previous finding must not silently become a current fact after source changes.
- Tried and rejected: anchoring an old report to current content without historical proof;
  such records remain stale. Exact unchanged ranges can be revalidated after unrelated edits.
- Rule extracted: none.

## 2026-09-27 — Structural index sources
- Shipped: deterministic Python and Tree-sitter syntax extraction for TS, JS, TSX and Go,
  preserving symbol identities, directed imports/exports, routes, hooks, stores, services and
  environment reads. The richer graph import checks source freshness and keeps provenance.
- Why: the legacy file-neighbour graph loses edge direction and duplicate symbol identity.
- Tried and rejected: regex-only parsing and trusting cached graphs by Git revision. Unknown
  syntax and unresolved dynamic imports remain explicit coverage limitations.
- Rule extracted: none.

## 2026-09-27 — Durable incremental index inventory
- Shipped: pure index records and a port, copy-local SQLite storage with independent schema,
  hash provenance, recovery and safe source/documentation/style inventory. `cuanta index`
  updates only changed records; mandates refresh the inventory before launch.
- Why: source-only scanning misses non-source inputs and cannot safely enumerate index inputs.
- Tried and rejected: sharing the original project's index with an isolated copy, because
  different trees would overwrite each other's hashes. The ledger remains shared separately.
- Rule extracted: none.

## 2026-09-27 — The product today: six measured baseline trials
- Shipped: six sandbox audit/fix/feature trials, exact recovered requests and a frozen replay
  matrix. Public report keeps actual models, costs, cache, attempt/launch times and outcomes.
  Native trial role references now remove matching engine prefixes before routing, with
  command-through-routing regressions for Sonnet and Opus and rejection of engine mismatches.
- Why: the requested all-Sonnet pipeline silently used default Opus roles because qualified
  resolved IDs did not match the native catalog. The already-started series remains measured;
  its model contrasts are marked blocked, rather than relabelled as pure Sonnet.
- Tried and rejected: two GSAP patches passed external commands but failed unchanged source
  checks and lacked full runtime failure proof. No check was weakened and no patch applied.
  The six trials cost $5.08215255 against $8.00; four reports/patches accepted, two rejected.
- Rule extracted: none. Corrected V4 launches must label policy differences from misrouted E1.

## 2026-09-27 — Cross-engine budget reservations and measured estimates
- Shipped: proportional shares with a five-percent floor per active role, future-role reserves,
  unused-share carry, complete pipeline timing and calibrated shape/depth history estimates.
  Existing run metadata retains shape and calibration without a ledger migration. Team states
  Windows Codex build limitations and routes a cross-engine tester to Claude.
- Why: T1 exhausted the pipeline before later roles ran and understated total wall time.
  The raw Claude count still includes its terminal limit result; the UI explains it.
- Tried and rejected: inaccessible engine-created probe output is kept for recovery; no ACL
  change or broader sandbox grants are used. A user-owned pre-existing output file made the
  artifact readable. The Codex probe exceeded its remaining cap and no further probe ran.
- Rule extracted: none. Sandbox collection errors preserve costs and block application.

## 2026-09-26 — Real costs you can see
- Shipped: runs store the estimate shown at launch (low, high, source, samples) and the cap;
  every mandate and cross-engine pipeline gets an outcome (accepted, rejected, pending) through
  `cuanta runs accept|reject`, Result's Accept/Reject, or sandbox Apply/Discard; `cuanta costs`,
  a Home "Real costs" card and a Ledger costs table report spend, medians, cost per accepted
  change and the median estimate error.
- Why: the user must see what an audit, a fix and a feature really cost, and whether the
  estimate and the model mix paid off.
- Tried and rejected: a separate estimate setter on the ledger (the launcher's final full-row
  write would overwrite it); counting only accepted runs, as the bench does (it hides failed
  attempts); showing unknown costs as $0.
- Rule extracted: none.

## 2026-09-26 — Isolated copies (S1)
- Shipped: `cuanta mandate --sandbox`, trials with a git-format patch, apply with a drift check,
  discard, and the git hand-off.
- Why: agents can work on a real project without touching it until the user applies.
- Tried and rejected: a junction for node_modules (Turbopack rejects links out of the root);
  copying `.cuanta/` wholesale (the run belongs in the original ledger).
- Rule extracted: CLAUDE.md §7 sandbox bullet.
