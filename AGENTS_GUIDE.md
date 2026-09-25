# Agent system — operator manual (cuanta)

**Budget:** operator manual only. **No agent reads this file.** The agents' contracts are in
`.claude/agents/`; the rules they follow are in `CLAUDE.md`. What does NOT belong here: rules,
facts, backlog.

---

## 1. The scope gate — read this before anything else

Most daily work does not need the pipeline. Be honest about which bucket you are in:

| Scope | Looks like | Run | Cost |
|---|---|---|---|
| **trivial** | typo, one-line fix, obvious rename | **no agents** — just ask directly | one context |
| **normal** | bounded change in one area, small feature, fix with a known cause | analyst → python-senior → tester | three contexts |
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

- **WHY / EVIDENCE — paste it, never describe it.** The actual traceback, failing test output,
  log line.
- **OUT OF SCOPE — always fill it.** The cheapest field and the one that prevents the most rework.
- **WHERE** — "unknown" is a valid and honest answer. A wrong guess is worse than none.
- **CONSTRAINTS** — flag directions especially. "Flag stays OFF by default and OFF must be
  byte-identical" is a constraint the senior enforces literally.

---

## 3. The pipeline

```
architecture-analyst  →  python-senior  →  tester  →  docs-updater
     (map + plan)          (implement)       (prove)     (route + prune)
```

- **architecture-analyst** (`sonnet`) — reads the open entries in `HISTORIAS.md` and
  `docs/IMPROVEMENTS.md`, then queries the graph and produces the plan JSON with a complete
  `blast_radius`. Writes no code. A STOP (falsified premise, contract conflict, already handled)
  is a **successful outcome**.
- **python-senior** (`opus`) — implements one phase per invocation from the plan. Never
  re-derives the blast radius.
- **tester** (`sonnet`) — writes and actually runs tests, all output truncated. Red blocks the
  docs step. What it may claim depends on `VERIFY_TIER` — see §7.
- **docs-updater** (`haiku`) — works from the three JSONs only. Routes what was learned to seven
  destinations — the rulebook, the per-directory files, the changelog, the facts file, the
  backlog, `docs/IMPROVEMENTS.md` and `docs/RUN_LOG.md` — and enforces the 300-line ceiling by
  **moving content out**, without ever blocking the write.

---

## 4. If a run stops to ask mid-flight

Reply exactly:

> Continue autonomously per the EXECUTION CONTRACT.

**Change nothing else.** Re-stating the task mid-run replaces the context it already built.

---

## 5. Cost dials — in the correct order

1. **The scope gate.** Start here, always.
2. **Batch the docs-updater.** Let two or three normal-scope runs accumulate and run it once.
3. **The analyst's model tier.** With a wired graph it does bounded queries — a cheaper tier
   handles it well.
4. **The senior's model tier — LAST.** The only agent writing production code; downgrading it
   saves the least and costs the most.

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

**Graph: `GRAPH_MODE=cli` (graphify).** The index lives in `graphify-out/` (git-ignored). Agents
use `graphify query`, `graphify explain`, `graphify path` and `graphify affected … --depth 2`.
The docs-updater runs `graphify update .` after any change that adds, removes, moves or renames
a symbol or file; run it yourself after large manual edits. No LLM or API key needed.

---

## 7. The harness — what this repo now has

| Layer | Implemented by |
|---|---|
| Tool orchestration | the four agents + the scope gate |
| Verification | the tester's bar, `VERIFY_TIER` = **`strong`** |
| Context & memory | graph / rulebook / facts file / changelog |
| Guardrails | §Do not break, gate-OFF byte-identical, exceptionless boundaries |
| Observability | `docs/RUN_LOG.md` |

**`docs/RUN_LOG.md`** is append-only and **never auto-loaded** — one block per completed mandate.
You read it when a mandate misbehaved and you need the trajectory.

---

## 8. The loop — and what your verification tier permits

Above the harness sits the loop, specified in **`docs/LOOP.md`** — trigger, goal, verification,
stopping rule, memory.

| Tier | What is sanctioned |
|---|---|
| `strong` | test runner **and** typecheck/build → an unattended sequence may be described, with human review at the diff |
| `moderate` | typecheck/lint/build only → **no unattended loop.** Compiling is not behaving |
| `weak` | build only, or nothing verifiable → get a sensor first |

This repo is **`strong`** (pytest + mypy strict + ruff, gated in CI).

**The loop's memory:** `HISTORIAS.md` (the backlog; analyst reads open entries) and
`docs/IMPROVEMENTS.md` (open gaps). The FINAL REPORT's **NEXT** section proposes the next mandate
from these two files — it **proposes only**.

---

## 9. Keeping it honest — `/refresh-agents`

Run **`/refresh-agents`** after a big merge, a refactor, or a few weeks of work. It re-verifies
the rulebook against code and graph, enforces the 300-line ceiling by moving content out,
**regenerates the four agents from the current templates under the `.new.md` policy**, writes any
per-directory rulebook a directory has since earned, **drains `dirs_queued` from
`.claude/forge-state.json`**, **re-runs the loop spec when `VERIFY_TIER` changed**, re-indexes the
graph, and reports drift. The command ships at `.claude/commands/refresh-agents.md` (and with the
claude-agent-forge plugin).

**After installing or updating the plugin, restart Claude Code before running either command.**
A plugin's skill and commands are not registered in the session that installed them —
`Error: Unknown skill` in a fresh install is that, not a broken tool.

**It never overwrites a file you may have tuned.** A regenerated agent that differs lands at
`<name>.new.md` with the original preserved. Existing per-directory rulebooks are updated in
place, never replaced.

Signals you are overdue: an agent contradicts `CLAUDE.md`, the rulebook is near 300 lines, or a
graph query returns symbols that no longer exist.

---

## 10. Optional — the expensive-plan opt-in

Add this line to the REQUEST block for architecture-defining work:

> `PLAN REVIEW: stop after the analyst and show me the plan JSON before implementing.`

The **only** sanctioned pause. Use it for a new subsystem or a contract change.

---

## 11. Before each run

- Commit or stash first — you want a rollback point. **The agents never run git themselves.**
- Review the diff afterward. The run is autonomous, so the diff is your safety net.
- First time on a new area: run something small to watch the handoff behave.

## 12. Files

- Agents: `.claude/agents/architecture-analyst.md`, `python-senior.md`, `tester.md`,
  `docs-updater.md`
- Rulebook: root `CLAUDE.md` + `src/cuanta/domain/`, `src/cuanta/adapters/`, `src/cuanta/tui/`,
  `tests/` `CLAUDE.md`
- Mandate: `docs/MANDATE_TEMPLATE.md` · Loop: `docs/LOOP.md`
- Support: `docs/GROUND_TRUTH.md`, `docs/CHANGELOG_INTERNAL.md`, `docs/FLAGS.md`
- Memory + observability: `HISTORIAS.md`, `docs/IMPROVEMENTS.md`, `docs/RUN_LOG.md`
- Run state (disposable, git-ignored): `.claude/forge-state.json`

## 13. User stories — the backlog loop

Say "I have a user story: <describe it>" at any time. It is recorded in `HISTORIAS.md` as
`HU-NNN` and turned into a filled mandate. After one completes you get an honest self-review and
a proposed next step. **You always decide**; nothing auto-starts. Keep the entry shape intact —
`Estado` / `Contexto` / `Depende de` / `Cierre` — it is parsed.

## 14. A run that was interrupted

`/init-agents` writes `.claude/forge-state.json` after every phase and every batch of
per-directory rulebooks. If a run dies part-way, **run it again**: it resumes at the first
incomplete phase. Deleting the file forces a clean start.
