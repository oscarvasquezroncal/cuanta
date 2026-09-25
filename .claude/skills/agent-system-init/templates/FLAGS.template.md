# Flags and config registry — {{PROJECT_NAME}}

**Purpose:** the **full** inventory of feature flags, environment variables, and config keys.
`CLAUDE.md` keeps only the ones whose default is *surprising*; everything else lives here.

## Budget and boundaries

- **Not auto-loaded.** Read on demand, so it may hold the complete registry.
- One row per flag. Keep the table sorted by name.
- **The `Surprising?` column is the routing decision.** Every `yes` row must have a matching
  one-line trap entry in `CLAUDE.md` §Flags with surprising defaults — and *only* those rows.

### What does NOT belong here

- **Why a flag exists / the story of its rollout** — that is `docs/CHANGELOG_INTERNAL.md`.
- **External framework config semantics** — that is `docs/GROUND_TRUTH.md`, cited.
- Structure — where the flag is read from is a graph query, not a doc line.

## Registry

| Flag / key | Default | Where read | Surprising? | Effect when ON |
|---|---|---|---|---|
{{SEEDED_FLAGS}}

## Rules

- A flag whose default is **OFF** is usually surprising: an agent reasons as if the code runs.
  Mark it `yes` and mirror it into `CLAUDE.md`.
- **Gate OFF must be byte-identical to legacy.** If a flag's OFF path is not byte-identical,
  that is a defect, not a config note — record it in `CLAUDE.md` §Do not break.
- Never record a flag *count* anywhere. Counts drift; the table is the answer.
