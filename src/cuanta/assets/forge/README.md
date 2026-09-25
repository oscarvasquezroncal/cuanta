# Claude Agent Forge

**Standardize any repository for multi-agent development with Claude Code — in one command.**

`claude-agent-forge` is a [Claude Code](https://www.anthropic.com/claude-code) skill that turns a codebase into a consistent, agent-ready project. It wires a **code graph** for navigation, generates a verified **`CLAUDE.md` rulebook** for judgment, and produces a pipeline of four specialized subagents plus the mandate template that drives them. Works with Python, JavaScript/TypeScript, Go, and any other structured project.

> **Honest scope:** this standardizes context and enforces a consistent workflow. It improves reliability and reduces avoidable mistakes by giving agents verified docs, specialized roles, and explicit handoffs. It does **not** guarantee bug-free code, and it does not replace code review, tests, or version control.

---

## The thesis — a knowledge split, not a bigger CLAUDE.md

A knowledge base left to grow organically **duplicates what a code graph already knows**, so every session pays for structure twice: once in the graph, once in the file loaded on every message. Most `CLAUDE.md` files are 60% directory trees and file inventories — the exact content an AST parser regenerates for free.

The fix is not "replace `CLAUDE.md` with a graph". It is a split by **kind** of knowledge:

| | **Graph** (AST-derived) | **Rulebook** (`CLAUDE.md`) | **Facts file** (`docs/GROUND_TRUTH.md`) |
|---|---|---|---|
| Holds | what the code **IS**: symbols, calls, imports, inheritance, blast radius | what it **SHOULD be and WHY**: contracts, flag defaults, traps, proven-not-bugs | expensive derivations from **external** sources: framework internals, vendor API semantics |
| Derived | automatically, always | from experience; unrecoverable if lost | once, at real cost |
| Ages | no — re-index and it is current | yes — needs a ceiling and a budget | no — append-only, dated, cited |
| Cost | ~2k tokens per query | loaded in full, every message | read on demand |

> **The graph owns navigation. The rulebook owns judgment. The facts file owns expensive external derivations. Nothing is stored in two places.**

**Why the rulebook cannot be folded into a graph:** no AST parser will ever hold *"this deprecated-looking call is a working compatibility shim — flagging it CRITICAL is the bug"*, or *"flag OFF must be byte-identical to legacy"*, or *"never reload the config module inside a test"*. That is institutional judgment. It is not in the syntax tree and never will be.

**The corollary this skill enforces:** once a graph exists, the rulebook is **aggressively emptied** of everything structural. Generating a graph without removing what it replaces produces a repo that pays for both.

---

## What it produces

### 1. A wired code graph (when it pays)
Installed and indexed in one non-interactive step — or deliberately skipped. See [Graph integration](#graph-integration) below.

### 2. A `CLAUDE.md` rulebook
- A **root `CLAUDE.md`** under a hard **300-line ceiling**, holding judgment only: contracts, surprising flag defaults, repo traps, and the section nobody writes — **Proven NOT bugs**, the things that look like defects and are not.
- **Per-directory `CLAUDE.md`** files holding **local rules only** — purpose, local conventions, exceptionless boundaries, local commands, local gotchas — derived from architecture, capped at 40 lines. If a directory file would only describe what lives in that folder and a graph is wired, it is not created at all, and the skip is **reported with its reason**.
- **Zero drifting numbers**: no test counts, no migration revisions, no dates-of-verification. Where a count matters, the file stores *the command that asks the code*. This is what keeps a knowledge base from quietly lying six weeks later.

### 3. A four-agent pipeline (`.claude/agents/`)

```
architecture-analyst  →  <lang>-senior  →  tester  →  docs-updater
     (map + plan)          (implement)      (prove)    (route + prune)
```

| Agent | Model | Role |
|-------|-------|------|
| `architecture-analyst` | sonnet | **Reads the open backlog and improvement entries first**, then queries the graph and returns a plan JSON with a complete `blast_radius`, plus `related_stories` and `related_improvements`. Bounded read budget. A **STOP** (falsified premise, contract conflict, already handled) is a successful outcome. |
| `<lang>-senior` | opus | Implements one phase per invocation from the plan. Forbidden from re-deriving the blast radius; returns `blocked` instead of falling back to an unbounded search. |
| `tester` | sonnet | Writes and **runs** tests, every command truncated. Regression fixture, both flag directions, negative cases. Three fix attempts, then `persistent_failure`. Its contract is filled from `VERIFY_TIER`: on a repo with no runner it states it can prove compilation and lint, **never behavior**, and never reports `green`. |
| `docs-updater` | haiku | Works from the three JSONs only. Routes to seven destinations — **a rule scoped to one directory goes to that directory's file, always**, plus `docs/IMPROVEMENTS.md`, `docs/RUN_LOG.md` and the `HISTORIAS.md` entry — under a 5-line root budget and 3 lines per directory, and enforces the 300-line ceiling by **moving content out** without ever blocking the write. |

All four ship with a mandatory JSON deliverable, an explicit git prohibition, and no progress narration.

### 4. Supporting artifacts
`docs/GROUND_TRUTH.md` (external derivations, cited, once) · `docs/CHANGELOG_INTERNAL.md` (all narrative, never auto-loaded) · `docs/FLAGS.md` (the full registry) · `docs/MANDATE_TEMPLATE.md` (paste-ready) · `AGENTS_GUIDE.md` (operator manual). **Each ships with a header stating its budget and what does NOT belong in it** — a destination without a stated boundary becomes another dumping ground.

### 5. The harness, named — and the loop above it
`docs/RUN_LOG.md` (observability) · `docs/IMPROVEMENTS.md` (open gaps) · `HISTORIAS.md` (the backlog, **read-write**) · `docs/LOOP.md` (the loop specification, written to the repo's real verification tier). See [The harness and the loop](#the-harness-and-the-loop).

---

## The harness and the loop

What the four agents plus the artifacts add up to has a name: a **harness**. The skill has always built one; from v0.3 it says so, and fills the layer that was thin.

| Layer | Implemented by |
|---|---|
| Tool orchestration | the four agents + the scope gate |
| Verification | the tester's bar, `VERIFY_TIER` |
| Context & memory | graph / rulebook / facts file / changelog |
| Guardrails | §Do not break, gate-OFF byte-identical, exceptionless boundaries |
| Observability | **`docs/RUN_LOG.md`** — one append-only block per completed mandate: date, scope, phases run, files touched, tests result, recommendations raised, HU reference. Never auto-loaded; it is what you read when a mandate misbehaves and you need the trajectory rather than your memory of it. |

Above the harness sits the **loop**: a bounded specification of **trigger, goal, verification, stopping rule, memory**, written to `docs/LOOP.md` in Phase 6. Without it every mandate starts from a blank page — `HISTORIAS.md` was write-only, and the FINAL REPORT's recommendations evaporated into chat scrollback, so each run re-derived context the previous one had already paid for.

### `VERIFY_TIER` — and the gate that is not negotiable

Phase 0 computes what the repo can actually prove about itself, from the manifest scripts and dev dependencies:

| Tier | Means | What `docs/LOOP.md` may describe |
|---|---|---|
| **`strong`** | a test runner (a missing typecheck/build is recorded as `no typecheck`) | an unattended sequence: mandate → verify → next mandate from the backlog, with human review at the diff |
| **`moderate`** | typecheck, lint, or build — **no test runner** | **no unattended loop is sanctioned.** Compiling is not behaving. The human reviews every mandate before the next begins, and "install a test runner" becomes the top entry in `docs/IMPROVEMENTS.md` |
| **`weak`** | build only, or nothing verifiable | the same, in stronger terms — the loop section is one paragraph: get a sensor first |

**In any loop the verifier is the bottleneck, not the model.** A loop running unattended is a loop making mistakes unattended. A skill that offers autonomy to a repo that cannot verify itself is selling the exact failure mode the discipline exists to prevent — so **refusing there is the feature**, and no request for more autonomy overrides it.

The tier is load-bearing elsewhere too: it fills the generated tester's contract, so a `moderate` repo's tester states plainly that it can prove compilation and lint, never behavior, and never reports `green`.

### The loop's memory — two files, read at the start, written at the end

- **`HISTORIAS.md`** becomes **read-write**, with a fixed parseable entry (`Estado` / `Contexto` / `Depende de` / `Cierre`). The analyst reads the **open** entries only and returns `related_stories` in its plan JSON.
- **`docs/IMPROVEMENTS.md`** holds the open gaps the pipeline found itself, one line each, appended by the docs-updater from the tester's and senior's JSONs — **never invented to fill space**. A resolved item is checked and moved to the changelog next cycle, so the file's length is the size of the debt, not the history.

**Both are capped at 15 open entries.** Per-entry budgets do not bound a file — a hundred one-line entries is still a hundred lines loaded before every mandate, and the analyst reads these files *off-budget*, so nothing else brakes them. Over the cap the docs-updater consolidates in a fixed order — merge duplicates → close what has been untouched 90 days → demote the rest to a `## Deferred` section the analyst never reads — and if that is exhausted, writes the file anyway with an over-cap warning. Nothing is ever silently dropped, and the cap never blocks a cycle.

The mandate's FINAL REPORT ends with a **NEXT** section: read both files, propose the single most sensible next mandate with a filled REQUEST block. **Propose only — never start it.** One mandate ends holding the next one.

---

## Graph integration

The skill wires a graph **only when it pays**, and says so out loud when it does not.

**Branch precedence: the size gate sits above mode detection.** More than one condition can hold at once — a repo can be small *and* have graphify installed. `SIZE_TIER=small` wins, and the run says which condition won: `skipped (small tier; graphify present but not indexed for this repo)`. Reporting only the outcome looks like a bug even when the behavior was right.

| Situation | What happens |
|---|---|
| **< ~100 source files** | **No graph, deliberately** — even if graphify or a graph MCP server is already present. Below roughly a hundred files a pre-built index does not pay for itself; agentic grep is the correct tool at that size. The reason is written into the generated rulebook. |
| **A graph MCP server is already configured** | **Nothing installed, no command syntax written.** The MCP server delivers its own usage instructions at session start; duplicating them is the exact double-payment this skill removes. Only one behavioral rule is added: *trust the graph's edges; do not read source to confirm an edge.* |
| **graphify already present** | Not reinstalled. Re-indexed with `graphify update .`, full navigation contract written into the rulebook. |
| **≥ ~100 files, no graph** | Installed and indexed in one non-interactive step. |
| **Install fails, for any reason** | **Graceful degradation.** The pipeline continues with `GRAPH_MODE=none`, grep-based navigation rules are written instead, and the failure is reported at the end. A failed graph install never aborts the skill. |

The default graph backend is [graphify](https://pypi.org/project/graphifyy/): package `graphifyy`, binary `graphify`. `graphify update .` builds from scratch and incrementally thereafter — no LLM, no API key. The verified command surface lives in `skills/agent-system-init/reference/graph-integration.md`.

**A stale graph is worse than no graph** — agents distrust it and re-explore, paying for both. Every branch that wires one also writes the re-index instruction into the rulebook *and* into the docs-updater contract.

---

## How it works — ten phases

| Phase | What happens |
|-------|--------------|
| **0 — Detect** | Stack, versions, test runner, entry points. Computes `FILE_COUNT`, `SIZE_TIER`, `DOCS_STATE`, `FORGE_STATE`, `VERIFY_TIER`, `GRAPH_MODE`, `VCS`. Reads `.claude/forge-state.json` and **resumes an interrupted run**. Nine-line summary, no questions. |
| **0.5 — Graph bootstrap** | The branch table above, with an explicit precedence: **the size gate outranks mode detection**. Adds the artifact dir to `.gitignore` (a file edit — never a git command). |
| **1 — Audit / Seed** | Classifies every existing line: `RULE` keep · `STRUCTURE` delete (the graph owns it) · `NARRATIVE` relocate · `STALE` delete · `UNVERIFIABLE` mark. No CLAUDE.md → infers a base and marks it `[UNVERIFIED]`. At `xl`, **audits by sampling and says so**. |
| **2A — Root rulebook** | The root `CLAUDE.md`, to the 14-section spec, under the ceiling — with a **deterministic, always-terminating** overflow path. |
| **2B — Per-directory rulebooks** | Selects the directories that earn one, derives **rules from architecture** — never inventory — **in batches of ≤6, capped at 20 per run**, and **reports every rejection and every queued directory**. See below. |
| **2.5 — Facts file** | `docs/GROUND_TRUTH.md`, seeded from what the audit found, append-only and cited. |
| **3 — Agents** | Fills the four templates, with the navigation contract matching `GRAPH_MODE` and the tester's contract matching `VERIFY_TIER`. Fails loudly on any surviving `{{placeholder}}`. |
| **4 — Guide + mandate + memory** | `AGENTS_GUIDE.md` (with the §Harness table), `docs/MANDATE_TEMPLATE.md`, `docs/RUN_LOG.md`, `docs/IMPROVEMENTS.md`, `HISTORIAS.md`, and the remaining artifacts. |
| **5 — Refresh path** | Registers `/refresh-agents` and states the **restart-after-install** step out loud. |
| **6 — Loop spec + completion** | Writes `docs/LOOP.md` **to the repo's `VERIFY_TIER`**, seeds the missing-runner improvement when the tier demands it, asserts every artifact class exists, then prints the closing summary. |

**The run is autonomous** — it never pauses for approval. **Commit or stash before running**, and review the diff afterward. The skill itself performs **no git operations of any kind**.

### Any project size — four tiers, batched and resumable

`SIZE_TIER` is `small` (<100 source files) · `medium` (<500) · `large` (<2000) · **`xl` (≥2000)**. At `xl` the strategy changes, not just the thresholds: Phase 1 **audits a stated sample** rather than everything, Phase 2B selects directories **from the graph only** instead of walking the tree, and the per-run cap applies hard.

- **Batched.** Phase 2B processes at most **6 directories per batch**, writes them, updates the state file, and **releases that batch's evidence before the next**. Holding a whole monorepo's evidence at once is the specific thing that made a large run fall over.
- **Capped, and honest about it.** At most **20 per-directory rulebooks per run**. Directories beyond the cap go to `dirs_queued`, are **reported in the closing summary**, and are drained by the next `/refresh-agents`. A queued directory is a stated outcome, never a silent omission.
- **Resumable.** `.claude/forge-state.json` is written after every phase and every batch, and read at the start of Phase 0. A run that dies at Phase 3 **resumes at Phase 3**. A corrupt state file is discarded with a note, never a hard failure. It is bookkeeping, not knowledge — and it is git-ignored.

### A size rule never blocks a run

Over the 300-line ceiling, content relocates in a fixed order — structure (deleted, the graph owns it) → narrative → the flag registry → external derivations → single-directory rules. **When that order is exhausted and the file is still over, it is written anyway**, with this line under the title:

> ⚠ Over ceiling: `<n>`/300 — relocation exhausted. Split candidates: `<the sections>`.

Same for the 40-line per-directory cap. A budget is a signal, not a gate: a repo with an over-ceiling rulebook and a visible warning is strictly better off than a repo left half-written.

### Architecture yields rules, not inventory

The per-directory rule is *local rules only* — a file that would merely describe what lives in a folder is not created. Read naively on a fresh repo, that excludes everything: local rules come from experience, and a new repo has none. The answer is not to fill those files with structure. It is that **architecture is itself a source of rules**, and Phase 2B derives them:

| Structure — the graph owns it, never written | Rule — the rulebook owns it, derived |
|---|---|
| `api/` contains `routers/`, `services/`, `db/` | business logic lives in `services/`; routers stay thin |
| `worker/processor.py` imports `packages/core` | `worker/` may not import from `api/` — one-way boundary |
| this module has 14 functions | every I/O function here is async; sync I/O is wrapped |
| `tests/api/` exists | run only this subsystem's tests with `<command>` |

A directory earns a rulebook when it is a **cohesive boundary**, its **conventions provably differ** from the root's, it is a **seam** onto an external system, or it has **its own run/test command**. Content is at most five parts — purpose, local rules, boundaries, local commands, local gotchas — capped at 40 lines, with parts omitted rather than padded.

**A boundary rule is written only when the pattern is exceptionless.** One counter-example in the graph and it is a tendency, not a rule; shipping it as a rule means the next agent "fixes" working code.

**Local gotchas start empty, honestly** — `No local gotchas recorded yet — the docs-updater appends here as they are found.` That is the point: the docs-updater routes single-directory rules *down* on every cycle (3 lines per directory), creating the file if a rule needs one. A repo's architecture files fill in over the first weeks of real work rather than shipping fabricated.

### Re-running is safe — and a second init is not a virgin audit

Neither `/init-agents` nor `/refresh-agents` overwrites a file you may have tuned. A regenerated agent that differs from the one on disk is written to `<name>.new.md` with the original preserved and both paths reported. Existing per-directory rulebooks are preserved on a fresh init and updated in place only on an audit run.

Phase 0 also computes **`FORGE_STATE`**. When forge-generated agents or support artifacts are already present, `/init-agents` **runs refresh semantics**: it verifies against **code and graph**, never against a rulebook a previous forge run wrote. Without this the classifier grades its own homework — everything it wrote reads back as `RULE`, nothing is ever stale, and the audit path is never really exercised. The run says which semantics it is using: `forge: initialized — running refresh semantics`.

**`/refresh-agents` regenerates the four agents from the current templates, always** — not only when the stack changed. A repo initialized under v0.1 carries the old docs-updater, the one with no budget whose knowledge base grows forever; refresh is how it learns the new contract.

---

## Installation

### Option 1 — Claude Code plugin (recommended)

```
/plugin marketplace add https://github.com/oscarvasquezroncal/claude-agent-forge
/plugin install claude-agent-forge@claude-agent-forge
```

**Then restart Claude Code before running anything.**

### Option 2 — Manual install

```bash
git clone https://github.com/oscarvasquezroncal/claude-agent-forge.git
cd claude-agent-forge

./install.sh      # macOS / Linux
.\install.ps1     # Windows PowerShell
```

Copies the skill into `~/.claude/skills/agent-system-init` and both commands (`/init-agents`, `/refresh-agents`) into `~/.claude/commands/`. Both installers fail loudly if any skill file or command is missing, rather than leaving a half-install that only breaks later, inside your repo.

### Restart first — this step is not optional

**A plugin's skill and slash commands are not registered in the session that installs them.** Running `/init-agents` in that same session gives:

```
Error: Unknown skill
```

That is registration, not a broken install. Restart Claude Code, then run `/init-agents` in any project. Both installers print this, and every run ends by repeating it.

---

## Usage

```
/init-agents        # standardize the repo (ten phases, autonomous, resumable)
/refresh-agents     # re-verify against code + graph, drain queued rulebooks, enforce the ceiling,
                    # re-run the loop spec if VERIFY_TIER changed, re-index, report drift
```

or just ask: *"Standardize this repo with the agent system."*

### Then, to build something

Open `docs/MANDATE_TEMPLATE.md`, fill the `REQUEST` block, paste it. The mandate carries its own EXECUTION CONTRACT — one continuous pass, no approval stops, no git operations, and a fixed final report ending in a **commit message proposal** you run yourself.

```
=== REQUEST ===
TYPE:            bug
WHAT:            <the change, concretely>
WHY / EVIDENCE:  <PASTE the traceback — never describe it>
WHERE:           <file / module, or "unknown">
CONSTRAINTS:     <invariants, flag directions, what stays byte-identical>
EXPECTED TESTS:  <regression fixture for the pasted error>
OUT OF SCOPE:    <what must NOT be touched — always fill this>
```

---

## Cost dials — in the correct order

Turn them in this order. The first two are worth more than the last two combined.

1. **The scope gate.** trivial → **no agents**; normal → analyst/senior/tester; complex → full pipeline. Not running three agents on a ten-line fix saves more than every other dial. The gate lives in the generated `CLAUDE.md`, so the main session reads it every session.
2. **Batch the docs-updater.** Its budget is per cycle — let a few normal-scope runs accumulate and route them in one pass.
3. **The analyst's model tier.** With a wired graph it does bounded queries against structured output; a cheaper tier handles it well.
4. **The senior's model tier — LAST.** It is the only agent writing production code. Downgrading it saves the least and costs the most: you pay it back in review time, rework, and the bugs a cheaper tier ships quietly.

Also: `/clear` between unrelated tasks, `/usage` to track spend.

---

## Repository layout

```
claude-agent-forge/
├── .claude-plugin/
│   ├── plugin.json                   # plugin manifest
│   └── marketplace.json              # marketplace catalog
├── commands/
│   ├── init-agents.md                # /init-agents
│   └── refresh-agents.md             # /refresh-agents
├── skills/
│   └── agent-system-init/
│       ├── SKILL.md                  # the method: ten phases + hard rules
│       ├── reference/
│       │   ├── claude-md-spec.md     # the 14-section rulebook spec
│       │   └── graph-integration.md  # verified graph command surface
│       └── templates/
│           ├── architecture-analyst.md
│           ├── senior-engineer.md
│           ├── tester.md
│           ├── docs-updater.md
│           ├── AGENTS_GUIDE.template.md
│           ├── MANDATE_TEMPLATE.template.md
│           ├── GROUND_TRUTH.template.md
│           ├── CHANGELOG_INTERNAL.template.md
│           ├── FLAGS.template.md
│           ├── RUN_LOG.template.md
│           ├── IMPROVEMENTS.template.md
│           ├── HISTORIAS.template.md
│           └── LOOP.template.md
├── install.sh                        # manual installer (macOS/Linux)
├── install.ps1                       # manual installer (Windows)
├── LICENSE
├── CHANGELOG.md
└── CONTRIBUTING.md
```

---

## Status & roadmap

Built from a real, working pattern; **not yet benchmarked across many repositories**. Treat the first run on a new stack as a first pass and review the output. The most likely rough edge remains an unfilled `{{placeholder}}` in a generated artifact — every placeholder now has a fill instruction and Phase 5 scans the generated agents for survivors, but check.

Done: plugin distribution (v0.1.0) · graph integration, knowledge split, size budgets, `/refresh-agents` (v0.2.0) · per-directory rulebooks derived from architecture, downward rule routing, completion assertion, safe re-runs (v0.2.1) · the `xl` tier, batched and resumable runs, the named harness, the loop layer with its verification gate, and a read-write backlog (v0.3.0).
Planned: evaluation across multiple stacks, optional stack-specific specialist agents beyond the four.

**Deliberately not built:** a scheduler, a daemon, or a webhook trigger. Phase 6 **describes** the loop; it does not automate it. Nothing in this skill runs without a human pasting a mandate, and nothing sanctions an unattended loop on a repo that cannot verify itself.

Contributions and issues welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

[MIT](LICENSE) © 2026 Oscar Vasquez Roncal
