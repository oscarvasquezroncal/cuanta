# Agent system — operator manual ({{PROJECT_NAME}})

**This file is for you, not for the agents.** No agent reads it. The agents' contracts are in
`.claude/agents/`; the rules they follow are in `CLAUDE.md`. This is the manual for driving them.

---

## 1. The scope gate — read this before anything else

Most daily work does not need the pipeline. Be honest about which bucket you are in:

| Scope | Looks like | Run | Cost |
|---|---|---|---|
| **trivial** | typo, one-line fix, obvious rename, comment | **no agents** — just ask directly | one context |
| **normal** | bounded change in one area, small feature, fix with a known cause | analyst → {{SENIOR_NAME}} → tester | three contexts |
| **complex** | crosses subsystems, changes a contract, new module, unclear root cause | full pipeline incl. docs-updater | four contexts |

**Most of what you do every day is trivial or normal.** Running the full pipeline on a ten-line
fix pays four agent contexts for work that needed one. That is the single largest avoidable cost
in this system — larger than any model choice.

---

## 2. How to run it

Open `docs/MANDATE_TEMPLATE.md`, copy the whole block, fill the `REQUEST` block at the bottom,
paste it as your message. Change nothing above the REQUEST block — the EXECUTION CONTRACT is what
keeps the run continuous.

### Filling the request block

- **WHY / EVIDENCE — paste it, never describe it.** Paste the actual traceback, the actual failing
  test output, the actual log line. "The login is broken sometimes" costs the analyst its entire
  read budget rediscovering what your clipboard already had.
- **OUT OF SCOPE — always fill it.** This is the cheapest field in the template and the one that
  prevents the most rework. "Do not touch the auth middleware" saves more than any instruction
  you can add elsewhere.
- **WHERE** — "unknown" is a valid and honest answer. A wrong guess is worse than none; it sends
  the analyst to the wrong place with confidence.
- **CONSTRAINTS** — flag directions especially. "Flag stays OFF by default and OFF must be
  byte-identical" is a constraint the senior enforces literally.

---

## 3. The pipeline

```
architecture-analyst  →  {{SENIOR_NAME}}  →  tester  →  docs-updater
     (map + plan)          (implement)       (prove)     (route + prune)
```

- **architecture-analyst** (`sonnet`) — reads the open entries in `HISTORIAS.md` and
  `docs/IMPROVEMENTS.md`, then queries the graph and produces the plan JSON with a complete
  `blast_radius`. Writes no code. A STOP from this agent (falsified premise, contract conflict,
  already handled) is a **successful outcome** — it prevented a wrong implementation.
- **{{SENIOR_NAME}}** (`opus`) — implements one phase per invocation from the plan. Never
  re-derives the blast radius.
- **tester** (`sonnet`) — writes and actually runs tests, all output truncated. Red blocks the
  docs step. What it is entitled to claim depends on this repo's `VERIFY_TIER` — see §The harness.
- **docs-updater** (`haiku`) — works from the three JSONs only. Routes what was learned to seven
  destinations — the rulebook, the per-directory files, the changelog, the facts file, the
  backlog, `docs/IMPROVEMENTS.md` and `docs/RUN_LOG.md` — and enforces the rulebook's 300-line
  ceiling by **moving content out**, without ever blocking the write.

---

## 4. If a run stops to ask mid-flight

Reply exactly:

> Continue autonomously per the EXECUTION CONTRACT.

**Change nothing else.** Do not re-explain the task, do not add a new constraint, do not answer
the question it asked. Re-stating the task mid-run replaces the context it already built; the one
line above restores the contract without costing anything.

---

## 5. Cost dials — in the correct order

Turn them in this order. The first two are worth more than the last two combined.

1. **The scope gate.** Not running three agents on a trivial task saves more than every other
   dial. Start here, always.
2. **Batch the docs-updater.** It does not need to run after every small change. Let two or three
   normal-scope runs accumulate and run it once over them. Its budget is per cycle, so fewer
   cycles is strictly cheaper.
3. **The analyst's model tier.** On a repo with a wired graph the analyst is doing bounded
   queries against structured output — a cheaper tier handles it well. Try it before touching
   the senior.
4. **The senior's model tier — LAST.** This is the only agent writing production code. Downgrading
   it saves the least and costs the most: you pay it back in review time, in rework, and in the
   bugs a cheaper tier ships quietly. Turn this dial only after the first three.

Also: `/clear` between unrelated tasks, `/usage` to track spend.

---

## 6. The knowledge split — where things go

| | Graph | `CLAUDE.md` (rulebook) | `docs/GROUND_TRUTH.md` |
|---|---|---|---|
| Holds | what the code IS | what it SHOULD be and WHY | expensive external derivations |
| Ages | no — re-index | yes — has a 300-line ceiling | no — append-only, cited |
| Cost | ~2k per query | every message | on demand |

Plus `docs/CHANGELOG_INTERNAL.md` (the narrative, never auto-loaded) and `docs/FLAGS.md` (the
full registry; the rulebook keeps only the surprising defaults).

**Nothing is stored in two places.** If you find yourself writing structure into `CLAUDE.md`,
that is the graph's job and the line should not exist.

{{GRAPH_NOTE}}

---

## 7. The harness — what this repo now has

What the pieces above add up to has a name. Each layer, and the artifact that implements it:

| Layer | Implemented by |
|---|---|
| Tool orchestration | the four agents + the scope gate |
| Verification | the tester's bar, `VERIFY_TIER` = **`{{VERIFY_TIER}}`** |
| Context & memory | graph / rulebook / facts file / changelog |
| Guardrails | §Do not break, gate-OFF byte-identical, exceptionless boundaries |
| Observability | `docs/RUN_LOG.md` |

**`docs/RUN_LOG.md`** is append-only and **never auto-loaded** — one block per completed mandate:
date, scope, phases run, files touched, tests result, recommendations raised, HU reference. You
do not read it during normal work. You read it when a mandate misbehaved and you need the
trajectory rather than your memory of it.

---

## 8. The loop — and what your verification tier permits

Above the harness sits the loop: how one mandate hands off to the next. It is specified in
**`docs/LOOP.md`** — trigger, goal, verification, stopping rule, memory.

**The gate is `VERIFY_TIER`, and it is not negotiable.** In any loop the verifier is the
bottleneck, not the model; a loop running unattended is a loop making mistakes unattended. So:

| Tier | What is sanctioned |
|---|---|
| `strong` | a test runner (with or without typecheck/build; a missing one is recorded as `no typecheck`) → an unattended sequence may be described, with human review at the diff |
| `moderate` | typecheck/lint/build only → **no unattended loop.** Compiling is not behaving. You review every mandate before the next begins, and "install a test runner" is the top item in `docs/IMPROVEMENTS.md` |
| `weak` | build only, or nothing verifiable → same, in stronger terms. Get a sensor first |

This repo is **`{{VERIFY_TIER}}`**. Installing a test runner is the change that upgrades the loop
itself — after it, `/refresh-agents` re-runs the loop spec.

**The loop's memory** is two files, both read at the start of a mandate and written at the end:

- **`HISTORIAS.md`** — the backlog. The analyst reads the **open** entries (`pendiente`,
  `en progreso`) and returns `related_stories`, so a mandate does not re-derive context a
  previous one already established.
- **`docs/IMPROVEMENTS.md`** — the open gaps the pipeline found itself, one line each. A
  recommendation in a FINAL REPORT that is not in this file evaporates when the chat scrolls.

The FINAL REPORT's last section, **NEXT**, proposes the next mandate from these two files with a
filled REQUEST block. It **proposes only** — nothing starts without you.

---

## 9. Keeping it honest

Run **`/refresh-agents`** when the repo has drifted — after a big merge, a refactor, or a few
weeks of normal work. It re-verifies the rulebook against code and graph, enforces the 300-line
ceiling by moving content out, **regenerates the four agents from the current templates**, writes
any per-directory rulebook a directory has since earned, **drains the queue** of directories a
previous run deferred past its per-run cap, **re-runs the loop spec when `VERIFY_TIER` changed**
(installing a test runner upgrades what the loop may do), re-indexes the graph, and reports drift.

**After installing or updating the plugin, restart Claude Code before running either command.**
A plugin's skill and slash commands are not registered in the session that installed them —
`Error: Unknown skill` in a fresh install is that, not a broken tool.

**It never overwrites a file you may have tuned.** A regenerated agent that differs from the one
on disk lands at `<name>.new.md` with the original preserved and both paths reported — diff and
merge at your pace. Existing per-directory rulebooks are updated in place, never replaced.

Signals you are overdue: an agent contradicts `CLAUDE.md`, the rulebook is near 300 lines, or a
graph query returns symbols that no longer exist.

---

## 10. Optional — the expensive-plan opt-in

For architecture-defining work where you genuinely want to read the plan before the expensive
model spends, add this one line to the REQUEST block:

> `PLAN REVIEW: stop after the analyst and show me the plan JSON before implementing.`

This is the **only** sanctioned pause. Use it for a new subsystem or a contract change — not for
routine work, where it just converts an autonomous run into a manual one.

---

## 11. Before each run

- Commit or stash first. The agents edit files; you want a rollback point. **The agents never run
  git themselves** — that is yours.
- Review the diff afterward. The run is autonomous, so the diff is your safety net.
- First time on a new area: run something small to watch the handoff behave.

## 12. Files

- Agents: `.claude/agents/architecture-analyst.md`, `{{SENIOR_NAME}}.md`, `tester.md`,
  `docs-updater.md`
- Rulebook: root `CLAUDE.md` + per-directory `CLAUDE.md`
- Mandate: `docs/MANDATE_TEMPLATE.md`
- Loop: `docs/LOOP.md`
- Support: `docs/GROUND_TRUTH.md`, `docs/CHANGELOG_INTERNAL.md`, `docs/FLAGS.md`
- Memory + observability: `HISTORIAS.md`, `docs/IMPROVEMENTS.md`, `docs/RUN_LOG.md`
- Run state (disposable, git-ignored): `.claude/forge-state.json`

## 13. User stories — the backlog loop

Say "I have a user story: <describe it>" at any time. It is recorded in `HISTORIAS.md` as
`HU-NNN` and turned into a filled mandate you can run. After one completes you get an honest
self-review — including anything that came out fragile even if the build passed — and a proposed
next step. **You always decide**; nothing auto-starts.

`HISTORIAS.md` is **read as well as written**: the analyst reads the open entries before planning,
so the next mandate starts from what the last one established. Keep the entry shape intact —
`Estado` / `Contexto` / `Depende de` / `Cierre` — it is parsed, not just read.

## 14. A run that was interrupted

`/init-agents` writes `.claude/forge-state.json` after every phase and every batch of
per-directory rulebooks. If a run dies part-way — context exhausted, a crash, you stopped it —
**run it again**: it resumes at the first incomplete phase and announces `resuming from Phase <n>`
instead of redoing what is already on disk. The file is pure bookkeeping; deleting it just forces
a clean start.
