# Loop specification — cuanta

**Budget:** five parts, no more. Rewritten by `/refresh-agents` only when `VERIFY_TIER` changes.
What does NOT belong here: scheduling this repo does not have, rules (`CLAUDE.md`).

`VERIFY_TIER` = **`strong`** (pytest + mypy strict + ruff, all gated in CI).

> An unattended sequence is sanctioned here — mandate → verify → next mandate from the backlog —
> with a human reviewing every diff before it is committed.

## 1. Trigger

A human pastes `docs/MANDATE_TEMPLATE.md` with a filled REQUEST block. The FINAL REPORT's NEXT
section proposes the following mandate; a human (or an operator who has explicitly opted into
the unattended sequence below) pastes it. CI (`.github/workflows/ci.yml`, on push / pull request)
re-runs the gates after commit — it verifies, it does not start mandates. No scheduler, daemon or
webhook exists here.

## 2. Goal

Done = every requirement in the REQUEST block is met **and** every `CLAUDE.md` §Do not break
contract holds: layer map, no comments/docstrings, TUI i18n, public entry points, coverage
floor, cross-platform, installed-wheel smoke. Nothing else counts as done.

## 3. Verification — in order

1. `uv run ruff check` — proves lint rules hold; nothing about behavior.
2. `uv run ruff format --check` — proves formatting; nothing about behavior.
3. `uv run mypy` — proves types line up under `--strict`; nothing about runtime behavior.
4. `uv run pytest --cov --cov-report=term` — proves the behaviors the suite asserts, including
   the architecture gates and coverage floor; cannot prove `live` paths (real engines, network)
   or platforms not run locally.
5. `uv build && cuanta --plain doctor` — proves the wheel installs and starts; not feature
   behavior. CI's matrix covers the other OSes; a local run proves only the local one.

## 4. Stopping rule

- A mandate ends when self-verification passes all five commands above.
- Tester: max **three** fix attempts, then `persistent_failure`.
- Tester ↔ senior: **one** return trip per phase — not an open loop.
- Halt immediately on (a) a requirement proven impossible, (b) a §Do not break conflict with no
  compliant path.
- Unattended sequence: after a green mandate, take the next `pendiente` entry from
  `HISTORIAS.md`; stop the sequence on any red/`persistent_failure`, any analyst STOP, an empty
  backlog, or the first mandate that changes a §Do not break contract. Every diff is still
  reviewed by a human before commit — the agents never run git.

## 5. Memory

- `HISTORIAS.md` — the backlog; analyst reads open entries, docs-updater writes outcome.
- `docs/IMPROVEMENTS.md` — open gaps; analyst reads unchecked lines, docs-updater appends.
- `docs/CHANGELOG_INTERNAL.md` — narrative; docs-updater writes, never auto-loaded.
- `docs/RUN_LOG.md` — per-mandate trajectory; docs-updater appends, never auto-loaded.
- `docs/GROUND_TRUTH.md` — external facts; analyst checks first, docs-updater appends.

A mandate reads `HISTORIAS.md` and `docs/IMPROVEMENTS.md` before planning.
