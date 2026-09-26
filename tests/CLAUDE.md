# tests — local rules

## Purpose
Offline, $0 proof of behavior: unit, contract, architecture, adapter, CLI and TUI suites.

## Local rules
- Same style gate as `src`: no comments, no docstrings, `mypy --strict`.
- Every boundary faked (`tests/fakes.py`, `tests/support.py`, `tests/ledger_fixture.py`);
  anything needing real binaries or network is marked `@pytest.mark.live` and is excluded by
  default. Markers are strict (`--strict-markers`) — register new ones in `pyproject.toml`.
- `tests/fixtures/` holds sample repos as data; never add test modules there (not collected,
  not linted, not typechecked).
- Perf assertions use the `perf` marker and read their budgets from env
  (`CUANTA_HELP_BUDGET_S`, `CUANTA_PAINT_BUDGET_S`).
- Tests needing more than 120 seconds declare `@pytest.mark.timeout(300)`; live tests are
  unbounded by `conftest.py`, and `faulthandler_timeout` must stay unset.

## Local commands
- One suite: `uv run pytest tests/<suite> -q 2>&1 | tail -n 40`
- Architecture gates only: `uv run pytest tests/architecture -q`

## Local gotchas
No local gotchas recorded yet — the docs-updater appends here as they are found.
