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

## 2026-09-26 — Real costs you can see
- Shipped: runs store the estimate shown at launch (low, high, source, samples) and the cap;
  every mandate and cross-engine pipeline gets an outcome (accepted, rejected, pending) through
  `cuanta runs accept|reject`, Result's Accept/Reject, or sandbox Apply/Discard; `cuanta costs`,
  a Home "Real costs" card and a Ledger costs table report spend, medians, cost per accepted
  change and the median estimate error.
- Why: the user must see what an audit, a fix and a feature really cost, and whether the
  estimate and the model mix paid off.
- Tried and rejected: a separate estimate setter on the ledger (the launcher's final full-row
  write would overwrite it); counting only accepted runs, as the bench does (it hides failed
  attempts); showing unknown costs as $0.
- Rule extracted: none.

## 2026-09-26 — Isolated copies (S1)
- Shipped: `cuanta mandate --sandbox`, trials with a git-format patch, apply with a drift check,
  discard, and the git hand-off.
- Why: agents can work on a real project without touching it until the user applies.
- Tried and rejected: a junction for node_modules (Turbopack rejects links out of the root);
  copying `.cuanta/` wholesale (the run belongs in the original ledger).
- Rule extracted: CLAUDE.md §7 sandbox bullet.
