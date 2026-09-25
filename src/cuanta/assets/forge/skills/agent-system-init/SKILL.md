---
name: agent-system-init
description: Standardize a repository for multi-agent development with Claude Code. Use this skill whenever the user wants to set up, bootstrap, initialize, or standardize a CLAUDE.md knowledge base plus a specialized subagent pipeline (architecture-analyst, senior engineer, tester, docs-updater) for a codebase — Python, JavaScript, TypeScript, Go, or any structured project. Trigger when the user mentions standardizing agents, initializing agents, "/init-agents", "/refresh-agents", setting up a CLAUDE.md system, generating per-directory CLAUDE.md docs, refreshing stale CLAUDE.md against real code, creating a CLAUDE.md from scratch when none exists, wiring a code graph for agent navigation, or building a repeatable agent workflow for features. Also trigger when a user has one big CLAUDE.md and wants it split into a root + per-directory structure with agents derived from the real architecture, when a large monorepo needs a batched or resumable standardization pass, or when they want the repo's agent loop specification written to what the project can actually verify.
---

# Agent System Init

Bootstraps a repository into a standardized multi-agent setup for Claude Code.

## The design decision this skill encodes

A knowledge base that describes structure grows monotonically and duplicates what a code graph
already knows, so **every session pays for structure twice**. The fix is not "replace CLAUDE.md
with a graph". It is a split by KIND of knowledge:

| | Graph (AST-derived) | Rulebook (`CLAUDE.md`) | Facts file (`docs/GROUND_TRUTH.md`) |
|---|---|---|---|
| Holds | what the code IS: symbols, calls, imports, inheritance, blast radius | what it SHOULD be and WHY: contracts, flag defaults, traps, proven-not-bugs | expensive derivations from EXTERNAL sources: framework internals, vendor API semantics |
| Derived | automatically, always | from experience; unrecoverable if lost | once, at real cost |
| Ages | no — re-index and it is current | yes — needs a ceiling and a budget | no — append-only, dated, cited |
| Cost | ~2k tokens per query | loaded in full, every message | read on demand |

> **The graph owns navigation. The rulebook owns judgment. The facts file owns expensive
> external derivations. Nothing is stored in two places.**

No AST parser can hold "this deprecated-looking call is a working compatibility shim — flagging
it CRITICAL is the bug", or "flag OFF must be byte-identical to legacy", or "never reload the
config module inside a test". That is institutional judgment; it is not in the syntax tree and
never will be. The rulebook exists for exactly that.

**The corollary, and the part that is easy to miss:** once a graph exists, this skill must
AGGRESSIVELY EMPTY the rulebook of everything structural — directory trees, file inventories,
call relationships, "who imports what". Generating a graph without removing what it replaces
produces a repo that pays for both.

## What it produces

1. A **rulebook** — a compact root `CLAUDE.md` (judgment, loaded every session) plus
   per-directory `CLAUDE.md` files holding **local rules only**.
2. A **code graph**, wired and indexed, when the repo is big enough to earn one.
3. A **fixed pipeline of 4 subagents** in `.claude/agents/`: `architecture-analyst` →
   `<lang>-senior` → `tester` → `docs-updater`.
4. **Supporting artifacts**: `docs/GROUND_TRUTH.md`, `docs/CHANGELOG_INTERNAL.md`,
   `docs/FLAGS.md`, `docs/MANDATE_TEMPLATE.md`, `AGENTS_GUIDE.md`.
5. **The harness, named, and the loop above it**: `docs/RUN_LOG.md` (observability),
   `docs/IMPROVEMENTS.md` (found gaps), `HISTORIAS.md` (the backlog, read-write), and
   `docs/LOOP.md` — the loop specification, written to the repo's real `VERIFY_TIER`.

## Harness and loop — the two layers this skill produces

What the four agents plus the artifacts add up to is a **harness**: tool orchestration,
verification, context and memory, guardrails, observability. Above it sits the **loop**: a
bounded specification of trigger, goal, verification, stopping rule, and memory, so one mandate
hands off to the next instead of restarting from a blank page.

| Layer | Implemented by |
|---|---|
| Tool orchestration | the four agents + the scope gate |
| Verification | the tester's bar, `VERIFY_TIER` |
| Context & memory | graph / rulebook / facts file / changelog |
| Guardrails | §Do not break, gate-OFF byte-identical, exceptionless boundaries |
| Observability | `docs/RUN_LOG.md` |

**The honest gate.** In any loop the verifier is the bottleneck, not the model. A repo that
cannot prove behavior does not get an unattended loop described for it — see Phase 6. Offering
autonomy to a repo without a sensor sells the exact failure mode the discipline exists to
prevent. **Refusing there is the feature.**

## Honest scope — state this to the user, do not oversell

This skill **standardizes context and enforces a consistent workflow**. It improves consistency
and reduces avoidable mistakes. It does **NOT** guarantee bug-free code, and it does not remove
the need for code review, tests, or version control. Frame it as "standardization +
consistency", never as "error-free development".

## Consumption budget

The goal is NOT minimum tokens; it is a **moderate, well-structured pass** that avoids a massive
crawl.

- Discover with **targeted reads**: manifests, config files, entry points, module indexes, and a
  representative SAMPLE per area — never every file.
- Once a graph is wired, **prefer graph queries over reads** for structural discovery. That is
  the whole point.
- For a large repo with no graph, use at most **2–3 focused exploration subagents** in parallel,
  each narrowly scoped. Small/medium repo: inline, no subagents.
- Never re-read a file already read. The init is ONE focused pass.
- **At `xl`, the pass is sampled and batched, not exhaustive** — Phase 1 verifies a stated sample,
  Phase 2B works in batches of ≤6 and **releases each batch's evidence before the next**. Holding
  a whole monorepo's evidence at once is the specific thing that makes the run fall over.

---

# The method — ten phases

Work in order: **0, 0.5, 1, 2A, 2B, 2.5, 3, 4, 5, 6.** **The run is autonomous — it completes
without stopping for approval.** It prints brief progress as it goes; the user reviews the result
and the diff afterward.

Every phase branches on the variables computed in Phase 0: **`SIZE_TIER`**, **`GRAPH_MODE`**,
**`DOCS_STATE`**, **`FORGE_STATE`**, and **`VERIFY_TIER`**.

**The run is resumable.** State is written to `.claude/forge-state.json` after every phase and
after every Phase 2B batch, and read at the start of Phase 0. A run that dies at Phase 3 resumes
at Phase 3 — it does not restart at zero. See §Run state below.

## Run state — `.claude/forge-state.json`

Run bookkeeping, not knowledge. It records where the run got to, never what it learned.

```json
{
  "version": "0.3",
  "started_at": "<iso>",
  "size_tier": "…", "graph_mode": "…", "docs_state": "…", "forge_state": "…",
  "verify_tier": "…",
  "phases_completed": ["0", "0.5", "1", "2A"],
  "dirs_written": ["src/api"], "dirs_queued": ["src/workers"],
  "dirs_rejected": [{ "path": "src/utils", "reason": "…" }],
  "unverified": [], "verify_markers": []
}
```

Rules:

- **Write it after every completed phase**, and after every Phase 2B batch. A phase is added to
  `phases_completed` only once its output is on disk.
- **Read it in Phase 0.** Exists with incomplete phases → **resume from the first incomplete
  phase**, announce `resuming from Phase <n>` in the detection summary, and do not redo completed
  work. All phases complete → this is a re-run; start fresh from Phase 0 and overwrite it.
- **Corrupt or unreadable → discard it with a note in the summary and run fresh.** Never a hard
  failure. A bookkeeping file must never be able to break the run.
- **Add `.claude/forge-state.json` to `.gitignore`** when `VCS` is a git working tree and the
  entry is absent. **File edit only — never a git command.**

---

## Phase 0 — DETECT

**Input:** the repository root, plus `.claude/forge-state.json` if it exists.

**Actions**
0. **Read `.claude/forge-state.json` first.** Present and valid with incomplete phases → this is
   a **resumed run**: adopt its recorded variables, skip every phase in `phases_completed`, and
   continue at the first incomplete one. Present but corrupt → discard, note it, run fresh.
   Absent, or all phases complete → fresh run.

   **Handed off by cuanta.** When the state file carries `"producer": "cuanta"` and lists both
   `"0"` and `"0.5"` in `phases_completed`, detection and the graph bootstrap already ran — in
   code, at zero model tokens. **Adopt its recorded variables as written**, print the nine-line
   summary from them with `run: resuming from Phase 1 (handed off by cuanta)`, and continue at
   Phase 1. Do not re-detect and do not re-run the graph bootstrap: that is work already paid
   for, and redoing it is the exact double-payment this skill exists to remove. Without that
   `producer` field this paragraph does not apply — the skill runs standalone, exactly as below.
1. Read the build manifest(s) — `package.json`, `pyproject.toml`/`requirements.txt`, `go.mod`,
   `Cargo.toml`, `pom.xml`/`build.gradle`, `composer.json`, `Gemfile`. Record: **language,
   framework, versions, package manager, test runner, entry points**. Versions come from the
   manifest and lockfile — they are authoritative.
2. Compute **`FILE_COUNT`** = count of source files, EXCLUDING vendor/deps/build artifacts and
   lockfiles: `node_modules`, `.venv`/`venv`, `vendor`, `dist`, `build`, `.next`, `out`,
   `target`, `__pycache__`, `*.egg-info`, `.git`, minified bundles, and every `*.lock` /
   `package-lock.json` / `poetry.lock` / `go.sum`.
3. Compute **`SIZE_TIER`** — four tiers: `small` if `FILE_COUNT < 100`; `medium` if `< 500`;
   `large` if `< 2000`; **`xl` if `>= 2000`**. `xl` is not "large with bigger numbers" — it
   changes the strategy of Phases 1 and 2B (sampled audit, graph-driven selection only). See
   §The `xl` strategy below.
4. Compute **`DOCS_STATE`**: `exists` (root + per-directory `CLAUDE.md`) | `partial` (root only,
   no per-directory) | `absent`.
4b. Compute **`FORGE_STATE`** — whether this repo has already been through the forge:
   - **`initialized`** — `.claude/agents/` holds forge-generated agents (a file named
     `architecture-analyst.md`, `docs-updater.md`, `tester.md`, or `*-senior.md`), **or** the
     support artifacts exist (`AGENTS_GUIDE.md`, `docs/MANDATE_TEMPLATE.md`,
     `docs/GROUND_TRUTH*.md`), **or** a `.claude/forge-state.json` from a completed run is present.
   - **`fresh`** — none of the above.

   **`initialized` changes what `/init-agents` does: it runs REFRESH semantics.** Phase 1 then
   verifies against **code and graph**, not against a rulebook a previous forge run wrote. This
   matters because a classifier that grades its own homework always passes: the previous run's
   output looks like `RULE` to it, nothing is ever stale, and the audit path is never exercised.
   Say which semantics are running, in the summary and in the closing summary.
4c. Compute **`VERIFY_TIER`** from the manifest scripts and dev dependencies — **what this repo
   can actually prove about itself**:
   - **`strong`** — a **test runner**, with or without a typecheck or build step (`pytest`,
     `pytest` + `mypy`, `jest`/`vitest` + `tsc`, `go test` + `go build`, …). A runner without a
     typecheck or build step is still `strong`, and the evidence says so verbatim:
     `pytest, no typecheck`. The runner is the sensor for behavior; the missing typecheck is a
     recorded gap, not a lower tier.
   - **`moderate`** — typecheck, lint, or build, but **no test runner**.
   - **`weak`** — build only, or nothing verifiable.

   Record the evidence (which scripts/deps decided it). This value is load-bearing: it fills the
   tester's contract in Phase 3 and **gates what Phase 6 is permitted to describe**. It is never
   inferred optimistically — a `test` script that only prints "no tests" is `weak`.
5. Compute **`GRAPH_MODE`** by filesystem evidence, in this strict priority order:
   - **`cli`** — a graph CLI is already present: `graphify-out/` exists in the repo, OR the
     binary resolves (`command -v graphify`).
   - **`mcp`** — an MCP graph server is already configured for this project: an entry in
     `.mcp.json`, `.claude/settings.json`, or `~/.claude.json` whose command or name matches a
     graph server (`graphify-mcp`, `code-graph*`, `*-graph-server`).
   - **`none`** — no graph tooling detected.
6. Compute **`VCS`** — whether a `.git` directory exists. Used **only** to decide whether to
   write ignore rules. **Never run a git command.**

**Output** — exactly nine lines to the console, no questions:

```
stack:      <language> <version> / <framework> <version> (<package manager>, <test runner>)
files:      <FILE_COUNT> source files → SIZE_TIER=<small|medium|large|xl>
docs:       DOCS_STATE=<exists|partial|absent>
forge:      FORGE_STATE=<fresh|initialized>[ — running refresh semantics]
graph:      GRAPH_MODE=<cli|mcp|none>
verify:     VERIFY_TIER=<strong|moderate|weak> — <the scripts/deps that decided it>
vcs:        <git working tree | no vcs>
entry:      <entry point path(s)>
run:        <fresh run | resuming from Phase <n> | state file discarded: <reason>>
```

**Write `.claude/forge-state.json`** before leaving this phase, naming all five variables
explicitly — `size_tier`, `graph_mode`, `docs_state`, `forge_state`, **`verify_tier`** — plus
`phases_completed: ["0"]`. `verify_tier` is the value `/refresh-agents` compares against to decide
whether the loop spec must be rewritten; omit it and that comparison has no reference.

### The `xl` strategy — what changes at ≥2000 source files

`xl` exists because every phase below was written assuming the repo fits in one pass. At `xl` it
does not, so three things change and they change in kind, not degree:

1. **Phase 1 audits by SAMPLING, not exhaustively.** Verify the root rulebook's claims against a
   representative sample plus everything the graph flags as high-degree, and **state the sample
   size in the gap summary**. An honest partial audit that finishes beats a complete one that
   cannot.
2. **Phase 2B selects directories from the GRAPH ONLY** — clusters/communities and
   cross-directory edge density. Do **not** walk the tree looking for candidates.
3. **The per-run cap of 20 rulebooks applies hard**, and the overflow is queued and reported.

**Failure behavior:** no manifest found → infer the stack from file extensions and directory
shape, mark the stack line `[UNVERIFIED]`, and continue. `VERIFY_TIER` with no manifest is
`weak` unless a CI config proves otherwise. Detection never blocks the run.

---

## Phase 0.5 — GRAPH BOOTSTRAP

**Input:** `SIZE_TIER`, `GRAPH_MODE`, and `VCS` from Phase 0, plus
`reference/graph-integration.md` — read it first; it holds the verified command surface.

This is the "one command" the user experiences. Branch strictly; do exactly one of these.

### Branch precedence — the size gate sits ABOVE mode detection

More than one branch condition can hold at once: a repo can be `small` *and* have graphify on the
PATH. Evaluate in this fixed order and take the **first** match:

| Order | Condition | Branch |
|---|---|---|
| 1 | `SIZE_TIER = small` | **A — skip.** Wins over C and D even when a graph CLI or MCP server is present. |
| 2 | `GRAPH_MODE = mcp` | B — defer to the server |
| 3 | `GRAPH_MODE = cli` | C — re-index |
| 4 | `GRAPH_MODE = none` and `SIZE_TIER` ∈ {medium, large, xl} | D — install + index |

**Always say which condition won**, not just the outcome. A run that prints
`graph: skipped (small tier; graphify present but not indexed for this repo)` is legible. A run
that prints `GRAPH_MODE=cli detected → skipped (small tier)` looks like a bug even when the
behavior was right.

### Branch A — `SIZE_TIER=small`

Do **NOT** install or wire any graph, **even when `GRAPH_MODE` is `cli` or `mcp`** — the size gate
outranks mode detection. Record which condition won and skip to Phase 1.

Write into the generated rulebook, verbatim:

> **No code graph, deliberately.** Below roughly one hundred source files a pre-built index does
> not pay for itself: the query costs more than the grep it replaces, and the index goes stale
> between uses. Agentic grep is the correct tool at this size. Revisit if the repo passes ~100
> source files.

This restraint is a feature. Recommending infrastructure that does not pay is how a tool loses
trust.

### Branch B — `GRAPH_MODE=mcp`

Do **NOT** install anything. Do **NOT** write navigation command syntax into the rulebook.

An MCP graph server delivers its own usage instructions to the agent at session start;
duplicating them in the rulebook is the exact double-payment this upgrade exists to remove.

Write only the ONE behavioral rule the server cannot supply:

> **Trust the graph's edges.** The graph MCP server's edges are AST facts. Do not read source to
> confirm an edge the graph asserts. Read source only to modify it, to resolve a runtime branch
> the graph cannot see, or when the graph is silent.

### Branch C — `GRAPH_MODE=cli`

**Reached only when `SIZE_TIER` is not `small`** — Branch A outranks this one.

graphify is already present. **Do not reinstall.** Run the index/update:

```bash
graphify update .
```

Write the full navigation contract into the rulebook (Phase 2A, §Navigation) with the verified
query surface from `reference/graph-integration.md`.

### Branch D — `GRAPH_MODE=none` AND `SIZE_TIER` is `medium`, `large`, or `xl`

**Reached only when `SIZE_TIER` is not `small`** — Branch A outranks this one. At `xl` the graph
is not optional in practice: Phase 2B's directory selection is graph-driven only there, so a
failed install there degrades Phase 2B to "no directory qualifies", which must be reported.

Install and initialize in a single non-interactive step, then index:

```bash
uv tool install graphifyy || pip install graphifyy   # package is graphifyy; binary is graphify
graphify update .                                    # builds from scratch, then incremental
```

Both commands are verified against graphify 0.9.11 (see `reference/graph-integration.md`).
`graphify update` needs no LLM, no API key, and no network beyond the install itself.

**Failure behavior — DEGRADE GRACEFULLY.** If installation or indexing fails for any reason
(network, permissions, unsupported language, non-zero exit):
1. Set `GRAPH_MODE=none` and continue the **entire** pipeline.
2. Write grep-based navigation rules into the rulebook instead of graph rules.
3. Record the failure verbatim and report it in the Phase 6 closing summary.

**A failed graph install must never abort the skill.** A graph is an accelerator, never a
dependency.

### In every branch where a graph ends up wired (C and D)

1. **Ignore the artifacts.** If `VCS` is a git working tree and the entry is absent, append
   `graphify-out/` to `.gitignore` — alongside `.claude/forge-state.json`, which every branch
   ignores. **File edit only — never a git command.**
2. **Write the re-index instruction** into the rulebook §Navigation *and* into the generated
   docs-updater agent, so the graph cannot silently go stale:
   `graphify update .` after any change that adds, removes, moves, or renames a symbol or file.

   A stale graph is worse than no graph: agents distrust it and re-explore, paying for both.

**Output:** one line, naming the condition that won —
`graph: <installed|updated|skipped (small tier; <what was present>)|deferred to MCP|FAILED — reason>`.

Then update `.claude/forge-state.json`: append `"0.5"` to `phases_completed` and record the final
`graph_mode`.

---

## Phase 1 — AUDIT or SEED

### The `FORGE_STATE=initialized` routing — read this before choosing a path

When Phase 0 computed `FORGE_STATE=initialized`, the `CLAUDE.md` on disk was **written by a
previous forge run**. Re-classifying it is the classifier grading its own homework: everything it
wrote reads back as `RULE`, nothing is ever `STALE`, and the audit path is never really exercised.

So `/init-agents` on an initialized repo runs **refresh semantics**:

- Verify every claim **against code and graph**, never against the document that asserted it.
  A claim's presence in a forge-written file is not evidence for it.
- Treat forge-generated boilerplate — empty §Proven NOT bugs, the "no local gotchas recorded yet"
  line, the deliberate-no-graph paragraph — as **structure of the format**, not as content to
  classify. It is neither `RULE` nor `STALE`; leave it.
- **Never overwrite a tuned agent or an existing per-directory rulebook** (Phase 3's `.new.md`
  policy and Phase 2B's update-in-place policy both stand).
- Say it in the summary: `forge: initialized — running refresh semantics`.

`FORGE_STATE=fresh` runs the AUDIT/SEED split below exactly as written.

### AUDIT — when `DOCS_STATE` is `exists` or `partial`

**Input:** every existing `CLAUDE.md`, plus companion docs (ARCHITECTURE.md, audits, READMEs)
treated as **claims to verify, not truth**.

**Actions**
1. Read every existing `CLAUDE.md` in full. **At `SIZE_TIER=xl`, audit by sampling** — see below.
2. Verify every claim against reality. **Where a graph is wired, verify structural claims via
   graph queries rather than by reading files** — `graphify query`, `graphify explain`,
   `graphify affected`. Read source only for claims the graph cannot settle.
3. Classify **every existing line** into exactly one bucket:

   | Class | Meaning | Action |
   |---|---|---|
   | `RULE` | judgment: a contract, a trap, a convention an agent could violate | **keep** |
   | `STRUCTURE` | directory trees, file inventories, call relationships, "who imports what" | **delete** — the graph owns it (keep only if `GRAPH_MODE=none`) |
   | `NARRATIVE` | the story of a past change, a migration diary, a "we did X because Y" | **relocate** to `docs/CHANGELOG_INTERNAL.md` |
   | `STALE` | verified false against current code | **delete** |
   | `UNVERIFIABLE` | plausible, not confirmable from code | **keep**, mark `[UNVERIFIED]` |

4. Harvest any externally-derived facts already recorded (framework internals, vendor API
   semantics) — they feed Phase 2.5.

#### `SIZE_TIER=xl` — audit by sampling

Verifying every claim in an `xl` repo does not finish. Instead:

- Verify claims against a **representative sample** — the entry points, one module per top-level
  area, and the directories the graph reports as **high-degree** (they carry the most consequence
  when a claim about them is wrong).
- **State the sample size and what it covered** in the gap summary, e.g.
  `audited by sampling: 38 of ~420 claims — entry points, 9 high-degree modules, 1 module per area`.
- A claim outside the sample is neither kept-as-verified nor deleted: it stays and is marked
  `[UNVERIFIED]`. **Never report a sampled audit as complete.**

An honest partial audit beats an unfinishable complete one; silently presenting one as the other
is the only failure here.

### SEED — when `DOCS_STATE=absent`

**Actions**
1. Infer a base from the code: manifests, entry points, folder names, a sample of files, plus
   any context the user gave in their invocation.
2. For facts the code cannot reveal and the user did not supply — deploy target, business
   intent, known-fragile areas — **do NOT stop to ask.** Write the best inference and mark it
   `[UNVERIFIED]`.
3. Sections with no evidence yet (notably §Proven NOT bugs) are generated **empty with their
   instructions**, not omitted and not padded.

**Output:** a short gap summary — counts per class, undocumented areas found, claims deleted as
false, and at `xl` the sample size. **Do not ask what to do about it — apply the classification.**
Then update `.claude/forge-state.json` (`phases_completed` += `"1"`, plus any `unverified` items).

**Failure behavior:** a `CLAUDE.md` too large or malformed to classify line-by-line → classify by
section, note the coarser granularity in the summary, continue.

---

## Phase 2A — WRITE THE ROOT RULEBOOK

**Input:** the Phase 1 classification, `SIZE_TIER`, `GRAPH_MODE`.

**Actions:** write the **root** `CLAUDE.md` per `reference/claude-md-spec.md`. Read that spec
before writing. Per-directory files belong to Phase 2B and are not written here.

**Three hard rules — enforced by this skill AND stated inside the generated file so they
survive after the skill is gone:**

1. **300-line ceiling on the root file.** Over it, content moves out; nothing is added. The
   destinations are the facts file, `docs/FLAGS.md`, `docs/CHANGELOG_INTERNAL.md`, and the
   per-directory rulebooks. The relocation order and its terminating end are below.
2. **Zero drifting numbers.** No test counts, migration revision numbers, rule counts, command
   counts, prices, or dates-of-verification. Where a count matters, write **the command that
   asks the code**: `alembic heads`, `pytest --collect-only -q | tail -1`, `<cli> --help`. This
   single rule is what keeps a knowledge base from quietly lying six weeks later.
3. **Per-directory files hold LOCAL RULES ONLY.** If a per-directory file would only describe
   *what lives in that folder*, and a graph is wired, **it must not be created at all.**

Documentation only during init — do not modify source, configs, or tests.

### The overflow path — deterministic, and it always terminates

"Over the ceiling, move content out" has no defined end if relocation runs out of destinations.
Without one, a run can stall on a size rule. It must not. Execute exactly this:

1. **Check the line count.**
2. **Over 300 → relocate, in this fixed priority order**, stopping as soon as the file is under:
   1. **structure** → **delete** (the graph owns it)
   2. **narrative** → `docs/CHANGELOG_INTERNAL.md`
   3. **the full flag registry** → `docs/FLAGS.md`
   4. **external derivations** → `docs/GROUND_TRUTH*.md`
   5. **single-directory rules** → that directory's rulebook
3. **Re-check the line count.**
4. **Still over after the order is exhausted → WRITE THE FILE ANYWAY.** Insert this line directly
   under the title, verbatim, and report it in the closing summary:

   > ⚠ Over ceiling: `<n>`/300 — relocation exhausted. Split candidates: `<the sections>`.

**A size rule must never cause a run to stall, refuse, or leave a repo half-written.** The same
principle governs the 40-line per-directory cap in Phase 2B. A ceiling is a budget signal, not a
gate: a repo with an over-ceiling rulebook and a visible warning is strictly better off than a
repo with no rulebook at all.

**Output:** the root rulebook path and its line count against the 300 ceiling, plus what was
relocated and where. Update `.claude/forge-state.json` (`phases_completed` += `"2A"`).

---

## Phase 2B — WRITE THE PER-DIRECTORY RULEBOOKS

**Input:** the graph (when wired), the Phase 1 classification, and the root rulebook just written.

Rule 3 of Phase 2A stands and is not weakened here. What this phase adds is the missing
derivation: **architecture yields RULES, and they must be derived rather than skipped.** On a
SEED run there is no accumulated judgment yet, so a naive reading of rule 3 excludes every
directory and the run ships no per-directory rulebooks at all — a repo standardized "by
architecture" with nothing architecture-shaped in it. The fix is not to fill those files with
structure. It is to write the right-hand column of this table and never the left:

| Structure — the graph owns it, never write it | Rule — the rulebook owns it, derive it |
|---|---|
| `api/` contains `routers/`, `services/`, `db/` | business logic lives in `services/`; routers stay thin |
| `worker/processor.py` imports `packages/core` | `worker/` may not import from `api/` — the boundary is one-way |
| this module has 14 functions | every I/O function here is async; sync I/O is wrapped |
| `tests/api/` exists | run only this subsystem's tests with `<command>` |

Both columns are derivable at init. Only the right column may be written.

### Step 1 — Select the directories that earn a rulebook

Use the graph's structure where available — clusters/communities, cross-directory edge density,
high-degree nodes — together with the folder layout.

**At `SIZE_TIER=xl`, selection is GRAPH-DRIVEN ONLY:** clusters/communities and cross-directory
edge density decide the candidate set. **Do not walk the tree looking for candidates** — a tree
walk at that size costs more than the phase is worth and returns mostly noise. No graph wired at
`xl` → no candidates qualify; say so explicitly with that reason rather than falling back to a
walk.

A directory earns a rulebook when **any** of these holds:

1. **It is a cohesive boundary** — dense internal edges, few external ones — and it holds enough
   code to matter.
2. **Its conventions provably differ from the root's** — a different schema library, a different
   error-handling shape, sync where the rest is async, its own test layout.
3. **It is a seam** — it wraps an external system, or everything inside it is reached through one
   entry point.
4. **It has its own run or test command** distinct from the project-wide one.
5. **(AUDIT path only)** the prior docs already carried local rules for it that Phase 1
   classified as `RULE`.

A directory does **not** earn one when:

- it is generated or vendored — `node_modules`, `.venv`/`venv`, `vendor`, `dist`, `build`,
  `.next`, `out`, `target`, `__pycache__`, `*.egg-info`, asset folders;
- it shares the root's conventions with nothing of its own;
- the only content available would be an inventory.

### Step 1b — Batch the work, and cap it

A monorepo falls over here specifically: holding every selected directory's evidence in working
context at once is what makes the phase unfinishable. So it is processed in batches.

- **Batches of at most 6 directories.** For each batch: derive the content (Step 2), **write the
  files**, then **update `.claude/forge-state.json`** — `dirs_written` grows, the batch's entries
  leave `dirs_queued` — and **release that batch's evidence from working context before starting
  the next batch.** Never carry batch N's evidence into batch N+1.
- **Hard cap: 20 per-directory rulebooks per run.** Qualifying directories beyond the cap are
  written to `dirs_queued` in the state file, **reported in the closing summary**, and picked up
  by the next `/refresh-agents`, which drains the queue.
- A queued directory is a **stated outcome**, never a silent omission. `queued: 7 directories
  qualified beyond the 20-rulebook cap — /refresh-agents drains them` is legible; silence is not.

Because the state file is written after every batch, a run that dies mid-phase resumes at the
next unwritten batch rather than redoing the ones already on disk.

### Step 2 — Derive the content, from evidence only

For each selected directory write at most these five parts, in order. **Omit any part with no
evidence — never pad:**

1. **Purpose** — one line: what this subsystem is responsible for. Not what it contains.
2. **Local rules** — conventions that hold HERE and are not in the root file. Derived by
   comparing this directory's observable patterns against the root rulebook's §Conventions.
   Inferred-but-unproven rules are marked `[UNVERIFIED]`.
3. **Boundaries** — what this directory may and may not reach, and what may reach it.
   **Only exceptionless patterns.** Where a graph is wired, verify with a graph query rather
   than by reading, and state **the direction, not the edge list**.
4. **Local commands** — how to run or test just this subsystem, taken verbatim from the
   manifest, CI config, or test layout. `[VERIFY]` if not confirmable.
5. **Local gotchas** — on an AUDIT run, the `RULE`-classified items that belong here. On a SEED
   run this is expected to be **empty**, and is written as an empty section with one line
   inviting accumulation, verbatim:

   > No local gotchas recorded yet — the docs-updater appends here as they are found.

   An honest empty section is the point; it is where this file grows.

**A boundary rule may only be written when the pattern is EXCEPTIONLESS.** If the graph shows
`worker/` importing `api/` even once, "worker must not import api" is not a rule — it is a
tendency, and writing it as a rule means the next agent "fixes" working code. Exceptionless or
unwritten. There is no third option and no hedged phrasing that rescues it.

**Cap: 40 lines per file.** Over it, the excess is almost always structure the graph owns — cut
it, do not raise the cap. **If it is still over 40 after cutting every piece of structure, write
the file anyway** with the same warning line the root file gets
(`⚠ Over cap: <n>/40 — <what could not be cut>`) and report it. Like the 300-line ceiling, this
cap never blocks a write.

### Step 3 — Report the selection, including the rejections

Output the directories that earned a rulebook **and** the ones that did not, each with its
reason. A user who sees `utils/ — skipped: shares root conventions, no boundary of its own`
understands the system is working. A user who sees silence assumes it is broken.

**Re-run policy:** an existing per-directory `CLAUDE.md` is **never overwritten**. On a SEED run,
a directory that already has one is left untouched and reported as preserved. On an AUDIT run,
it is **updated in place** following the Phase 1 classification — a different operation, and the
only one permitted to modify an existing file.

**Output:**

```
per-directory:  written  → <path> (<n> lines) ...   [batch <i>/<n>]
                preserved→ <path> — existing file, not overwritten
                skipped  → <path> — <reason>
                queued   → <path> — beyond the 20-rulebook per-run cap
```

**Failure behavior:** no directory qualifies — a genuinely flat repo, or `xl` with no graph — →
**write nothing**, and say so explicitly in the closing summary with the reason. Never create
empty files to look productive.

---

## Phase 2.5 — FACTS FILE

**Input:** externally-derived facts harvested in Phase 1.

**Actions**
1. Create `docs/GROUND_TRUTH.md` — named `docs/GROUND_TRUTH_<FRAMEWORK>.md` when a dominant
   framework was detected (e.g. `GROUND_TRUTH_DJANGO.md`).
2. Seed it with the Phase 1 harvest, converted to the entry format:

   ```
   <claim> — <source path>:<line> — <date> [superseded: <reason>]
   ```

   Grouped by subject.
3. **Append-only.** An entry is never rewritten, only superseded by a new dated line. The old
   line stays, so the reasoning trail survives.
4. Write its purpose at the top: *every reading of an external source tree (framework internals,
   vendor SDK, spec) is paid once and cited forever.*

**Output:** the file, with the seeded entry count. Update `.claude/forge-state.json`
(`phases_completed` += `"2.5"`).

**Failure behavior:** nothing to seed → create the file with its header, purpose, and format,
and one line stating it is empty. An empty facts file with a stated contract is the point; the
analyst starts appending to it on the first real task.

The analyst template **must consult this file BEFORE deriving anything** (Phase 3).

---

## Phase 3 — GENERATE AGENTS

**Input:** `templates/architecture-analyst.md`, `templates/senior-engineer.md`,
`templates/tester.md`, `templates/docs-updater.md`, plus everything detected and written so far.

**Actions**
1. Fill every placeholder from the rulebook just produced, by name:
   `{{PROJECT_NAME}}`, `{{STACK_SUMMARY}}`, `{{SENIOR_NAME}}`, `{{ARCHITECTURE_SUMMARY}}`
   (§Architecture map, or the one-line "query the graph" pointer when §3 was omitted),
   `{{CONVENTIONS}}` (§Conventions), `{{DO_NOT_BREAK}}` (§Do not break),
   `{{SANITY_CHECK_CMDS}}` and `{{TEST_CMD}}` (§Commands, verbatim),
   `{{TEST_STACK}}` / `{{TEST_LANDSCAPE}}` (the detected runner, layout, and fixtures),
   `{{TEST_GOTCHAS}}` (§Repo traps).

   **When `.cuanta/` exists at the repo root**, the repo runs its tests through the cuanta
   gateway: fill `{{TEST_CMD}}` with `cuanta test --json`, and in the tester's truncation section
   replace the `| tail -n 40` rule with this one, verbatim: *the gateway output is already
   bounded; never pipe it.* `cuanta test --json` returns one failure line per signature and keeps
   the full log in a capsule the tester reads with `cuanta cat <capsule> --level L2`. Piping it
   through `tail` can only cut the one part that matters. Add this iteration rule to the tester,
   verbatim: *while iterating on a phase, run `cuanta test --affected --json` (only the tests
   related to what changed, last failures first); the full `cuanta test --json` decides the
   final verdict, and cuanta runs it once more at the end of the mandate before reporting green.*
   Without `.cuanta/`, fill
   `{{TEST_CMD}}` from §Commands as above.
2. **Fill the navigation contract to match `GRAPH_MODE`.** The same template must produce a
   graph-first agent when a graph is wired and a disciplined, budgeted grep-based agent when it
   is not. Two placeholders carry this — fill both from the table below.

   **`{{NAVIGATION_CONTRACT}}`** (analyst):
   - `cli` → the verified query surface from `reference/graph-integration.md` (`graphify query`,
     `explain`, `path`, `affected`), each with one line on when to reach for it.
   - `mcp` → "Use the graph MCP server's own tools; it supplies their usage at session start.
     Do not restate their syntax here." Nothing more.
   - `none` → "No graph is wired. Navigate with Glob/Grep from the entry points in
     `CLAUDE.md` §Architecture map."

   **`{{BLAST_RADIUS_METHOD}}`** (analyst):
   - `cli` → ``Run `graphify affected "<symbol>" --depth 2` for every symbol the change touches,
     and `graphify explain "<symbol>"` for its role. The output IS the blast radius — do not
     re-derive it by reading.``
   - `mcp` → "Use the graph server's reverse-dependency / callers tool for every symbol the
     change touches. Its output IS the blast radius — do not re-derive it by reading."
   - `none` → "**Bounded search budget: {{READ_BUDGET}} searches.** For each symbol the change
     touches, grep for its definition and for its call sites, then stop. **State exactly what you
     searched** — the patterns and the paths — in the `open_questions` field, so the senior and
     tester know the edge of what was checked instead of guessing at it. Exhausted budget with an
     incomplete picture → `needs_scoping`. Never widen the search to compensate."

   The same three-way branch fills `{{REINDEX_INSTRUCTION}}` in the docs-updater
   (`cli` → run `graphify update .`; `mcp` → the server re-indexes itself, do nothing;
   `none` → "n/a") and `{{GRAPH_OWNS_CAVEAT}}` (empty when a graph is wired; when not,
   ", or belongs in a per-directory rulebook — there is no graph here").
3. Fill the remaining path placeholders from what was actually written:
   `{{FACTS_FILE}}` → the Phase 2.5 path, `{{ROOT_RULEBOOK}}` → the root `CLAUDE.md` path,
   `{{BACKLOG_FILE}}` → `HISTORIAS.md`, `{{COUNT_CMD_EXAMPLE}}` → a real counting command for
   this stack, `{{READ_BUDGET}}` → 8 unless the repo justifies otherwise,
   `{{DEPLOY_STATE}}` → whether the codebase is deployed/stable or greenfield.
3b. **Fill `{{VERIFY_TIER}}` and `{{VERIFY_TIER_CONTRACT}}` in the tester** from Phase 0's
   `VERIFY_TIER`. `{{VERIFY_TIER}}` is the literal word (`strong` | `moderate` | `weak`).
   `{{VERIFY_TIER_CONTRACT}}` is what this repo's tester can honestly claim:
   - `strong` → "This repo has a test runner. Your green is evidence about **behavior**. Run it,
     and run the typecheck/build step too when the repo has one; report both. When the evidence
     says `no typecheck`, state in your report that types are unchecked."
   - `moderate` → "**This repo has no test runner.** You can prove the code compiles, typechecks
     and lints — you **cannot** prove it behaves. Say exactly that in your report; never write
     `green` as if behavior were proven. Return `docs_impact.trap_found` entries about coverage
     gaps you hit, and expect the missing runner to be the top open item in
     `docs/IMPROVEMENTS.md`."
   - `weak` → the `moderate` text, plus: "There is effectively **no sensor** on this repo. Every
     claim you make is about the build, not the product. Installing a runner outranks any test
     you could write today."

   A `moderate` or `weak` tier makes **"install a test runner" an automatic entry** in
   `docs/IMPROVEMENTS.md` — seeded by Phase 6, not optional.
4. Name the senior agent after the stack (`backend-senior`, `frontend-senior`, `<lang>-senior`).
   The file name MUST match its `name:` frontmatter.
5. Model tiers: analyst `sonnet`, senior `opus`, tester `sonnet`, docs-updater `haiku`.
6. Write the four files to `.claude/agents/`.

**Re-run policy — never overwrite a tuned agent.** If an agent file already exists and differs
from what would be generated, write the new version to `<name>.new.md`, leave the original
untouched, and report **both** paths. An agent file is the most likely thing in this system a
user has hand-tuned; a second `/init-agents` must not destroy it. If the existing file is
byte-identical to what would be generated, leave it and report it as unchanged.

**Output:** the four agent paths (or `<name>.md` + `<name>.new.md` pairs where a file was
preserved) and the resolved senior name. Update `.claude/forge-state.json`
(`phases_completed` += `"3"`).

**Failure behavior:** **scan every generated file for surviving `{{placeholders}}` and fail
loudly if any remain** — name the file and the placeholder in the console output. A shipped
`{{PLACEHOLDER}}` is the most likely defect in this skill; do not let it pass silently.

---

## Phase 4 — GUIDE AND MANDATE TEMPLATE

**Input:** `templates/AGENTS_GUIDE.template.md`, `templates/MANDATE_TEMPLATE.template.md`,
`templates/RUN_LOG.template.md`, `templates/IMPROVEMENTS.template.md`,
`templates/HISTORIAS.template.md`.

**Actions**
1. Write `AGENTS_GUIDE.md` at the repo root — the **operator manual**, explicitly not read by
   agents. It now carries the **§Harness** section (below).
2. Write `docs/MANDATE_TEMPLATE.md` — the **paste-ready mandate** with its EXECUTION CONTRACT,
   PIPELINE, SELF-VERIFICATION, FINAL REPORT (whose **last section is NEXT** — propose the next
   mandate, never start it), and REQUEST block.
3. Write the remaining support artifacts if absent — `docs/CHANGELOG_INTERNAL.md`,
   `docs/FLAGS.md`, **`docs/RUN_LOG.md`**, **`docs/IMPROVEMENTS.md`**, and **`HISTORIAS.md`** at
   the repo root — each from its template, each with its budget header stating what does NOT
   belong in it. A destination without a stated boundary becomes another dumping ground.

   - **`docs/RUN_LOG.md`** — append-only, **one block per completed mandate**: date, scope, phases
     run, files touched, tests result, recommendations raised, HU reference. Written by the
     docs-updater, **never auto-loaded**. This is the observability layer: it is the record you
     read when a mandate misbehaves and you need the trajectory.
   - **`docs/IMPROVEMENTS.md`** — the open gaps the pipeline found, one line each, appended by the
     docs-updater from the tester's and senior's JSONs. Never invented to fill space.
   - **`HISTORIAS.md`** — the backlog, in the fixed parseable entry shape (§After the run), **read
     at the start of every mandate and written at the end**.
4. Fill the artifact placeholders. **Phase 3's fill list is scoped to the four agent templates —
   these artifacts need their own pass, including the two shared names:**
   - `{{PROJECT_NAME}}` — the project name, in `AGENTS_GUIDE`, `MANDATE_TEMPLATE`,
     `CHANGELOG_INTERNAL` and `FLAGS`. Same value as Phase 3 used.
   - `{{SENIOR_NAME}}` — the resolved senior agent name from Phase 3 (`backend-senior`,
     `<lang>-senior`, …), in `AGENTS_GUIDE` and `MANDATE_TEMPLATE`. A shipped `{{SENIOR_NAME}}`
     in the mandate template makes the mandate unrunnable as pasted.
   - `{{GRAPH_NOTE}}` (AGENTS_GUIDE) — one short paragraph stating this repo's actual
     `GRAPH_MODE` and what it means: the wired query surface, or the MCP server's ownership of
     its own instructions, or the "no graph below ~100 files, revisit at that threshold" reason,
     or the install-failure note if Phase 0.5 degraded.
   - `{{FRAMEWORK_OR_PROJECT}}` (facts file) — the detected dominant framework, else the project
     name.
   - `{{SEEDED_ENTRIES}}` (facts file **only**) — the Phase 1 harvest of **external** derivations
     in the entry format `<claim> — <path>:<line> — <date>`, grouped by subject; when there is
     nothing to seed, a single line stating the file is empty and fills on first use.
   - `{{SEEDED_NARRATIVE}}` (CHANGELOG_INTERNAL **only**) — the `NARRATIVE` content Phase 1
     relocated, in the changelog's own entry format (`## <date> — <title>` / Shipped / Why /
     Tried and rejected / Rule extracted), newest first. This is the story of past work, **not**
     ground-truth facts — never seed it from the facts-file harvest. Nothing relocated → a single
     line stating the file starts empty and fills on the first pipeline run.
   - `{{SEEDED_FLAGS}}` — one table row per flag or config key found in the manifest, env
     handling, and settings modules. Mark `Surprising? = yes` only where the default is counter
     to expectation, and mirror exactly those rows into the rulebook §Flags.
   - `{{VERIFY_TIER}}` (AGENTS_GUIDE §Harness, and the tester in Phase 3) — the literal word
     computed in Phase 0: `strong`, `moderate`, or `weak`.
   - `{{SEEDED_IMPROVEMENTS}}` (`IMPROVEMENTS` **only**) — the open gaps that already have
     evidence at init time, in the entry format below. Nothing found → a single line stating the
     file starts empty and fills from the pipeline's own findings. **Never invent entries to fill
     it.** Phase 6 adds the missing-runner entry when `VERIFY_TIER` is `moderate` or `weak`.
   - `{{SEEDED_STORIES}}` (`HISTORIAS` **only**) — existing user stories if the file is being
     created alongside prior work, else a single line stating the backlog starts empty. A backlog
     is not padded either.

**§Harness — write it into `AGENTS_GUIDE.md`.** One line per layer, each pointing at the artifact
that implements it, so the repo can name what it now has:

| Layer | Implemented by |
|---|---|
| Tool orchestration | the four agents + the scope gate |
| Verification | the tester's bar, `VERIFY_TIER` = `{{VERIFY_TIER}}` |
| Context & memory | graph / rulebook / facts file / changelog |
| Guardrails | §Do not break, gate-OFF byte-identical, exceptionless boundaries |
| Observability | `docs/RUN_LOG.md` |

**Output:** the artifact paths. Update `.claude/forge-state.json` (`phases_completed` += `"4"`).

**Failure behavior:** an artifact already exists with real content → do not overwrite. Prepend
the budget header if missing and report the file as preserved.

---

## Phase 5 — REGISTER THE REFRESH PATH

**Input:** everything produced.

**Actions**
1. Ensure `/refresh-agents` is available and documented. If the skill is running from a plugin,
   the command ships with it; if the user installed manually and
   `.claude/commands/refresh-agents.md` is absent, write it and say so.
2. Document in `AGENTS_GUIDE.md` what `/refresh-agents` does: re-verify the rulebook against
   code and graph, enforce the 300-line ceiling **by moving content out**, **regenerate the four
   agents from the current templates under the `.new.md` policy**, **drain `dirs_queued` from
   `.claude/forge-state.json`**, re-run Phase 6 when `VERIFY_TIER` changed, re-index the graph,
   report drift.
3. **State the registration step out loud.** A plugin's skill and commands are **not available in
   the session that installs them** — the session must be restarted before `/init-agents` or
   `/refresh-agents` resolves. This is why a fresh install can produce `Error: Unknown skill`.
   Say it in the closing summary, every run. And if **this** run had to work around it — the skill
   was invoked by reading `SKILL.md` off disk because the command did not resolve — report that
   explicitly as:

   > `registration: deferred — restart the session before the next command`

   A user who does not know that recovery exists sees a broken tool. Naming it costs one line.

Update `.claude/forge-state.json` (`phases_completed` += `"5"`).

---

## Phase 6 — WRITE THE LOOP SPECIFICATION, THEN CLOSE

**Input:** `VERIFY_TIER`, `HISTORIAS.md`, `docs/IMPROVEMENTS.md`, the command set, and §Commands
from the root rulebook.

This is the last phase. It writes the layer above the harness, seeds the improvement backlog from
what detection already proved, then runs the completion assertion and prints the closing summary.

### Step 1 — Write `docs/LOOP.md`

The repo's loop specification, from `templates/LOOP.template.md`. **Five parts, no more:**

1. **Trigger** — what starts a mandate. Today: the human pastes `docs/MANDATE_TEMPLATE.md`.
   Record any other trigger the repo **genuinely** supports (a CI hook that already exists, a
   Makefile target). **Do not invent scheduling this skill cannot deliver** — there is no daemon,
   no webhook, and no scheduler here, and describing one is a lie the next reader pays for.
2. **Goal** — how "done" is expressed for this repo: the REQUEST block's requirements plus the
   §Do not break contracts. Nothing else counts as done.
3. **Verification** — the exact commands that constitute proof here, **in order, verbatim from
   §Commands**, each with **what it can and cannot prove**. `tsc --noEmit` proves the types line
   up; it proves nothing about behavior. Say so per command.
4. **Stopping rule** — the run ends when self-verification passes; the tester's **three-attempt
   cap**; the **one-return-trip limit** between tester and senior; and the two halt conditions
   (a proven-impossible requirement, a §Do not break conflict with no compliant path).
   **Every loop must be able to stop without a human.**
5. **Memory** — which files carry state between mandates (`HISTORIAS.md`, `docs/IMPROVEMENTS.md`,
   `docs/CHANGELOG_INTERNAL.md`, `docs/RUN_LOG.md`, the facts file) and the rule that **a mandate
   reads them before planning**.

**Fill list for `templates/LOOP.template.md`** — every placeholder, by name:

- `{{PROJECT_NAME}}` — the project name. Same value Phases 3 and 4 used.
- `{{VERIFY_TIER}}` — the literal word from Phase 0: `strong`, `moderate`, or `weak`.
- `{{LOOP_AUTONOMY_VERDICT}}` — one blockquote sentence stating what this tier sanctions, taken
  from the gate table in Step 2. `strong` → an unattended sequence is sanctioned, with human
  review at the diff. `moderate` → no unattended loop is sanctioned; compiling is not behaving.
  `weak` → there is no sensor here; get one before anything else.
- `{{LOOP_TRIGGER}}` — part 1: the human pasting `docs/MANDATE_TEMPLATE.md`, plus any other
  trigger the repo **genuinely** supports. Invent nothing.
- `{{LOOP_GOAL}}` — part 2: the REQUEST block's requirements plus the `CLAUDE.md` §Do not break
  contracts, named.
- `{{LOOP_VERIFICATION}}` — part 3: the §Commands entries **verbatim, in run order**, each with
  one line on what it can and cannot prove.
- `{{LOOP_STOPPING_RULE}}` — part 4: self-verification passing, the tester's three-attempt cap,
  the one-return-trip limit, the two halt conditions — and, for `moderate`/`weak`, the explicit
  statement that the human reviews every mandate before the next begins.
- `{{LOOP_MEMORY}}` — part 5: the memory files above, one line each on what each carries and who
  writes it.

### Step 2 — The honest gate: write the loop TO the repo's `VERIFY_TIER`

**In any loop the verifier is the bottleneck, not the model.** A loop running unattended is a
loop making mistakes unattended. So what §Stopping rule is allowed to describe depends on what
this repo can actually prove:

| `VERIFY_TIER` | What `docs/LOOP.md` may say |
|---|---|
| **`strong`** | The spec **may** describe an unattended sequence: mandate → verify → next mandate from the backlog, with human review at the diff. |
| **`moderate`** | The spec states **explicitly that no unattended loop is sanctioned** — compiling is not behaving. The human reviews every mandate before the next begins, and the top entry in `docs/IMPROVEMENTS.md` is **"install a test runner"**. |
| **`weak`** | The same, in stronger terms, and the loop section is **one paragraph**: get a sensor first. |

This gate is not softened, not hedged, and not overridden by a user asking for more autonomy. A
skill that offers autonomy to a repo that cannot verify itself is selling the exact failure mode
the whole discipline exists to prevent. **Refusing here is the feature.**

### Step 3 — Seed `docs/IMPROVEMENTS.md` when the tier demands it

`VERIFY_TIER` is `moderate` or `weak` → append this entry automatically, at the next free
`IMP-NNN`, with today's date, and make it the **top** entry:

```
- [ ] IMP-001 — install a test runner; this repo can prove it compiles, not that it behaves — <manifest path> — found <date> in init — severity: high
```

**Seed it ONCE — the write is idempotent.** Before appending, scan the unchecked entries for one
whose normalized text already says this (lowercased, punctuation and the `<path>` / `found <date>`
tails stripped: an existing "install a test runner" entry matches regardless of its ID, date, or
wording of the tail). Found → **change nothing** and report it as already present. `/init-agents`
on an already-forged repo and every `/refresh-agents` both re-enter this step; without the guard
each one would stack another copy of the same line, and a file whose top three entries are the
same gap teaches the reader to skim past it.

Also check the `## Deferred` section: the entry sitting there means a human demoted it
deliberately. **Do not re-seed above it** — say so in the summary instead.

`strong` → seed nothing, and if an unchecked missing-runner entry exists from an earlier run,
**close it**: the gap is gone. This is the one entry the skill is allowed to write without a
pipeline finding behind it, because detection *is* the evidence: the manifest has no runner.

### Completion assertion — run this BEFORE printing the summary

The user's expectation of `/init-agents` is one command that leaves them with a wired graph,
generated agents, the `CLAUDE.md` files their architecture needs, and a loop spec that matches
what the repo can prove. Assert all seven classes; **verify by inspection, never by assumption**:

| Class | Passes when |
|---|---|
| **Graph** | wired and indexed, **OR** deliberately skipped with the reason recorded in the rulebook. Any other state is a failure. |
| **Agents** | four files in `.claude/agents/`, and **zero surviving `{{placeholders}}` in any of them** — scan the files, do not assume Phase 3 succeeded. |
| **Root rulebook** | exists, and is either under 300 lines **or** over it with the ⚠ over-ceiling line under its title and the relocation order provably exhausted. A missing file is the only failure here — an over-ceiling file that was written and flagged is a pass. |
| **Per-directory rulebooks** | one exists for every directory that met Phase 2B's criteria **and fit within the 20-rulebook cap**, a **named reason** for every rejection, and every over-cap directory listed in `dirs_queued`. Zero qualifying directories is a pass only when the reason is stated. |
| **Support artifacts** | `docs/GROUND_TRUTH.md`, `docs/CHANGELOG_INTERNAL.md`, `docs/FLAGS.md`, `docs/MANDATE_TEMPLATE.md`, `AGENTS_GUIDE.md` all exist. |
| **Harness + loop** | `docs/RUN_LOG.md`, `docs/IMPROVEMENTS.md`, `HISTORIAS.md`, and `docs/LOOP.md` exist, each with its budget header; `AGENTS_GUIDE.md` has its §Harness section; `docs/LOOP.md` matches the `VERIFY_TIER` gate — **an unattended loop described for a non-`strong` repo is a hard failure**, not a style issue. |
| **Run state** | `.claude/forge-state.json` exists, lists all ten phases in `phases_completed`, and is ignored by `.gitignore` when the repo is a git working tree. |

Anything failing → **fix it and re-verify**, then print the summary.

**Output — the closing summary:**

```
written:        <file list>
forge:          FORGE_STATE=<fresh|initialized> — <full init | refresh semantics>
graph:          GRAPH_MODE=<final value> — <installed|updated|skipped (small tier; …)|FAILED: reason>
verify:         VERIFY_TIER=<strong|moderate|weak> — loop spec: <unattended sanctioned |
                no unattended loop — human reviews every mandate | no sensor — get one first>
per-directory:  written <path>, <path> … | preserved <path> … | skipped <path> — <reason> …
                queued <path> … — beyond the 20/run cap, /refresh-agents drains them
                (or: none — <why no directory qualified>)
agents:         <4 paths> — placeholder scan: clean | <file>:<placeholder>
unverified:     <count> [UNVERIFIED] items — <where>
verify markers: <count> [VERIFY] items — <what must be confirmed>
rulebook:       <n>/300 lines <| ⚠ over ceiling — relocation exhausted>
state:          .claude/forge-state.json — phases 0…6 complete
registration:   restart the session before running /init-agents or /refresh-agents again —
                a plugin's skill and commands do not resolve in the session that installed them
                <| deferred — this run read SKILL.md off disk because the command did not resolve>
next:           docs/MANDATE_TEMPLATE.md — paste it, fill the REQUEST block.
                docs/LOOP.md — how this repo's loop is allowed to run.
```

**Failure behavior:** any item incomplete → fix it and re-verify before printing the summary.
Completion means verified complete, not "done, please check". Finally, write
`.claude/forge-state.json` with `phases_completed` holding all ten phases.

---

## After the run — the User Story (H) loop

This is **not** a phase and never blocks completion. Once the summary is printed, the skill may
offer to turn the user's next piece of work into a ready-to-run mandate:

1. **Ask once:** "Init is done. Have a user story to work on next — feature, bug, or
   improvement? I'll turn it into a mandate. Or say no to finish."
2. **If given a story:** record it in `HISTORIAS.md` at the repo root as the next `HU-NNN`, and
   generate the mandate by filling `docs/MANDATE_TEMPLATE.md`'s REQUEST block with the concrete
   story. Ask whether to run it now.
3. **After a HU completes:** self-review what was touched and flag honestly anything fragile or
   rushed — never default to "all perfect". Read `HISTORIAS.md`, then either suggest the next
   pending HU or propose a new one from what was observed. **The user decides**; never
   auto-start the next HU.

### `HISTORIAS.md` is READ-WRITE — this is the loop's memory

A write-only backlog is not memory: every mandate then re-derives context a previous one already
paid for. The entry shape is **fixed and parseable** so it can be read back:

```
## HU-007 — <short title>
- Estado: pendiente | en progreso | hecha | descartada   (YYYY-MM-DD)
- Contexto: <one line: the symptom or the want>
- Depende de: HU-003 | —
- Cierre: <audit/changelog link, when hecha>
```

**Three lines of substance, no transcripts.** The reading contract:

- **The analyst reads the OPEN entries only** — `pendiente` and `en progreso` — at the start of a
  mandate, together with the open items in `docs/IMPROVEMENTS.md`, and returns
  `related_stories` and `related_improvements` in its plan JSON: whether this request continues
  existing work or opens new ground.
- **The docs-updater writes** the outcome back at the end of the cycle, within its 3-line budget.
- A `hecha` or `descartada` entry is never read at plan time — it stays for the trail.

It is a backlog and record, not a transcript.

### Both continuity files are capped at 15 OPEN entries

Per-entry budgets alone do not bound a file — a hundred one-line entries is still a hundred lines
loaded before every mandate. This is the monotonic-growth defect the 300-line ceiling and the
40-line cap already fixed twice, reappearing in the continuity layer, and it is worse here
because **the analyst reads these files off-budget**: nothing else brakes it.

- `HISTORIAS.md` — **max 15 open entries** (`pendiente` + `en progreso`).
- `docs/IMPROVEMENTS.md` — **max 15 unchecked entries.**

The cap is written into each file's own budget header, so it survives independently of this skill.
The docs-updater enforces it at the end of every cycle by consolidating in a fixed order — merge
duplicates (oldest ID wins) → close what has been untouched 90 days, moving it to the changelog on
that same cycle → demote the lowest-severity `low` items, oldest first, to a `## Deferred` section
at the bottom that the analyst never reads. Order exhausted and still over → **write anyway** with
`⚠ Over cap: <n>/15 open — consolidation exhausted. Oldest open: <ID, date>.` under the title.

**A cap must never cause a cycle to stall, refuse, or silently drop an entry.** The analyst's read
is bounded to match: at most 15 per file, never the `## Deferred` section, and an observed
overflow sets `continuity_overflow: true` in the plan JSON rather than stopping the run.

### `docs/IMPROVEMENTS.md` — where found gaps live

The FINAL REPORT's recommendations are real findings, and until now they evaporated into chat
scrollback. They get a home, in this exact one-line shape:

```
- [ ] IMP-004 — <one line> — <path>:<line> — found <date> in <HU-NNN|init> — severity: low|med|high
```

Rules:

- **Appended by the docs-updater** from the tester's and senior's JSONs.
- **Never invented to fill space.** An empty improvements file is an honest state.
- One line each. The detail belongs in `docs/CHANGELOG_INTERNAL.md`.
- A resolved item is **checked and moved to the changelog on the next cycle**, so the file stays a
  list of *open* gaps rather than an archive.
- **Max 15 unchecked entries**, consolidated by the docs-updater as described above.
- Phase 6 seeds the missing-test-runner entry automatically when `VERIFY_TIER` is `moderate` or
  `weak` — **once**, never on every run (see Phase 6 Step 3).

---

## Hard rules (apply throughout)

1. **Evidence only** — verify against real code or the graph; mark uncertainty `[UNVERIFIED]`;
   never invent. Any install/init command that cannot be verified from the tool's own source of
   truth is written as `[VERIFY: <what> — confirm before release]` and reported.
2. **Init writes only docs + agent files** — never source, configs, or tests.
3. **Nothing is stored in two places** — graph owns navigation, rulebook owns judgment, facts
   file owns external derivations.
4. **Autonomous run** — no approval stop. Print brief progress; the user reviews the diff.
5. **No git operations, ever** — `.gitignore` is a file edit, not a git command.
6. **Honest framing** — "standardizes + improves consistency", never "error-free".
7. **Language-agnostic** — adapt the senior agent and conventions to the detected stack.
8. **A size rule never blocks a run.** The 300-line ceiling and the 40-line cap relocate first and
   then write anyway with a warning line. Stalling, refusing, or leaving a repo half-written
   because of a budget is always the wrong outcome.
9. **State is bookkeeping, never knowledge.** `.claude/forge-state.json` records where the run
   got to; nothing that belongs in the rulebook, the facts file, or the backlog goes in it. It is
   discardable by design and is `.gitignore`d.
10. **No autonomy without a sensor.** An unattended loop is described only for a repo whose
    `VERIFY_TIER` is `strong`. This does not bend to a request for more autonomy.

## Bundled resources

- `reference/claude-md-spec.md` — the 14-section rulebook spec + the five-part per-directory
  rulebook. Read before Phase 2A and Phase 2B.
- `reference/graph-integration.md` — the verified graph command surface and the knowledge split.
  Read before Phase 0.5.
- `templates/architecture-analyst.md`, `templates/senior-engineer.md`, `templates/tester.md`,
  `templates/docs-updater.md` — agent skeletons. Read before Phase 3.
- `templates/AGENTS_GUIDE.template.md`, `templates/MANDATE_TEMPLATE.template.md`,
  `templates/GROUND_TRUTH.template.md`, `templates/CHANGELOG_INTERNAL.template.md`,
  `templates/FLAGS.template.md`, `templates/RUN_LOG.template.md`,
  `templates/IMPROVEMENTS.template.md`, `templates/HISTORIAS.template.md` — artifact skeletons.
  Read before Phase 4.
- `templates/LOOP.template.md` — the loop specification skeleton. Read before Phase 6.
- `commands/init-agents.md`, `commands/refresh-agents.md` — the slash commands.
