---
description: Re-verify the CLAUDE.md rulebook against code and graph, enforce the 300-line ceiling, drain queued rulebooks, re-index the graph, report drift.
---

Use the `agent-system-init` skill in REFRESH mode on this repository. This is the maintenance
path for an already-initialized repo — not a fresh init. Run it autonomously, end to end,
without stopping for approval.

1. **Re-run Phase 0 (DETECT).** Recompute `FILE_COUNT`, `SIZE_TIER`, `DOCS_STATE`, `FORGE_STATE`,
   `VERIFY_TIER`, `GRAPH_MODE`, `VCS`. Print the nine-line detection summary. If `SIZE_TIER`
   crossed a threshold since init, say so — ~100 files changes whether a graph pays, and ≥2000
   (`xl`) changes the audit and selection strategy.

   Read `.claude/forge-state.json`. It carries `dirs_queued` from a previous run — the
   directories that qualified beyond the per-run cap — and it is written after every phase here
   too, so an interrupted refresh resumes rather than restarting.

   **Compare the freshly computed `VERIFY_TIER` against the `verify_tier` stored in that file.**
   That stored value is the only reference for "changed"; without the comparison the step below
   has nothing to fire on. Different → say so explicitly
   (`verify: moderate → strong (jest added to devDependencies)`) and run step 7. No stored value
   — a repo forged before v0.3 — → treat the current tier as new and run step 7 once.

2. **Re-index the graph** (Phase 0.5, the already-wired branches only). If `GRAPH_MODE=cli`, run
   `graphify update .`. If `GRAPH_MODE=mcp`, do nothing — the server owns its own index. If
   `GRAPH_MODE=none` and the repo is now `medium`, `large`, or `xl`, install and index per Phase
   0.5 Branch D, degrading gracefully on failure. **`SIZE_TIER=small` still outranks everything
   here**: no graph, and say which condition won. Never reinstall something already present.

3. **Re-audit the rulebook (Phase 1 AUDIT).** Verify every claim against reality, using graph
   queries for structural claims rather than reading files. Classify every line:
   `RULE` keep / `STRUCTURE` delete (the graph owns it) / `NARRATIVE` relocate to
   `docs/CHANGELOG_INTERNAL.md` / `STALE` delete / `UNVERIFIABLE` keep and mark `[UNVERIFIED]`.
   **At `xl`, audit by sampling and state the sample size.** Verify against **code and graph** —
   never treat a forge-written line as evidence for itself.

4. **Enforce the 300-line ceiling by MOVING content out**, not by trimming wording, in the fixed
   priority order: structure (delete — the graph owns it) → narrative
   (`docs/CHANGELOG_INTERNAL.md`) → the full flag registry (`docs/FLAGS.md`) → external
   derivations (`docs/GROUND_TRUTH.md`) → single-directory rules (the per-directory rulebooks).
   **Order exhausted and still over → write the file anyway** with the over-ceiling warning line
   and report it. The ceiling never blocks the run.

5. **Regenerate the four agents** in `.claude/agents/` **from the current templates** — always,
   not only when the stack changed. Fill them per Phase 3, with the navigation contract matching
   the current `GRAPH_MODE` and the tester's contract matching the current `VERIFY_TIER`, then
   scan for surviving `{{placeholders}}` and fail loudly on any.

   **Never overwrite.** If a regenerated agent differs from the file on disk, write it to
   `<name>.new.md`, preserve the original, and report both paths so the user can diff and merge.

   This step is why `/refresh-agents` exists. A repo initialized under an older version carries
   the **old docs-updater** — the one with no budget, whose knowledge base grows forever, and with
   no per-directory routing. If refresh only re-verified docs and re-indexed the graph, those
   users would keep that defect permanently with no way to learn it.

6. **Write missing per-directory rulebooks (Phase 2B), and DRAIN `dirs_queued` FIRST.** The
   directories a previous run deferred past its 20-rulebook cap are the first batch here. Then
   re-run the selection: directories that now qualify and have no `CLAUDE.md` get one, in the
   five-part shape, under the 40-line cap. Same discipline as init — **batches of at most 6, state
   file updated after each batch, evidence released between batches, 20 per run, the rest
   re-queued and reported.** Existing per-directory files are updated in place per the Phase 1
   classification, never overwritten. Report the rejections with their reasons.

7. **Re-run Phase 6 when `VERIFY_TIER` changed** (per the comparison in step 1) — installing a
   test runner is exactly the change that should upgrade the loop spec, and a runner that was
   removed must downgrade it. Three things happen together:

   - **Rewrite `docs/LOOP.md` to the current tier.** An unattended sequence may be described
     **only** at `strong`; `moderate` and `weak` state that no unattended loop is sanctioned.
   - **Refill the tester's `{{VERIFY_TIER_CONTRACT}}`** from the new tier, **under the `.new.md`
     policy** — the regenerated tester goes to `tester.new.md` if it differs from the file on
     disk, never overwriting a tuned one.
   - **Rose to `strong` → close the missing-runner improvement**: check its box in
     `docs/IMPROVEMENTS.md`, move it to `docs/CHANGELOG_INTERNAL.md` with the runner that closed
     it, and say so. The gap is gone; leaving it open teaches the reader the file is stale.
     Fell to `moderate`/`weak` → seed it, once, per the idempotence guard in Phase 6 Step 3.

   Unchanged tier → leave `docs/LOOP.md` alone. Then write the current `verify_tier` back to
   `.claude/forge-state.json` so the next refresh has a reference. Create the harness/loop files
   if the repo predates them: `docs/RUN_LOG.md`, `docs/IMPROVEMENTS.md`, `HISTORIAS.md`,
   `docs/LOOP.md`, and the `AGENTS_GUIDE.md` §Harness section.

8. **Report drift**: claims that had gone false, structure removed, content moved and where,
   rulebook line count before → after against the 300 ceiling (and the warning line if it is
   over), `GRAPH_MODE` and `VERIFY_TIER` outcomes, the per-directory files written / preserved /
   skipped-with-reason / still queued, any `<name>.new.md` files left for review, whether the loop
   spec was rewritten, and any `[UNVERIFIED]` / `[VERIFY]` items. End with the registration
   reminder: a plugin's skill and commands do not resolve in the session that installed them —
   restart before the next command.

Hard rules: evidence only; documentation and agent files only (never source, configs, or tests);
no size rule ever blocks the run; no unattended loop sanctioned below `VERIFY_TIER=strong`;
**no git operations of any kind** — `.gitignore` is a file edit, not a git command. Since this
runs without an approval gate, commit or stash first so there is a clean rollback point.
