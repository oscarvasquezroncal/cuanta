# Flags and config — cuanta

**Budget:** the full registry. `CLAUDE.md` §Flags mirrors only rows marked `Surprising? = yes`.
What does NOT belong here: rules about how to use a flag (rulebook), history of why it exists
(changelog). Defaults are not copied here — they drift; read them from the source cited.

| Key | Where read | Purpose | Surprising? |
|---|---|---|---|
| `CUANTA_ENGINE` | `src/cuanta/domain/config.py` | default agent engine | no |
| `CUANTA_INSTINCT` | `src/cuanta/domain/config.py` | instinct backend (heuristic / jev / llm) | no |
| `cuanta instinct use NAME --global` | `src/cuanta/cli/commands/instinct.py` | set the user-level instinct backend; the project setting takes precedence | no |
| `CUANTA_EMOJI` | `src/cuanta/domain/config.py` | emoji output toggle | no |
| `CUANTA_THEME` | `src/cuanta/domain/config.py` | UI theme | no |
| `CUANTA_LANG` | `src/cuanta/domain/config.py` | UI language (locales in `tui/locales`) | no |
| `CUANTA_PORT` | `src/cuanta/domain/config.py` | local listener port | no |
| `CUANTA_TEST_COMMAND` | `src/cuanta/domain/config.py` | override detected test command | no |
| `CUANTA_TEST_RUNNER` | `src/cuanta/domain/config.py` | force a test-runner plugin | no |
| `CUANTA_BUDGET_USD` | `src/cuanta/domain/config.py` | spend budget | no |
| `CUANTA_HOME` | `src/cuanta/adapters/system/platform.py` | data directory override | no |
| `CUANTA_CONFIG_DIR` | `src/cuanta/adapters/system/platform.py` | config directory override | no |
| `CUANTA_CLAUDE_BIN` / `CUANTA_CODEX_BIN` / `CUANTA_OPENCODE_BIN` | `src/cuanta/adapters/engines/*.py` | engine binary path override | no |
| `CUANTA_ENGINE_BIN` | `src/cuanta/adapters/engines/base.py` | [UNVERIFIED: generic binary override semantics] | no |
| `CUANTA_SHELL` | `src/cuanta/adapters/system/shell.py` | force shell detection | no |
| `CUANTA_RUN_ID` | `src/cuanta/domain/telemetry.py`, `cli/commands/test.py`, `tui/services.py` | correlate a run's telemetry | no |
| `CUANTA_FORCE_TTY` | `src/cuanta/cli/runtime.py` | force interactive terminal behavior | no |
| `CUANTA_NO_ANIMATION` | `src/cuanta/cli/commands/ui.py` | disable TUI launch animation | no |
| `NO_COLOR` | `src/cuanta/cli/runtime.py`, `cli/app.py` | standard no-color; also suppresses auto-launch of the TUI | [UNVERIFIED] |
| `runs.session` (`.cuanta/config.toml`), `--session` | `src/cuanta/domain/config.py`, `application/engine_run.py` | runs cuanta launches are `lean` (no user plugins, hooks or MCP servers) or `full` | yes |
| `CUANTA_HELP_BUDGET_S` / `CUANTA_PAINT_BUDGET_S` | perf tests; set in `.github/workflows/ci.yml` | loosen perf budgets | yes |
| pytest `addopts -m "not live"` | `pyproject.toml` | live tests excluded by default | yes |
| `[tool.coverage.report] fail_under` | `pyproject.toml` | coverage floor on domain + application | no |
| extra `web` | `pyproject.toml` | installs `textual-serve` for `cuanta ui --web` | no |
