---
description: Standardize this repo — wire a code graph, generate the CLAUDE.md rulebook, the 4-agent pipeline and the loop spec (runs autonomously, resumable).
---

Use the `agent-system-init` skill to standardize this repository for multi-agent development. Run
its full ten-phase method autonomously, end to end, without stopping for approval:

1. **Phase 0 — DETECT.** Stack, versions, package manager, test runner, entry points. Compute
   `FILE_COUNT`, `SIZE_TIER` (`small` <100 · `medium` <500 · `large` <2000 · **`xl` ≥2000**),
   `DOCS_STATE`, **`FORGE_STATE`**, **`VERIFY_TIER`**, `GRAPH_MODE`, `VCS`. Print the nine-line
   summary.

   **Read `.claude/forge-state.json` first.** If a previous run left phases incomplete, **resume
   from the first incomplete phase** and announce `resuming from Phase <n>` — do not redo
   completed work. A corrupt state file is discarded with a note, never a hard failure. Write the
   state file after **every** phase and after **every** Phase 2B batch, and add it to
   `.gitignore` (a file edit, never a git command).

   **`FORGE_STATE=initialized`** — this repo has already been forged → run **refresh semantics**:
   verify against code and graph, never re-classify a rulebook a previous forge run wrote, never
   overwrite tuned agents, and say `forge: initialized — running refresh semantics`.

2. **Phase 0.5 — GRAPH BOOTSTRAP.** Branch strictly, and **the size gate outranks mode
   detection**: `SIZE_TIER=small` skips the graph even when graphify or a graph MCP server is
   present. Say which condition won (`skipped (small tier; graphify present but not indexed for
   this repo)`). Otherwise: defer to an already-configured MCP server, re-index an existing CLI,
   or install and index in one non-interactive step. **On any install failure, degrade to
   `GRAPH_MODE=none` and continue** — a failed graph install must never abort the run.

3. **Phase 1 — AUDIT or SEED.** Verify existing claims (via graph queries where a graph is
   wired) and classify every line: keep rules, delete structure, relocate narrative, delete
   stale, mark unverifiable. If there is no CLAUDE.md, infer a base from the code and mark
   inferences `[UNVERIFIED]`. **At `xl`, audit by sampling** — a representative sample plus the
   graph's high-degree nodes — and **state the sample size**. Never report a sampled audit as
   complete.

4. **Phase 2A — ROOT RULEBOOK.** The root `CLAUDE.md` per the spec: 300-line ceiling, zero
   drifting numbers, local rules only per directory. **Over the ceiling → relocate in the fixed
   order (structure → narrative → flags → external derivations → single-directory rules), then
   write the file anyway with the over-ceiling warning line.** A size rule never blocks a run.

5. **Phase 2B — PER-DIRECTORY RULEBOOKS.** Select the directories that earn one (cohesive
   boundary, conventions differing from the root's, a seam, or their own run/test command) — **at
   `xl`, from the graph only, never by walking the tree** — then derive **rules, never structure**.
   **Batches of at most 6**: write the files, update the state file, and **release that batch's
   evidence before the next**. **Hard cap of 20 per run**; the rest go to `dirs_queued`, are
   reported, and are drained by the next `/refresh-agents`. Report the rejections with their
   reasons; never overwrite an existing file.

6. **Phase 2.5 — FACTS FILE.** Create `docs/GROUND_TRUTH.md`, append-only and cited.

7. **Phase 3 — AGENTS.** Fill and write the four agents to `.claude/agents/`, with the navigation
   contract matching `GRAPH_MODE` and the tester's contract filled from `VERIFY_TIER`. Fail loudly
   on any surviving `{{placeholder}}`. An existing agent that differs is preserved; the new
   version goes to `<name>.new.md`.

8. **Phase 4 — GUIDE + MANDATE + MEMORY.** Write `AGENTS_GUIDE.md` (including the **§Harness**
   table), `docs/MANDATE_TEMPLATE.md`, `docs/CHANGELOG_INTERNAL.md`, `docs/FLAGS.md`,
   `docs/RUN_LOG.md`, `docs/IMPROVEMENTS.md`, and `HISTORIAS.md` — each with its budget header.

9. **Phase 5 — REFRESH PATH.** Ensure `/refresh-agents` is available and documented, and **state
   the registration step**: a plugin's skill and commands do not resolve in the session that
   installed them — restart before the next command. If this run had to read `SKILL.md` off disk
   because the command did not resolve, report `registration: deferred`.

10. **Phase 6 — LOOP SPEC + COMPLETION.** Write `docs/LOOP.md` — trigger, goal, verification,
    stopping rule, memory — **written to this repo's `VERIFY_TIER`**: an unattended sequence may
    be described only for `strong`; `moderate` and `weak` state plainly that no unattended loop is
    sanctioned, because compiling is not behaving, and get an automatic "install a test runner"
    entry in `docs/IMPROVEMENTS.md`. Then assert every artifact class actually exists — graph,
    four placeholder-free agents, root rulebook, per-directory rulebooks (with a named reason for
    every skip and every queued directory), support artifacts, harness + loop files, run state —
    fixing anything that fails before printing the closing summary.

Afterwards (optional, never blocking): offer to turn my next piece of work into a mandate,
recorded in `HISTORIAS.md` as `HU-NNN` in the fixed `Estado` / `Contexto` / `Depende de` /
`Cierre` shape. Ask before running it; never auto-start.

Follow the skill's hard rules: evidence only (mark unverifiable items `[UNVERIFIED]`, and write
any unverifiable install command as `[VERIFY: ...]` rather than guessing); docs + agent files
only (do not modify source during init); nothing stored in two places; **no size rule ever blocks
a run**; **no autonomy without a sensor**; **no git operations of any kind** — `.gitignore` is a
file edit, not a git command; and honest scope — this standardizes and improves consistency, it
does not guarantee error-free code.

Since this runs without an approval gate, commit or stash first so there is a clean rollback
point, and review the diff afterward.
