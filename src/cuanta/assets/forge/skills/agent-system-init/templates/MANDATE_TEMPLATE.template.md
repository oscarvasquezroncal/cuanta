# Mandate template — {{PROJECT_NAME}}

Copy this whole file, fill the `REQUEST` block at the bottom, paste it as your message. Change
nothing above the REQUEST block.

---

```
# MANDATE — {{PROJECT_NAME}}

## EXECUTION CONTRACT — read first, applies to everything below

- Run this mandate start to finish in ONE continuous pass. NEVER pause to ask for approval,
  confirmation, or "should I continue?". Do not present partial results as checkpoints or
  progress reports awaiting a go-ahead. Handoffs between agents are INTERNAL — they are not
  moments to return to me.
- The ONLY reasons to halt before completion: (a) a requirement here is impossible and you can
  prove it, or (b) implementing it would break a contract in CLAUDE.md §Do not break with no
  compliant path. Halt ONCE, at the moment of discovery, with the proof — never with
  "do you want me to…".
- If your own final verification finds something incomplete, FIX IT and re-verify. Completion
  means verified complete, not "done, please check".
- ABSOLUTELY NO GIT OPERATIONS. No commit, add, stash, branch, checkout, restore, diff, status —
  nothing. I handle all git myself. Your only git-related output is the commit message PROPOSAL
  at the very end (text only, never executed).

## SCOPE GATE — apply before invoking anything

- trivial (typo, one-line fix, obvious rename) → NO agents. Just do it, then report.
- normal (bounded change in one area, small feature, fix with a known cause)
  → architecture-analyst → {{SENIOR_NAME}} → tester.
- complex (crosses subsystems, changes a contract, new module, unclear root cause)
  → the full pipeline including docs-updater.

A ten-line fix must not pay four agent contexts.

## INVESTIGATION — when TYPE is investigation

An investigation changes nothing; it answers questions.

- READ-ONLY: create, edit and delete nothing, and run no command except `graphify` queries.
  Read, Grep and Glob are the tools.
- Skip the SCOPE GATE and the PIPELINE below: invoke the **architecture-analyst** ONCE. No
  {{SENIOR_NAME}}, no tester, no docs-updater — there is nothing to implement, test or document.
- Answer every question listed under WHY / EVIDENCE, inside WHERE when it is given.
- Replace the FINAL REPORT with this report, in this order: **SUMMARY** (two to five sentences)
  · **FINDINGS** (one line each, every finding cites `file:line`) · **RISKS** · **OPEN QUESTIONS**
  · **NEXT STEP** (the single most sensible next request as a filled REQUEST block — propose
  only).
- When `cuanta` is available (`.cuanta/` exists), it stores this report and shows it on its
  Result screen, from where it is saved to `docs/investigations/`. Do not write it to a file
  yourself.

## PIPELINE

Invoke the agents consecutively. Each consumes the previous one's JSON.

1. **architecture-analyst** — **reads the open backlog first**: the `pendiente` / `en progreso`
   entries in `HISTORIAS.md` and the unchecked lines in `docs/IMPROVEMENTS.md`. It states in its
   plan whether this request **continues existing work or opens new ground**, and returns
   `related_stories` and `related_improvements`. A previous mandate already paid to establish
   that context; re-deriving it is the pipeline paying twice.
   Then it produces the plan JSON with a complete `blast_radius`.
   Its STOP statuses (`falsified_premise`, `contract_conflict`, `already_handled`) feed the
   PIPELINE, not me: on a STOP, stop implementing, record it, and carry it to the FINAL REPORT.
   A STOP that prevented a wrong implementation is a successful run.
2. **{{SENIOR_NAME}}** — implements ONE phase per invocation from the plan. Does not re-derive
   `blast_radius`. Returns `blocked` rather than falling back to an unbounded search.
3. **tester** — writes and RUNS the tests, every command truncated (`| tail -n 40`, `-q`).
   Red → ONE return trip to the senior for that phase, then continue. Not an open loop.
   When `cuanta` is available (`.cuanta/` exists), run the suite with `cuanta test --json`
   instead — its output is already bounded, never pipe it — and read failure detail through
   `cuanta cat <capsule> --level L2` rather than re-running tests one by one. While iterating,
   `cuanta test --affected --json` runs only the tests related to the changed files, last
   failures first; the full `cuanta test --json` gives the final verdict.
4. **docs-updater** — ALWAYS runs at the end, on the three JSONs only. Routes to its seven
   destinations — including `docs/IMPROVEMENTS.md`, `docs/RUN_LOG.md` and the `HISTORIAS.md`
   entry — respects the 5-line rulebook budget, and enforces the 300-line ceiling by moving
   content out. **The ceiling never blocks the write**: relocation exhausted → it writes anyway
   with the over-ceiling warning line.

## SELF-VERIFICATION — before the final report

Re-read the REQUEST block and check EVERY requirement, constraint, and flag direction against
what was actually done. Anything failing → fix it and re-verify. Never report a gap as a
question.

## FINAL REPORT — fixed order

1. **WHAT WAS DONE** — per phase: files changed, what changed in each.
2. **RECOMMENDATIONS** — ONLY if something real was found while working, with `file:line`,
   one line each. Found nothing → write exactly "None." and invent no filler. Anything listed
   here must also exist as an `IMP-NNN` line in `docs/IMPROVEMENTS.md` — a recommendation that
   lives only in this report is a finding that evaporates when the chat scrolls.
3. **DOCS UPDATED** — what the docs-updater wrote and where; what it moved out, if anything.
4. **COMMIT PROPOSAL** — conventional-commit form, `type(scope): summary` plus a 2–5 line body.
   If the work separates cleanly, give 2–3 messages in the order I should commit them.
   PROPOSAL ONLY — never executed.
5. **NEXT** — read `HISTORIAS.md` (open entries only) and `docs/IMPROVEMENTS.md`. Propose the
   **single** most sensible next mandate: the story it advances or the gap it closes, and a
   filled REQUEST block ready to paste. If the work just done created a new story or gap, record
   it first. **Propose only — never start it.**

   One mandate ends holding the next one, built on what this run learned rather than on a blank
   page. Starting it without me is not continuity, it is an unattended loop — and whether this
   repo may have one is decided in `docs/LOOP.md`, not here.

=== REQUEST ===

TYPE:            feature | bug | refactor | investigation
WHAT:            <the change, concretely>
WHY / EVIDENCE:  <PASTE the error, the failing output, the log — never describe it>
WHERE:           <file / module / area, if known — "unknown" is a valid answer>
CONSTRAINTS:     <invariants, flag directions, what must stay byte-identical>
EXPECTED TESTS:  <what proof you want; "regression fixture for the pasted error" is a good default>
OUT OF SCOPE:    <what must NOT be touched — always fill this>
```
