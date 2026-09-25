# Loop specification — {{PROJECT_NAME}}

**Purpose:** the layer above the harness. The harness is *how one mandate runs* — agents, tools,
verification, guardrails, observability. This file is *how one mandate hands off to the next*:
trigger, goal, verification, stopping rule, memory. Five parts, no more.

**This repo's verification tier: `{{VERIFY_TIER}}`.** That value decides what §4 is allowed to
sanction. In any loop the verifier is the bottleneck, not the model — a loop running unattended
is a loop making mistakes unattended.

> {{LOOP_AUTONOMY_VERDICT}}

## Budget and boundaries

- **Five parts only.** A sixth section is a sign something belongs in `AGENTS_GUIDE.md` (how to
  drive it) or `CLAUDE.md` (a rule).
- **Not auto-loaded.** Read when setting up a mandate or when changing how the loop runs.
- **Describes; does not automate.** There is no scheduler, no daemon, and no webhook here. A
  trigger this repo does not genuinely support does not get written down.

---

## 1. Trigger — what starts a mandate

{{LOOP_TRIGGER}}

## 2. Goal — how "done" is expressed here

{{LOOP_GOAL}}

## 3. Verification — what constitutes proof, in order

Each command below is verbatim from `CLAUDE.md` §Commands, with **what it can and cannot prove**.
A command whose limits are not stated gets read as proof of everything.

{{LOOP_VERIFICATION}}

## 4. Stopping rule — every loop must be able to stop without a human

{{LOOP_STOPPING_RULE}}

## 5. Memory — what carries between mandates

A mandate **reads these before planning**. That is the whole point: one mandate ends holding the
next one, built on what the last run learned rather than on a blank page.

{{LOOP_MEMORY}}
