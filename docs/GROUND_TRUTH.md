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
