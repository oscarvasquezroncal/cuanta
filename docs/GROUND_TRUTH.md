# Ground truth — cuanta

**Purpose:** every reading of an external source tree (Textual internals, agent CLI output
formats, OTLP, vendor SDKs, specs) is paid once and cited forever. The architecture-analyst
checks this file BEFORE deriving anything.

**Budget:** append-only. An entry is never rewritten — it is superseded by a new dated line and
the old line stays. What does NOT belong here: repo structure (graph), rules (`CLAUDE.md`),
stories of past work (`docs/CHANGELOG_INTERNAL.md`).

**Entry format**

```
<claim> — <source path>:<line> — <YYYY-MM-DD> [superseded: <reason>]
```

Grouped by subject (`## Textual`, `## Claude Code stream-json`, `## OTLP`, …).

---

Empty — nothing external was harvested at init (no prior docs existed). Fills on first use.

## Git workflow — 2026-09-25

- Claude Code supports `attribution.commit` and `attribution.pr` as strings; empty values
  suppress those bylines. `includeCoAuthoredBy` is deprecated since v2.0.62.
  `attribution.sessionUrl: false` separately suppresses the Claude session trailer/link.
  Verified against the official Markdown reference fetched on 2026-09-25:
  https://code.claude.com/docs/en/settings-reference.md#attribution . No live Claude commit
  was requested; runtime compliance remains unverified and is independently checked by Git.
- `commit-msg` receives the message file path and may edit it in place. Exit status zero
  allows the commit; `--no-verify` bypasses this hook. Hooks are selected by `core.hooksPath`
  and must be executable on POSIX. Official contract:
  https://git-scm.com/docs/githooks#_commit_msg . Verified locally through Git for Windows
  2.41.0 with the actual sh/awk hook, including CRLF, human coauthors and mixed-case markers.
- pytest-xdist 3.8.0 honors `xdist_group` with `--dist=loadgroup`; `-n auto` enables workers
  and `--maxprocesses` limits their count. Official contract:
  https://pytest-xdist.readthedocs.io/en/stable/distribution.html . The repository defaults
  to loadgroup; fixed-port listener tests share one group. Git tests use isolated local
  repositories and bare remotes and may run concurrently.

## Claude Code CLI and cache — 2026-09-25

- The CLI reference documents `--max-turns` as print-mode only and says reaching it ends with
  an error. Installed Claude Code 2.1.283 help omits the flag. A 2.1.282 live probe with a limit
  of one returned `error_max_turns`, `terminal_reason=max_turns` and exit code 1. Source:
  https://code.claude.com/docs/en/cli-reference — 2026-09-25.
- The CLI reference says `--agents` validates JSON at startup from 2.1.242, and
  `--max-budget-usd` includes subagent spend with cap enforcement from 2.1.217. A 2.1.282 live
  probe accepted an agents file and attributed the main and subagent models separately. Source:
  https://code.claude.com/docs/en/cli-reference — 2026-09-25.
- `--tools` limits built-in tool definitions and does not restrict MCP tools. On Claude Code
  2.1.282, the same Haiku investigation's first request was 30,588 tokens with default tools
  and 15,454 with Read, Grep, Glob and Bash; the four-tool init list matched the request.
  Source: https://code.claude.com/docs/en/cli-reference and M0 live probe, summarized in
  `docs/CONTRACTS.md` — 2026-09-25.
- `--exclude-dynamic-system-prompt-sections` moves per-machine sections to the first user
  message. In a 2.1.282 subscription probe, a 7,116-token prefix was read after 60 s and
  360 s idle gaps, then zero tokens were read after a 3,660 s gap. The seed and cold run each
  wrote 8,456 tokens in the one-hour bucket. The exact expiry instant and API-key behavior
  remain unmeasured. Source: https://code.claude.com/docs/en/cli-reference and M0 live probe,
  summarized in `docs/CONTRACTS.md` — 2026-09-25.
- `--add-dir` grants file access to secondary directories, but Claude Code does not discover
  most `.claude/` configuration from them. No cuanta permission probe has been run. Source:
  https://code.claude.com/docs/en/cli-reference — 2026-09-25.

## Claude Code hooks and MCP — 2026-09-25

- `PreToolUse` can return `hookSpecificOutput.permissionDecision` to deny a call and
  `updatedInput` to replace its arguments; `PostToolUse` can return `additionalContext` for
  the next model request. Cuanta has not probed those hook contracts on the installed client.
  Source: https://code.claude.com/docs/en/hooks — 2026-09-25.
- `disableAllHooks` is evaluated after settings precedence; managed hooks require managed-level
  control. Cuanta's lean settings set it to true, so a future cuanta hook needs a separate
  configuration design. Source: https://code.claude.com/docs/en/hooks and
  `src/cuanta/domain/plugins.py` — 2026-09-25.
- In the MCP 2025-11-25 lifecycle, the client proposes `protocolVersion` in `initialize` and
  the server responds with a version. Cuanta has no MCP server or captured negotiation with
  the installed client. Source:
  https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle — 2026-09-25.

## Test timeout tooling — 2026-09-25

- pytest-timeout 2.4.0 accepts a config `timeout`, `PYTEST_TIMEOUT`, `--timeout`, and per-test
  marks in increasing priority; zero disables a timeout. The repository keeps live tests
  unbounded unless explicitly marked. Source: https://pypi.org/project/pytest-timeout/ and
  `pyproject.toml`, `tests/conftest.py` — 2026-09-25.
