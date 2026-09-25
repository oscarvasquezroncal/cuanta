# Internal changelog — cuanta

**Budget:** unbounded, never auto-loaded. Holds the NARRATIVE — what shipped, why, what was tried
and rejected. What does NOT belong here: rules (`CLAUDE.md`), external facts
(`docs/GROUND_TRUTH.md`), open gaps (`docs/IMPROVEMENTS.md`), per-run telemetry
(`docs/RUN_LOG.md`).

**Entry format** (newest first):

```
## <YYYY-MM-DD> — <title>
- Shipped: <what>
- Why: <reason>
- Tried and rejected: <what, and why not>
- Rule extracted: <the one-line rule that went to CLAUDE.md, or "none">
```

---

Starts empty — no prior docs existed to relocate narrative from. Fills on the first pipeline run.
