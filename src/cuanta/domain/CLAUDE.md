# domain — local rules

## Purpose
Pure model of cuanta: ledger, pricing, detection, forge state, config, mandates — no I/O.

## Local rules
- Never import `subprocess`, `sqlite3`, `socket`, `http`, `httpx` or `shutil` here — enforced by
  `test_domain_has_no_io_modules` in `tests/architecture/test_layers.py`. I/O belongs in an
  adapter behind a port.
- Keep functions deterministic: time, environment and filesystem arrive as arguments (e.g.
  `layer_from_env(os.environ)` is called from `bootstrap.py`, not read here) [UNVERIFIED as
  universal — `domain/config.py` names env keys but should not read `os.environ`].

## Boundaries
- Imports only `cuanta.domain`. Every other layer may import it.

## Local commands
- `uv run pytest tests/unit -q` [VERIFY: unit tests for domain live under tests/unit]

## Local gotchas
No local gotchas recorded yet — the docs-updater appends here as they are found.
