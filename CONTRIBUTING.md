# Contributing to cuanta

## Git workflow

Work only on `main`. At session start, check `git status`; if a remote exists, run
`git fetch` and `git pull --ff-only`. Stop if history has diverged. Never force,
rebase, stash, clean or rewrite published commits.

Run this once from the repository in cmd, after configuring your Git identity:

```cmd
scripts\git\setup.cmd
```

Setup checks `user.name` and `user.email`, then sets this repository's
`core.hooksPath` to `.githooks`. On POSIX, make `.githooks/commit-msg` executable
and set the same local hooks path. The POSIX hook removes AI attribution and
session links, preserves human coauthors, normalizes CRLF and trims trailing
blank lines. It always exits successfully; the commit script provides the
post-commit check.

Finish each milestone with separate conventional commits by type:

```cmd
scripts\git\commit.cmd -m "feat(git): add guarded workflow" scripts/git .githooks .gitattributes
scripts\git\commit.cmd -m "test(git): cover workflow guards" tests pyproject.toml uv.lock .github/workflows/ci.yml
scripts\git\commit.cmd -m "docs(git): explain repository workflow" CONTRIBUTING.md docs
```

Quote paths containing spaces. Omitting paths stages all changes; existing
staged changes are also included. Run from the repository root. The stdlib
scripts use `uv run --no-sync python`, so run `uv sync --extra web` first.
`commit.cmd` refuses off `main` or without the configured hook. It checks the
new commit and, if needed, amends only that just-created local commit's message.
It never rewrites older commits. ZIP archives are ignored and the script refuses them
even if they were force-staged. Local settings and private plans must stay untracked.

Only the user publishes:

```cmd
scripts\git\push.cmd
```

The push script fetches `origin`, fast-forwards when behind, stops on divergence,
and checks every outgoing commit for AI markers and the configured author and
committer identity. It never repairs history. With a new remote, the entire
history is outgoing and must pass those checks. Without `origin`, it prints the
command needed to add one. Tests exercise publishing only against temporary
local bare repositories.

This repository's ignored `.claude/settings.local.json` uses
`"attribution": {"commit": "", "pr": "", "sessionUrl": false}`. Configure that
locally on each checkout; it is a first defense alongside the hook and scripts.
Cuanta's application never executes Git in the user's projects.

## Verification and handoff

Use focused tests during development. At milestone close:

```cmd
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict
uv run pytest -n auto --cov --cov-report=term
```

`loadgroup` scheduling keeps tests sharing `xdist_group` serial within their
group; fixed-port listener tests share one group. Use `--maxprocesses=4` when
local resources require a worker limit. Run the full suite once, or twice
consecutively when changing TUI lifecycle, process management or timing-sensitive
code, and at V2 M9. Do not lower the coverage floor. No comments or docstrings;
keep strict types, hexagonal layers and both es/en catalogs.
Tests have a 120-second timeout; tests whose own bounds require more time use
`@pytest.mark.timeout(300)`, while opted-in live tests have no timeout.

Record changes, gate results, verified and unverified external contracts, risks
and the next milestone in a local plan under the ignored `.cuanta/` directory.
Keep private session prompts, local paths and client details out of public documentation.
G0 commits wait for the user to run setup and confirm; later milestones use the active
hook. Stop after each milestone.
