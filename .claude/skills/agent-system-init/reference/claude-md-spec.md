# Rulebook spec — root `CLAUDE.md` + per-directory

This file is NOT a spec for "documentation". It is a spec for a **rulebook**.

The root `CLAUDE.md` loads in full on every message. That makes it the most expensive file in the
repo per unit of information, so it may hold only the kind of knowledge that nothing else can
hold: **judgment**. What the code IS — symbols, calls, imports, inheritance, blast radius —
belongs to the graph. Expensive derivations from external source trees belong to the facts file.

> The graph owns navigation. The rulebook owns judgment. The facts file owns expensive external
> derivations. Nothing is stored in two places.

## Three hard rules — write these INSIDE the generated file

They must survive after the skill is gone, so they go in the file itself, not only here.

1. **300-line ceiling on the root file.** Over it, content moves out; nothing is added.
   Relocate in this **fixed priority order**, stopping as soon as the file is under: structure
   (**delete** — the graph owns it) → narrative (`docs/CHANGELOG_INTERNAL.md`) → the full flag
   registry (`docs/FLAGS.md`) → external derivations (`docs/GROUND_TRUTH.md`) →
   single-directory rules (that directory's rulebook).

   **When the order is exhausted and the file is still over, write it anyway.** Insert this line
   directly under the title and report it:

   > ⚠ Over ceiling: `<n>`/300 — relocation exhausted. Split candidates: `<the sections>`.

   **A size rule must never cause a run to stall, refuse, or leave a repo half-written.** The
   ceiling is a budget signal, not a gate — a rulebook that is over with a visible warning is
   strictly better than no rulebook at all.
2. **Zero drifting numbers.** No test counts, migration revision numbers, rule counts, command
   counts, prices, or dates-of-verification. Where a count matters, write **the command that
   asks the code** — `alembic heads`, `pytest --collect-only -q | tail -1`, `<cli> --help`.
   This single rule is what keeps a knowledge base from quietly lying six weeks later.
3. **Per-directory files hold LOCAL RULES ONLY.** If a per-directory file would only describe
   what lives in that folder, and a graph is wired, **do not create it at all.** This excludes
   inventory files; it does not excuse skipping the phase. Architecture yields rules —
   Phase 2B derives them.

---

## Root rulebook — 14 sections, in this order

### 1. What this is
3–6 lines. The product(s), the stack, and the **design through-line** if the repo has one — a
hard-won lesson worth repeating at the top, e.g. "every feature ships behind a flag; OFF must be
byte-identical to legacy". Not a marketing paragraph.

### 2. Where things live
Pointers, one line each, to: changelog (`docs/CHANGELOG_INTERNAL.md`), facts file
(`docs/GROUND_TRUTH.md`), flags registry (`docs/FLAGS.md`), audits, archive, agent contracts
(`.claude/agents/`), the mandate template (`docs/MANDATE_TEMPLATE.md`), the loop spec
(`docs/LOOP.md`), the backlog (`HISTORIAS.md`), open gaps (`docs/IMPROVEMENTS.md`), the run log
(`docs/RUN_LOG.md`).
**Not a directory tree.** This section routes; it does not describe.

### 3. Architecture map
**ONE line per top-level directory.** End the section with, verbatim:

> For anything deeper, query the graph — do not read this file harder.

**Omit this section entirely** if `GRAPH_MODE != none` *and* the repo layout is conventional for
its stack (a standard Next.js `app/`, a standard Django project, a standard Go `cmd/`+`internal/`).
A conventional layout is already known to the model; restating it is pure cost.

### 4. Commands
The real ones, taken from the manifest scripts and the CI config — install, run, test, build,
lint, typecheck, migrate, deploy. Copy them exactly. Mark `[UNVERIFIED]` if not confirmable.
Where a command's *output* is the answer to a counting question, prefer the command over the
count (rule 2).

### 5. Conventions
**Only conventions an agent could plausibly violate.** Nothing the model already does by
default — no "use meaningful variable names", no "write tests", no "handle errors". Keep:
where business logic must live, the sync/async rule, the typing rule, the import-boundary rule,
the error-handling shape, the test layout. If removing a line would change nothing about what
the next agent writes, remove it.

### 6. Do not break
The contracts. Stable interfaces with many dependents, pipeline orders, auth priority,
storage/key invariants, wire formats, anything whose violation is expensive. Each line states
the invariant, not the history. Where a graph is wired, name the symbol so the agent can query
its blast radius rather than reading for it.

### 7. Flags / config with SURPRISING defaults
**Only the ones whose default is counter to expectation** — typically the ones that are OFF, so
an agent reasons as if they run. Format: `<name>` — default `<value>` — **the surprise**.
The full registry goes to `docs/FLAGS.md`; this section is the trap list, not the inventory.

### 8. External-source pitfalls
"Never teach these" items — things about a framework, vendor SDK, or spec that look true and are
not, each **citing the facts file** entry that proves it. One line each; the derivation lives in
`docs/GROUND_TRUTH.md`, never here.

### 9. Proven NOT bugs
**The highest-value section and the one nobody writes.** Things that look like defects and are
not, each with **why flagging it would be the error**:

```
<the thing that looks wrong> — it is <what it actually is>. Flagging it <severity> is the bug
because <reason>. — <path>:<line>
```

Example shape: *"the deprecated-looking `legacy_resolve()` call in the adapter — it is a working
compatibility shim for pre-2.x payloads. Flagging it CRITICAL is the bug because removing it
silently drops those payloads. — `src/adapters/inbound.py:88`"*

When the repo has no history of this yet, **generate the section empty with these instructions
in it**. Do not omit it and do not pad it. It fills from real findings, one at a time.

### 10. Repo traps
Test-harness landmines, import-time bindings, module-level side effects, fixtures that leak
state, anything that has cost hours. Each line: the trap, and what to do instead.

### 11. Open
One line each for open questions and known-broken areas, with a pointer to the detail. No
status narratives, no dates that will drift.

### 12. Navigation
The graph-first contract. Branch on `GRAPH_MODE`:

- **`cli`** — the verified query surface plus the re-index instruction:
  ```
  graphify query "<question>" --budget 2000    # structure, call sites, context
  graphify explain "<symbol>"                  # a node and its neighbors
  graphify path "<A>" "<B>"                    # how two things connect
  graphify affected "<symbol>" --depth 2       # blast radius before changing it
  graphify update .                            # re-index after adding/removing/renaming
  ```
  Plus the rule: **trust the graph's edges; do not read source to confirm an edge.** Read source
  only to modify it, to resolve a runtime branch the graph cannot see, or when the graph is
  silent.
- **`mcp`** — **omit the command syntax entirely.** The MCP server delivers its own usage
  instructions at session start; duplicating them is double payment. Write only the behavioral
  rule above.
- **`none`** — grep-based discipline: start from the entry points in §3, bound the search, state
  what was searched, and stop at the budget rather than widening indefinitely.

For a `small` repo, this section carries the "no graph, deliberately" paragraph and the ~100-file
revisit threshold.

### 13. Agent pipeline + scope gate
One line on the pipeline (`.claude/agents/`: analyst → senior → tester → docs-updater, this file
is their ground truth), then **the scope gate — the classification rule the main session must
read every session**:

| Scope | Looks like | Run |
|---|---|---|
| trivial | typo, one-line fix, obvious rename, comment | **no agents** — just do it |
| normal | a bounded change in one area, a small feature, a fix with a known cause | analyst → senior → tester |
| complex | crosses subsystems, changes a contract, new module, unclear root cause | full pipeline incl. docs-updater |

Without this gate a ten-line fix pays four agent contexts. That is the single largest avoidable
cost in the system, which is why it lives in the rulebook and not only in the operator manual.

### 14. Compaction
What to keep and what to discard when the session compacts. Keep: the active task's constraints,
the blast radius already derived, flag directions, decisions made and why. Discard: file contents
already summarized, resolved tool output, superseded plans, search results already acted on.
Re-query the graph rather than preserving structural context across a compaction — a query is
cheaper than the context it would have cost to carry.

---

## Per-directory rulebook

Written in Phase 2B, which also decides **which** directories earn one. At most five parts, in
this order — **omit any part with no evidence, never pad:**

1. **Purpose** — one line: what this subsystem is responsible for. Not what it contains.
2. **Local rules** — conventions that hold *here* and are not in the root file, derived by
   comparing this directory's observable patterns against the root's §Conventions.
   `[UNVERIFIED]` on anything inferred but not proven.
3. **Boundaries** — what this directory may and may not reach, and what may reach it.
   **Exceptionless patterns only** (see below). Where a graph is wired, verify with a graph query
   rather than by reading, and state **the direction, not the edge list**.
4. **Local commands** — how to run or test just this subsystem, verbatim from the manifest, CI
   config, or test layout. `[VERIFY]` if not confirmable.
5. **Local gotchas** — what has cost time here. Expected to be **empty on a fresh repo**; write
   it as an empty section with the line *"No local gotchas recorded yet — the docs-updater
   appends here as they are found."* An honest empty section is where the file grows.

**A boundary rule may only be written when the pattern is EXCEPTIONLESS.** One counter-example in
the graph and it is a tendency, not a rule — writing it anyway means the next agent "fixes"
working code. Exceptionless or unwritten.

**Structure vs. rule — the whole distinction:**

| Structure — the graph owns it, never write it | Rule — this file owns it |
|---|---|
| `api/` contains `routers/`, `services/`, `db/` | business logic lives in `services/`; routers stay thin |
| `worker/processor.py` imports `packages/core` | `worker/` may not import from `api/` — one-way boundary |
| this module has 14 functions | every I/O function here is async; sync I/O is wrapped |
| `tests/api/` exists | run only this subsystem's tests with `<command>` |

**No file listings. No inventories. No re-statement of root rules.** Skip generated/vendored dirs
entirely: `node_modules`, `.next`, `dist`, `build`, `out`, `target`, `__pycache__`, `*.egg-info`,
`vendor`, `.venv`, asset folders.

**Cap: 40 lines.** If one exceeds it, the excess is almost always structure the graph already
owns — cut it, do not raise the cap. Still over after every piece of structure is gone → **write
it anyway** with `⚠ Over cap: <n>/40 — <what could not be cut>` under the title, and report it.
Same principle as the root ceiling: a cap never blocks a write.

**Never overwritten.** An existing per-directory file is preserved on a fresh init and updated in
place only on an AUDIT run, following the Phase 1 classification.

---

## Style

- Terse, technical, bullet-dense. No emojis, no hype, no filler.
- Concrete paths and symbols (`src/api/client.ts:40`), never "the main module".
- `[UNVERIFIED]` on anything not confirmed against code or graph.
- Every line must change what the next agent does. If it would not, it does not belong.
