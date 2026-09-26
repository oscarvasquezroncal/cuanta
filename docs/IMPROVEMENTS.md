# Improvements — cuanta

**Budget:** open gaps the pipeline found, one line each, **max 15 unchecked entries** (the
docs-updater consolidates: merge duplicates → close 90-day stale → demote oldest `low` to
`## Deferred`; still over → write anyway with a ⚠ line). Appended only from agent JSONs — never
invented. A resolved item is checked, moved to `docs/CHANGELOG_INTERNAL.md` next cycle, and
deleted here. What does NOT belong here: detail (changelog), stories (`HISTORIAS.md`).

**Entry format**

```
- [ ] IMP-NNN — <one line> — <path>:<line> — found <date> in <HU-NNN|init> — severity: low|med|high
```

---

Initialized empty — `VERIFY_TIER=strong`, so no missing-runner entry was seeded. Findings below
come from the M0 verification pass.

- [ ] IMP-001 — Codex CLI runs without a reported cost can retain the generic model label `codex`, so the price lookup misses — src/cuanta/adapters/engines/codex.py:105 — found 2026-09-25 in init — severity: med

## Deferred
