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

## 2026-09-27 — Anchored index knowledge
- Shipped: section-scoped root and nested rulebooks, explicit documentation references,
  importing test links with verification commands, and deduplicated ledger history with
  outcome, retry and failure evidence. Reports and agent notes preserve original anchors.
- Why: a previous finding must not silently become a current fact after source changes.
- Tried and rejected: anchoring an old report to current content without historical proof;
  such records remain stale. Exact unchanged ranges can be revalidated after unrelated edits.
- Rule extracted: none.

## 2026-09-27 — Structural index sources
- Shipped: deterministic Python and Tree-sitter syntax extraction for TS, JS, TSX and Go,
  preserving symbol identities, directed imports/exports, routes, hooks, stores, services and
  environment reads. The richer graph import checks source freshness and keeps provenance.
- Why: the legacy file-neighbour graph loses edge direction and duplicate symbol identity.
- Tried and rejected: regex-only parsing and trusting cached graphs by Git revision. Unknown
  syntax and unresolved dynamic imports remain explicit coverage limitations.
- Rule extracted: none.

## 2026-09-27 — Durable incremental index inventory
- Shipped: pure index records and a port, copy-local SQLite storage with independent schema,
  hash provenance, recovery and safe source/documentation/style inventory. `cuanta index`
  updates only changed records; mandates refresh the inventory before launch.
- Why: source-only scanning misses non-source inputs and cannot safely enumerate index inputs.
- Tried and rejected: sharing the original project's index with an isolated copy, because
  different trees would overwrite each other's hashes. The ledger remains shared separately.
- Rule extracted: none.

## 2026-09-27 — The product today: six measured baseline trials
- Shipped: six sandbox audit/fix/feature trials, exact recovered requests and a frozen replay
  matrix. Public report keeps actual models, costs, cache, attempt/launch times and outcomes.
  Native trial role references now remove matching engine prefixes before routing, with
  command-through-routing regressions for Sonnet and Opus and rejection of engine mismatches.
- Why: the requested all-Sonnet pipeline silently used default Opus roles because qualified
  resolved IDs did not match the native catalog. The already-started series remains measured;
  its model contrasts are marked blocked, rather than relabelled as pure Sonnet.
- Tried and rejected: two GSAP patches passed external commands but failed unchanged source
  checks and lacked full runtime failure proof. No check was weakened and no patch applied.
  The six trials cost $5.08215255 against $8.00; four reports/patches accepted, two rejected.
- Rule extracted: none. Corrected V4 launches must label policy differences from misrouted E1.

## 2026-09-27 — Cross-engine budget reservations and measured estimates
- Shipped: proportional shares with a five-percent floor per active role, future-role reserves,
  unused-share carry, complete pipeline timing and calibrated shape/depth history estimates.
  Existing run metadata retains shape and calibration without a ledger migration. Team states
  Windows Codex build limitations and routes a cross-engine tester to Claude.
- Why: T1 exhausted the pipeline before later roles ran and understated total wall time.
  The raw Claude count still includes its terminal limit result; the UI explains it.
- Tried and rejected: inaccessible engine-created probe output is kept for recovery; no ACL
  change or broader sandbox grants are used. A user-owned pre-existing output file made the
  artifact readable. The Codex probe exceeded its remaining cap and no further probe ran.
- Rule extracted: none. Sandbox collection errors preserve costs and block application.

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
