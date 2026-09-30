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
- [x] IMP-002 — a cross-engine pipeline gives each role whatever budget is left, so an expensive first role starves the rest: in T1 the analyst used 40% of a $1.00 budget and the tester stopped at the cap before docs ran — src/cuanta/application/cross_engine.py:183 — found 2026-09-26 in T1 — severity: med
- [x] IMP-003 — plan-based estimates overshoot Forge pipelines about threefold ($1.39 estimated vs $0.43 and $0.50 actual in T1) — src/cuanta/domain/depth.py:100 — found 2026-09-26 in T1 — severity: med
- [x] IMP-004 — Codex on Windows reports that `npm` and `npx` are missing inside its workspace-write sandbox, so Codex roles cannot run the build they are asked to verify (CX-09) — src/cuanta/adapters/engines/codex.py:121 — found 2026-09-26 in T1 — severity: med — resolved 2026-09-27 in X2 without changing the sandbox: cuanta runs the checks between roles (`src/cuanta/application/verification.py`), keeps the tester on Claude and refuses a Codex tester pin on Windows; K1 settled CX-09 as broken in both sandbox modes
- [x] IMP-005 — the Result screen and `cuanta runs show` time a cross-engine run by its first role only (182 s vs 9 min 10 s for the pipeline in T1) — src/cuanta/application/results.py:87 — found 2026-09-26 in T1 — severity: low
- [x] IMP-006 — a Claude run can record more turns than its limit (21 of 20 for a T1 analyst); check whether `num_turns` counts the final result turn — src/cuanta/application/engine_run.py:232 — found 2026-09-26 in T1 — severity: low
- [ ] IMP-007 — the gate's first-paint budget (0.7 s) fails under host load: 0.77 s, 0.74 s and 0.77 s in three V5B/V5D gates while a game and a background indexer ran on the host (whole suite 680–774 s instead of about 500 s), against 0.20–0.25 s for the same test run alone; no code change explains it, and the budget is not moved; P3 later recorded 0.873 s with no competing agent workload, without a contemporaneous host-load measurement. Q1 recorded 0.840 s in the full gate and 1.039 s in a serial standalone diagnostic; help passed that full gate but then took a minimum of 0.382 s against its unchanged 0.20 s budget. A fixed five-pair help comparison, with the same interpreter, environment and working directory and verified isolated import paths, measured baseline `a9e7705` median 0.378 s (minimum 0.365 s) and Q1 median 0.373 s (minimum 0.371 s). Both versions missed the help budget in that comparison; these observations do not identify the cause or establish a Q1 startup regression. No competing agent test workload ran, no unrelated process was stopped, and neither speed budget changed. The Q2 gate recorded 0.931 s in the coverage-traced performance phase after a full 5006-test pass; five alternating standalone pairs then measured baseline `a9e7705` median 0.282 s and the Q2 tree median 0.278 s, so Q1 and Q2 add no measurable first-paint time — tests/tui/test_app.py::test_first_paint_is_fast; tests/cli/test_meow.py::test_help_renders_fast — found 2026-09-29 in V5D — severity: low
- [ ] IMP-008 — on Windows a per-role run that completed can fail to record its trial with `PermissionError` (R3 GPT JSON-LD classic run: all four roles finished, the senior changed `src/app/layout.tsx`, the copy was kept for rescue but the next trial reused the same copy path, so the patch was lost); the error names only the exception type — src/cuanta/application/sandbox.py:537 — found 2026-09-29 in R3 — severity: med

- [ ] IMP-009 — a fresh uv Python 3.12.13 wheel-smoke launcher injects `PYTHONHOME` into children, so this host's graphify 0.9.55 on Python 3.13.13 imports the wrong standard library and fails with `SRE module mismatch`; installed meow passes on both interpreters and doctor passes on 3.13, while 3.12 doctor remains blocked; no global environment was changed — src/cuanta/adapters/system/process_runner.py:172 — found 2026-09-30 in P3 — severity: med

- [ ] IMP-010 — the same-session verifier compares failures by file and message, so some swaps with an unchanged count stay invisible: the same message in the same file at another line, Mocha dot/min reporters that print only the suite, and Jest output behind a turbo or pnpm prefix; multi-project Jest runs that reuse a path without displayName can still merge a reprinted title — src/cuanta/application/verification.py:131 — found 2026-09-30 in Q2 — severity: low
- [ ] IMP-011 — the code-index budget test (first update of 300 files under 10 s) is load-sensitive: it took 21.2 s in one coverage-traced four-worker gate pass and 16.7 s in a focus run, and passed in the other pass; standalone, three alternating pairs measured the first update at 2.86–3.25 s on `a9e7705` and 2.98–3.89 s on the Q2 tree, so the change adds no measurable time and the budget is not moved — tests/unit/test_code_index.py::test_three_changes_in_three_hundred_files_fit_index_budgets — found 2026-09-30 in Q2 — severity: low

## Deferred
