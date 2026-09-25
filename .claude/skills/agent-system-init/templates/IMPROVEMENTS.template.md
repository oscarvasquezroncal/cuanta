# Improvements — open gaps found by the pipeline ({{PROJECT_NAME}})

**Purpose:** the FINAL REPORT's recommendations are real findings — a missing test runner, an
unguarded boundary, a fixture that leaks state. Without a home they evaporate into chat
scrollback and the next mandate rediscovers them at full price. This file is that home.

**It is a list of OPEN gaps.** A resolved item is checked and moved out on the next cycle, so the
length of this file is the size of the debt, not the size of the history.

## Budget and boundaries

- **HARD CAP: 15 unchecked entries.** This is the ceiling that keeps the continuity layer from
  growing the way an unbudgeted knowledge base does. Over it, the docs-updater consolidates at
  the end of the cycle — merge duplicates, close what has gone stale (**90 days** untouched),
  then demote the lowest-severity `low` items, oldest first, to `## Deferred` at the bottom.
  **Nothing is ever silently dropped**, and the cap never blocks a cycle: if consolidation is
  exhausted the file is written anyway with an over-cap warning under its title.
- **`## Deferred` is not loaded.** The analyst reads the unchecked entries above it and never
  reads that section. Deferred items are still here, still findable, still yours to promote back —
  they simply stop costing every mandate that follows.
- **One line per item.** The detail lives in `docs/CHANGELOG_INTERNAL.md`; the rule it produces
  lives in `CLAUDE.md`.
- **Appended by the docs-updater**, from the tester's and senior's JSONs — never from a guess.
- **Never invented to fill space.** An empty improvements file is an honest state and the correct
  output of a clean run. Padding it destroys the signal that makes it worth reading.
- **Read at the start of every mandate** by the architecture-analyst, together with the open
  entries in `HISTORIAS.md` — **at most 15 per file**. It returns `related_improvements` in its
  plan JSON.
- **Not auto-loaded** into every session. It is read at plan time.

### What does NOT belong here

- **A story** — why the gap exists is `docs/CHANGELOG_INTERNAL.md`.
- **A rule** — if it changes what the next agent writes, it is a `CLAUDE.md` line, not a gap.
- **A user story** — what the user wants next is `HISTORIAS.md`. This file holds what the *system*
  found while doing something else.
- A wish, a preference, or a refactor with no evidence behind it.

## Entry format

```
- [ ] IMP-004 — <one line> — <path>:<line> — found <date> in <HU-NNN|init> — severity: low|med|high
```

`severity: high` is reserved for gaps that block proof — no test runner, a suite that cannot run
offline, a boundary with no guard. A missing docstring is `low` and probably does not belong here
at all.

## Closing an item

Check the box, move the line into `docs/CHANGELOG_INTERNAL.md` with what actually fixed it, and
delete it here **on the next cycle**. Two states only: open in this file, or closed in the
changelog. Never both.

An item closed by consolidation rather than by work says so, and still goes to the changelog:

```
- [x] IMP-004 — <one line> — wontfix — stale (untouched 90d, closed <date>)
- [x] IMP-009 — <one line> — merged into IMP-004
```

---

{{SEEDED_IMPROVEMENTS}}

## Deferred

Below the cap's line. Kept, not loaded, not lost — promote one back by moving it above this
heading. Empty is the normal state.
