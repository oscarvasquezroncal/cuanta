# Internal changelog — {{PROJECT_NAME}}

**Purpose:** the narrative of all shipped work — what changed, why, what was tried and rejected,
what surprised us. This is the story. The compressed *rule* extracted from it lives in
`CLAUDE.md`; the story itself lives here.

## Budget and boundaries

- **Never auto-loaded.** No session reads this file unless someone asks for history. That is why
  it has no size ceiling.
- **Newest first.** Append at the top.
- One entry per pipeline run or shipped change.

### What does NOT belong here

- **Rules.** If a line would change what the next agent writes, it belongs in `CLAUDE.md` — not
  here, and not in both. A rule changes behavior; a story explains why the rule exists.
- **External-source derivations** — those are `docs/GROUND_TRUTH.md`, cited.
- **The flag registry** — that is `docs/FLAGS.md`.
- Transcripts, full diffs, or pasted tool output.

## Entry format

```
## <YYYY-MM-DD> — <short title>
- Shipped: <what changed, in one or two lines>
- Why: <the reason, including what problem it solved>
- Tried and rejected: <approaches that did not work, and why — this is the expensive part>
- Rule extracted: <the CLAUDE.md line this produced, or "none">
```

---

{{SEEDED_NARRATIVE}}
