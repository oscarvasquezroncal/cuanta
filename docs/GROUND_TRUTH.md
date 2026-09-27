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

## Isolated copies: Next.js, NTFS links and git — 2026-09-26

- Next.js 16.2.11 takes as its root the directory of the outermost lockfile found while walking
  up from the project, unless `turbopack.root` or `outputFileTracingRoot` is set —
  `next/dist/lib/find-root.js` (`findRootDirAndLockFiles`) in the installed package — 2026-09-26.
- Turbopack in Next.js 16.2.11 refuses a `node_modules` junction that resolves outside its root:
  `Symlink [project]/node_modules is invalid, it points out of the filesystem root`. A copy whose
  `node_modules` is a real folder of hard links builds (`npm run build` exit 0) and leaves the
  original `node_modules` unchanged — local probe on a production Next.js landing page,
  Windows 11 — 2026-09-26.
- NTFS hard links share data and attributes, but a directory entry's size and attributes are
  updated only at the link used for the change; to restore read-only after deleting a link, set
  it from a remaining link — https://learn.microsoft.com/en-us/windows/win32/fileio/hard-links-and-junctions
  — 2026-09-26. `os.scandir` stat data comes from directory entries, so the node_modules check
  uses `os.stat` per file — `tests/adapters/test_sandbox_copy.py`
  (`test_in_place_write_through_a_hard_link_is_detected`) — 2026-09-26.
- `_winapi.CreateJunction(target, link)` accepts an extended-length link path and fails with
  `FileNotFoundError` when the target does not exist yet, so links found in a project are
  recreated after the copy walk — `src/cuanta/adapters/system/sandbox.py` — 2026-09-26.
- `git add` and `git commit` accept `--pathspec-from-file` with `--pathspec-file-nul` (git 2.25
  or later), and `--literal-pathspecs` disables pathspec magic —
  https://git-scm.com/docs/git-add, https://git-scm.com/docs/git — 2026-09-26.
- `git apply` needs an `index <old>..<new>` line with full 40-character blob ids to apply a
  `GIT binary patch` hunk; the hunk is a zlib stream in git's base85 lines, and Python's
  `base64.b85encode` uses the same alphabet — `tests/unit/test_patch.py`
  (`test_patch_round_trips_through_git_apply`) — 2026-09-26.
- The git index (`.git/index`, versions 2 to 4) lists each staged path with its blob id; a file
  whose content hashes to a different blob has changes that are not staged —
  https://git-scm.com/docs/index-format — 2026-09-26.
- Each index entry also caches the working file's size and mtime; with `core.autocrlf=true`
  (the Git for Windows default) the working tree holds CRLF while the index blob has LF, so a
  clean file only matches its blob after CRLF is turned into LF — `tests/unit/test_sandbox_runs.py`
  (`test_handoff_flags_untracked_edits_but_not_crlf_checkouts`) — 2026-09-26.
- `GIT_CEILING_DIRECTORIES` stops repository discovery from walking above the listed folders —
  https://git-scm.com/docs/git#Documentation/git.txt-GITCEILINGDIRECTORIES — 2026-09-26.
- Git reads its user settings from `$XDG_CONFIG_HOME/git/config` and then `~/.gitconfig`, then
  the repository's `.git/config`; `core.excludesFile` defaults to `$XDG_CONFIG_HOME/git/ignore`
  (`~/.config/git/ignore`), and `core.ignorecase` makes ignore patterns match without case —
  https://git-scm.com/docs/git-config#FILES, https://git-scm.com/docs/gitignore — 2026-09-26.
- Git patches record a mode-only change as `old mode`/`new mode` lines with no hunk, and a
  deleted file keeps its own mode in `deleted file mode`; `git apply` sets the executable bit on
  POSIX — `tests/unit/test_patch.py` (`test_mode_patches_apply_with_git`, run under WSL Linux) —
  2026-09-26.
- On Windows, `os.replace` onto a read-only file and `os.unlink` of a read-only file raise
  `PermissionError` until the read-only attribute is cleared —
  `tests/adapters/test_workspace_bytes.py` — 2026-09-26.
- `socket.bind` on Windows fails with WinError 10048 when another socket already holds the
  port, so probing a port and binding it later races with parallel processes; the listener now
  binds and moves to the next port on failure — `tests/adapters/test_listener.py`
  (`test_scoped_listener_moves_on_when_a_free_looking_port_is_taken`) — 2026-09-26.

## Live trials (T1) — 2026-09-26

- Claude Code 2.1.283 in print mode sends a `generate_session_title` Haiku request before the
  agent's first request; it is not the context the agent works with (CC-21).
- Two runs with the same launch profile, back to back in the same isolated-copy path, shared
  part of their first-request prefix: 19,827 of 28,817 tokens (T1a) and 37,064 of 56,524
  (T1c) were read from cache. Cache state comes from each run's first agent request — ledger
  events in the project's `.cuanta/ledger.db`.
- `--depth quick` caps every routed role at the standard tier, so a role planned on Opus runs on
  Sonnet — T1d route and launch output.

## Test timeout tooling — 2026-09-25

- pytest-timeout 2.4.0 accepts a config `timeout`, `PYTEST_TIMEOUT`, `--timeout`, and per-test
  marks in increasing priority; zero disables a timeout. The repository keeps live tests
  unbounded unless explicitly marked. Source: https://pypi.org/project/pytest-timeout/ and
  `pyproject.toml`, `tests/conftest.py` — 2026-09-25.
