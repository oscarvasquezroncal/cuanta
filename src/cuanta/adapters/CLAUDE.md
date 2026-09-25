# adapters — local rules

## Purpose
The seam onto the outside world: agent CLIs, SQLite ledger, OTLP telemetry, test runners,
graphify, shells and processes. Each subpackage implements a protocol from `cuanta.ports`.

## Local rules
- Implement the port exactly; conformance is checked by `tests/contracts/test_port_contracts.py`
  (and `test_ledger_contract.py` for storage). A new adapter gets a contract-test entry.
- Plugin adapters (engines, test runners, instinct) are registered in `pyproject.toml`
  entry points; the entry-point string must match the class path.
- Engine binaries are overridable by env (`CUANTA_CLAUDE_BIN`, `CUANTA_CODEX_BIN`,
  `CUANTA_OPENCODE_BIN`); tests use these instead of real binaries.
- Unused-argument lint (`ARG002`) is relaxed here because port signatures are fixed.
- Anything spawning processes must work on Windows (process-tree teardown, shell choice).

## Boundaries
- May import `domain`, `ports`, `adapters` only. Never `application`, `cli`, `tui`
  (`tests/architecture/test_layers.py`).

## Local commands
- `uv run pytest tests/adapters tests/contracts -q`
- Real binaries: `uv run pytest -m live` (network, opt-in).

## Local gotchas
No local gotchas recorded yet — the docs-updater appends here as they are found.
