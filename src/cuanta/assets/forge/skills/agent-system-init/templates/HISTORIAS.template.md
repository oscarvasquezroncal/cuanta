# User stories — {{PROJECT_NAME}}

**Purpose:** the backlog, and the loop's memory. Each mandate **reads the open entries before
planning** and **writes its outcome back at the end**. A write-only backlog is not memory — it is
a log nobody reads, and it makes every mandate re-derive context a previous one already paid for.

## Budget and boundaries

- **HARD CAP: 15 OPEN entries** (`pendiente` + `en progreso` combined). A backlog above that is
  not memory, it is a bill every future mandate pays before it starts. Over it, the docs-updater
  consolidates at the end of the cycle — merge duplicates, close what has gone stale (**90 days**
  untouched → `descartada — stale`), then move the oldest `pendiente` entries to `## Deferred` at
  the bottom. **Nothing is ever silently dropped**, and the cap never blocks a cycle: if
  consolidation is exhausted the file is written anyway with an over-cap warning under its title.
- **`## Deferred` is not loaded.** The analyst reads the open entries above it and never reads
  that section. A deferred story is still here and still yours to promote back — it simply stops
  costing every mandate that follows.
- **Read-write.** The architecture-analyst reads the entries whose `Estado` is `pendiente` or
  `en progreso` at the start of a mandate — **at most 15** — and returns `related_stories` in its
  plan JSON. The docs-updater writes the outcome back at the end, within its 3-line budget.
- **Three lines of substance per entry. No transcripts.** If it needs a paragraph, the paragraph
  belongs in `docs/CHANGELOG_INTERNAL.md` and this entry points at it.
- **Fixed shape.** The format below is parsed, not just read — keep the field names exactly.
- **Never auto-loaded** into every session. It is read at plan time.

### What does NOT belong here

- **Gaps the system found on its own** — those are `docs/IMPROVEMENTS.md`. This file holds what a
  human asked for.
- **The story of how it was built** — `docs/CHANGELOG_INTERNAL.md`.
- **Rules** — `CLAUDE.md`.
- Status narratives, progress percentages, or dates that will drift out of the `Estado` line.

## Entry format

```
## HU-007 — <short title>
- Estado: pendiente | en progreso | hecha | descartada   (YYYY-MM-DD)
- Contexto: <one line: the symptom or the want>
- Depende de: HU-003 | —
- Cierre: <audit/changelog link, when hecha>
```

`hecha` and `descartada` entries stay in the file — they are the trail — but they are **not read
at plan time**. Only open entries are.

A story closed by consolidation rather than by work says which:

```
- Estado: descartada — stale (untouched 90d)   (YYYY-MM-DD)
- Estado: descartada — merged into HU-003      (YYYY-MM-DD)
```

---

{{SEEDED_STORIES}}

## Deferred

Below the cap's line. Kept, not loaded, not lost — promote one back by moving it above this
heading. Empty is the normal state.
