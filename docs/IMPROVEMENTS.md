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

Starts empty — `VERIFY_TIER=strong`, so no missing-runner entry is seeded. Fills from the
pipeline's own findings.

## Deferred
