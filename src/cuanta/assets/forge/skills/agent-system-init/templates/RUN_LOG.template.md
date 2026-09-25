# Run log — {{PROJECT_NAME}}

**Purpose:** the **observability** layer of this repo's harness. One append-only block per
completed mandate, so that when a run misbehaves you can read the trajectory instead of
reconstructing it from memory.

This is the record you read *after* something went wrong. It is not documentation and it is not
a changelog.

## Budget and boundaries

- **Append-only, newest at the bottom.** An entry is never edited after it is written — a
  corrected entry is a new entry.
- **Never auto-loaded.** No session reads this file unless someone asks what happened. That is
  why it has no size ceiling.
- **Written by the docs-updater**, at the end of every pipeline run, from the three agent JSONs.
- One block per **completed mandate**, not per agent invocation.

### What does NOT belong here

- **Rules.** If a line would change what the next agent writes, it belongs in `CLAUDE.md`.
- **The story of the change** — that is `docs/CHANGELOG_INTERNAL.md`. This file records *how the
  run went*, not *what shipped and why*.
- **Open gaps** — those are `docs/IMPROVEMENTS.md`, one line each.
- Transcripts, full diffs, or pasted tool output. Truncate, always.

## Entry format

```
## <YYYY-MM-DD> — <HU-NNN or "ad hoc"> — <short title>
- Scope:            trivial | normal | complex
- Phases run:       <analyst → senior → tester → docs-updater, or the subset the gate allowed>
- Files touched:    <paths, comma-separated>
- Tests:            <green | red | persistent_failure | not run — VERIFY_TIER=<tier>> (<runner>)
- Recommendations:  <IMP-NNN, IMP-NNN — or "none">
- HU:               <HU-NNN — estado after this run, or "—">
- Consumption:      run $CUANTA_RUN_ID → cuanta spectrum $CUANTA_RUN_ID
```

The `Consumption:` line is written **only when `CUANTA_RUN_ID` is set** — the mandate ran under
cuanta, which recorded every token of it. Without that variable the line is omitted, never
filled with a placeholder.

An entry whose `Tests:` line reads `not run` is not a failure of the log — it is the log doing its
job. What a repo could not prove is exactly what you need to see later.

---
