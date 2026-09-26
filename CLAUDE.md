# CLAUDE.md — cuanta

Rulebook: judgment only. Structure lives in the graph (`graphify-out/`); external derivations in
`docs/GROUND_TRUTH.md`; external contract status in `docs/CONTRACTS.md`; narrative in
`docs/CHANGELOG_INTERNAL.md`; full flag registry in `docs/FLAGS.md`.

**Budget rules for this file (survive the skill that wrote them):**
1. 300-line ceiling. Over it, move content out (structure → delete, narrative → changelog, flags →
   `docs/FLAGS.md`, external facts → `docs/GROUND_TRUTH.md`, contract status →
   `docs/CONTRACTS.md`, one-directory rules → that directory's `CLAUDE.md`); still over → write
   anyway with a ⚠ line under the title.
2. Zero drifting numbers. Write the command that asks the code, never the count.
3. Per-directory `CLAUDE.md` files hold local rules only — never inventories.

## 1. Project

cuanta — token observability and Forge bootstrap for AI coding agents (Claude Code, Codex,
OpenCode). CLI + Textual TUI. Python ≥3.12, managed with `uv`, built with hatchling.
Entry point: `cuanta = "cuanta.cli.app:main"` (`src/cuanta/cli/app.py`); composition root is
`src/cuanta/bootstrap.py`.

## 2. Navigation

A code graph is wired (graphify, CLI mode). **The graph owns structure** — who calls what,
imports, blast radius. Do not read source to confirm an edge the graph asserts; read only to
modify code, to resolve a runtime branch, or when the graph is silent.

- `graphify query "<question>"` — orient in an unfamiliar area
- `graphify explain "<symbol>"` — role and neighbours of one symbol
- `graphify path "<A>" "<B>"` — how two symbols connect
- `graphify affected "<symbol>" --depth 2` — blast radius before a change

**Re-index after any change that adds, removes, moves, or renames a symbol or file:**
`graphify update .` (no LLM, no API key). A stale graph is worse than none.

## 3. Architecture map

Hexagonal layering under `src/cuanta/`: `domain` (pure model) ← `ports` (protocols) ←
`application` (use cases) / `adapters` (I/O implementations); `cli` and `tui` are the outer
shells; `bootstrap.py` wires adapters into application. Plugins (engines, test runners,
instinct backends) are discovered through `[project.entry-points."cuanta.*"]` in
`pyproject.toml`. Everything else: ask the graph.

## 4. Conventions

- **No comments and no docstrings** anywhere in `src/cuanta` or `tests` (assets and fixtures
  excluded). Enforced by `tests/architecture/test_style.py`. Names carry the meaning.
- **Layer dependency rule** — allowed imports per layer are the `ALLOWED` map in
  `tests/architecture/test_layers.py`. That map is the source of truth; read it, don't restate it.
  Highlights: `domain` imports only `domain`; `adapters` never import `application`, `cli`, `tui`;
  `application` never imports `adapters`.
- `mypy --strict` over `src` and `tests`, `warn_unreachable`. Type everything; no untyped defs.
- Ruff: line length 100, rule set in `pyproject.toml` `[tool.ruff.lint]`; format with
  `ruff format`.
- `from __future__ import annotations` at the top of modules (observed in sampled files)
  [UNVERIFIED as universal].
- New engine / test runner / instinct backend → implement the port, register it under the
  matching `cuanta.*` entry-point group. Never hard-wire it in `bootstrap.py` alone
  [UNVERIFIED: whether bootstrap also lists them].
- TUI user-visible text comes from the i18n catalog (see `src/cuanta/tui/CLAUDE.md`).
- Before relying on an engine flag, settings key, hook, telemetry field, price row or protocol
  version, check `docs/CONTRACTS.md`. Update its status, version, date and evidence when a change
  verifies, probes or starts relying on the contract. Keep help-omitted flags out of
  `REQUIRED_FLAGS`, because the capability check reads the installed help text.

## 5. Commands

```bash
uv sync --extra web                 # install (dev group is default)
uv run ruff check                   # lint
uv run ruff format --check          # format gate
uv run mypy                         # strict typecheck (files from pyproject)
uv run pytest -n auto -m "not live and not perf" --cov --cov-report=term
uv run python scripts/tests/performance.py
uv run pytest -m live               # opt-in: real agent binaries + network
uv build && cuanta --plain doctor   # install smoke (CI install-smoke job)
```

Counts are never written here: `uv run pytest --collect-only -q | tail -1`.

The full test gate is both pytest phases, in order, in the same workspace. The parallel phase
starts fresh coverage; the performance helper discovers every non-live performance case,
then executes those exact node IDs serially in a fresh process and appends coverage. A
selection mismatch or empty discovery fails. Both phases enforce the configured coverage
floor. Preserve performance assertions, measured startup work and budgets.

## 6. Do not break

- Repository workflow: work only on `main`; commit only through `scripts/git/commit.cmd`
  after the gates; only the user runs `scripts/git/push.cmd`. No AI attribution, force,
  rebase, stash, clean or rewriting published commits. At session start, fetch and
  pull with `--ff-only` if a remote exists; stop on divergence. See `CONTRIBUTING.md`.
- The private session plan, when present, lives in `.cuanta/NEXT_SESSIONS.md` and stays
  untracked. Follow its milestone order. Full gate (both pytest phases) once at close,
  twice consecutively for TUI lifecycle, process management, timing changes and V2 M9.

- The layer `ALLOWED` map and the no-I/O-in-domain check (`tests/architecture/test_layers.py`).
- No comments / no docstrings (`tests/architecture/test_style.py`).
- TUI widgets take text from the catalog (`tests/architecture/test_tui_strings.py`).
- Entry-point group names `cuanta.engines`, `cuanta.test_runners`, `cuanta.instinct` and the
  `cuanta` console script — external plugins and installed users depend on them.
- Coverage floor on `domain` + `application` (`[tool.coverage.report]` in `pyproject.toml`);
  never lower it to get green.
- CI runs ubuntu, macOS **and Windows** on Python 3.12 and 3.13 (`.github/workflows/ci.yml`).
  Paths, shells, subprocess teardown and console encoding must work on all three.
- `cuanta --plain meow` and `cuanta --plain doctor` must run from an installed wheel.

## 7. Flags (surprising defaults only — full registry in `docs/FLAGS.md`)

- pytest `addopts` excludes `-m live` by default: a green local run never touched real engines.
- `CUANTA_HELP_BUDGET_S` / `CUANTA_PAINT_BUDGET_S` loosen perf budgets in CI; locally the
  stricter defaults apply.
- `runs.session` defaults to lean for Claude launches; full restores user plugins, hooks and MCP.
- `CUANTA_MAX_TURNS`, `runs.max_turns` and `cuanta mandate --max-turns` set the Claude turn rail;
  zero uses the depth limit, and `--no-cap` does not remove that rail.
- `cache.ttl_s` is a measured or conservative cache window tied to auth mode, engine version and
  date. Without a saved measurement, show unknown. `cuanta probe cache-ttl` previews without
  spending until `--yes` is supplied.
- pytest bounds ordinary tests per test, leaves live tests unbounded unless marked, and does not
  restart a crashed xdist worker. See `pyproject.toml` and `tests/conftest.py`.
- `NO_COLOR` disables automatic TUI launch when `cuanta` has no arguments.

## 8. Repo traps

- Snapshot tests (`pytest-textual-snapshot`) fail on any visual change; regenerate with
  `uv run pytest tests/tui --snapshot-update` only when the change is intended, and keep
  `docs/screenshots` in sync (a test checks it) [UNVERIFIED: exact sync test path].
- `tests/fixtures/` is excluded from ruff, mypy and pytest collection — fixture repos there are
  data, not tests.
- Windows is a first-class target: subprocess tree teardown, shell detection (`CUANTA_SHELL`
  override) and Rich markup escaping have bitten before [UNVERIFIED: from session history].

- `--yes` is hoisted to a global flag (`cli/group.py` `GLOBAL_FLAGS`): a command's own `--yes`
  option never receives it, so read `session.options.yes` too.
- `cuanta bench` copies each run to the system temp dir, outside this repo, so the cuanta
  `CLAUDE.md` and pytest config never leak into a run. Real benches spend money: run them only
  with an approved budget. Public-repo tasks download pinned tarballs checked by sha256 and are
  covered only by `pytest -m live`.
- `settle()` in `tests/tui/test_app.py` tolerates `WorkerCancelled`: exclusive workers cancel
  their predecessors, and `workers.wait_for_complete()` would otherwise raise.

## 9. Proven NOT bugs

Things that look like defects and are not. Filled only by pipeline runs (tester
`proven_not_bug`). Empty so far.

## 10. Deploy state

Release metadata lives in `pyproject.toml`; `uv build` produces the wheel and source archive.
The PyPI release workflow and user-only tag command are documented in `CONTRIBUTING.md`.
Treat CLI surface and entry points as public. A built artifact does not prove PyPI publication.
