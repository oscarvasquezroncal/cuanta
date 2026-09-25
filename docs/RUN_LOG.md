# Run log — cuanta

**Budget:** append-only, one block per completed mandate, written by the docs-updater. **Never
auto-loaded** — read it when a mandate misbehaved and you need the trajectory. What does NOT
belong here: rules, narrative (`docs/CHANGELOG_INTERNAL.md`), open gaps (`docs/IMPROVEMENTS.md`).

**Block format**

```
## <YYYY-MM-DD> — <HU-NNN or "ad hoc"> — <short title>
- Scope:            trivial | normal | complex
- Phases run:       <the agents the scope gate actually invoked>
- Files touched:    <paths>
- Tests:            <status and real numbers>
- Recommendations:  <IMP-NNN ids, or "none">
- HU:               <HU-NNN — estado after this run, or "—">
```

---
