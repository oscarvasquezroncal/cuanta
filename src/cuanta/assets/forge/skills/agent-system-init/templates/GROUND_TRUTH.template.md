# Ground truth — {{FRAMEWORK_OR_PROJECT}}

**Purpose:** every reading of an external source tree — framework internals, vendor SDK, a spec —
is **paid once and cited forever**. This file is where that payment is banked. Before deriving
anything about an external dependency, read this file. If the answer is here, use it and read
nothing.

## Budget and boundaries

- **Append-only.** An entry is **never rewritten**. When it becomes wrong, add a new dated line
  and mark the old one `[superseded: <reason>]`. The old line stays — the reasoning trail is the
  point.
- **Every entry cites its source.** A claim without `path:line` is not a fact, it is a memory.
- **Not auto-loaded.** Read on demand. That is why it may grow without a ceiling.

### What does NOT belong here

- Anything about **our own code** — that is the graph (structure) or `CLAUDE.md` (judgment).
- **Narrative** about what we shipped — that is `docs/CHANGELOG_INTERNAL.md`.
- **Our own flags and config** — that is `docs/FLAGS.md`.
- Anything not derived from reading an **external** source tree or spec.

## Entry format

```
<claim> — <source path>:<line> — <YYYY-MM-DD> [superseded: <reason>]
```

Grouped by subject.

---

{{SEEDED_ENTRIES}}
