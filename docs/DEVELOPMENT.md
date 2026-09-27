# Development (maintainer notes)

cuanta is maintained by its author and does not accept issues or pull requests. These notes
record the maintainer's Git workflow, verification gates and release steps.

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

Finish each milestone with separate
[Conventional Commits](https://www.conventionalcommits.org) by type:

```cmd
scripts\git\commit.cmd -m "feat(git): add guarded workflow" scripts/git .githooks .gitattributes
scripts\git\commit.cmd -m "test(git): cover workflow guards" tests pyproject.toml uv.lock .github/workflows/ci.yml
scripts\git\commit.cmd -m "docs(git): explain repository workflow" README.md docs
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

## Releases

Only the user publishes release tags. Prepare the release on `main`: synchronize the version
in `pyproject.toml`, `src/cuanta/__init__.py` and `uv.lock`, and add the matching dated entry
to `CHANGELOG.md`. Run the required gates, commit through `scripts/git/commit.cmd`, publish
main through `scripts/git/push.cmd`, and wait for every CI job on that exact commit to pass.

For the first PyPI release, the repository owner must create a pending Trusted Publisher in
their PyPI account with these values:

| Field | Value |
| --- | --- |
| PyPI project | `cuanta` |
| GitHub owner | `oscarvasquezroncal` |
| GitHub repository | `cuanta` |
| Workflow filename | `release.yml` |
| Environment | `pypi` |

Create the matching `pypi` environment in the GitHub repository settings. The release uses
Trusted Publishing; no PyPI API token is stored in the repository or GitHub secrets. A pending
publisher does not reserve the package name before the first successful publication.

After setup and exact-commit CI are complete, the user runs:

```cmd
scripts\git\push.cmd --tag
```

The tag path requires a clean, attached `main` whose HEAD matches remote main, a valid release
version with a dated changelog entry, successful current-attempt CI for that exact SHA, and
no existing local or remote version tag. It creates an annotated `vX.Y.Z` tag at the checked
commit and pushes only that tag. It does not fast-forward the checkout or push main as part
of tagging. Unknown arguments are rejected.

If the tag push fails after local creation, the script retains the local tag and reports its
name and checked SHA. Inspect local and remote state before any recovery; a subsequent
`--tag` invocation refuses the existing tag. Never force, move or recreate a published tag.

The `release.yml` workflow runs the reusable CI gates and installed-wheel smoke checks,
builds the distributions, publishes from the `pypi` environment, and installs that exact
published version on Windows, Linux and macOS. Each published-package smoke job runs
`cuanta --plain meow` and `cuanta --plain doctor` without a local wheel fallback. Only the
publish job receives `id-token: write`.

Watch the release with `gh run list --workflow release.yml`, then `gh run watch <run-id>`.
A release is complete only when publication and all three published-package smoke jobs pass.
If a post-publication check fails, diagnose it without rewriting the tag or republishing the
same version.

## House rules

Keep these rules; tests and the gates below check most of them:

- The hexagonal layers.
- `mypy --strict`.
- No comments or docstrings in `src/` and `tests/`: names and tests carry the meaning.
- Every visible string in both the English and Spanish catalogs.
- Snapshot tests for every screen.

## Verification and handoff

Use focused tests during development. At milestone close, from the repository root:

```cmd
uv sync --extra web
scripts\dev\gate.cmd
```

The full test gate requires both pytest phases, in that order, from the same workspace.
The parallel phase starts fresh coverage data. The performance helper discovers every
non-live performance case, then executes those exact node IDs serially in a fresh process
and appends coverage. Empty discovery or a selection mismatch fails the gate. Each execution
phase enforces the configured coverage floor. This keeps unrelated collected test modules
out of performance measurements. Keep the assertions and budgets unchanged.

`loadgroup` scheduling keeps tests sharing `xdist_group` serial within their
group; fixed-port listener tests share one group. The local gate uses `-n auto` with at most
four workers. While working, run `scripts\dev\focus.cmd`. At a milestone close, run the full
gate once; use `scripts\dev\gate.cmd --twice` only when
the milestone changes process management, and at V2 M9. Do not lower the coverage floor or move
speed budgets.
Tests have a 120-second timeout; tests whose own bounds require more time use
`@pytest.mark.timeout(300)`, while opted-in live tests have no timeout.

Record changes, gate results, verified and unverified external contracts, risks
and the next milestone in a local plan under the ignored `.cuanta/` directory.
Keep private session prompts, local paths and client details out of public documentation.
G0 commits wait for the user to run setup and confirm; later milestones use the active
hook. Stop after each milestone.

## Development scripts

Read the short summary first. Full command output and `summary.json` live under
`.cuanta/gates/<timestamp>/`; open a log only at the failing test named by the summary.
The gate prints step durations, pytest counts, coverage and a bounded list of failures.
Exit codes are 0 for success, 1 for a functional, report or coverage failure, and 2 when
only performance speed assertions exceed their budgets. The local gate enforces the
unchanged coverage floor in both phases. CI runs performance separately with
`continue-on-error: true`; it has independent coverage and does not block install-smoke
or run on release tags. Release validation still requires every gate and install-smoke job.

```cmd
scripts\dev\gate.cmd --static
scripts\dev\gate.cmd --no-perf
scripts\dev\focus.cmd
scripts\dev\focus.cmd src/cuanta/domain/example.py tests/unit/test_example.py
scripts\dev\trial.cmd .cuanta\specs\t1.toml --dry-run
scripts\dev\trial.cmd .cuanta\specs\t1.toml --only T1b-1
```

Focus reads staged, unstaged and untracked paths from Git status, checks the Python files
with ruff and format, then runs incremental mypy, architecture tests and test modules that
import changed modules, including changed tests. It stops at the first failure. Config and
shared conftest changes select all tests. Deleted and renamed modules remain in the selection.

Trial specs are private TOML files with `project`, `total_cap_usd` and `[[trials]]` tables.
Every trial requires `name`, `type`, `what`, `why`, `tests`, `out_of_scope`, `depth`, `engine`
and `cap_usd`. Optional keys are `shape`, `model`, `cross_engine`, `role_models` (a list of
`role=model` strings), `acceptance` (command strings), `checks` (tables with `file` and
`regex`), and `recovery_note`. Paths in checks are relative to the acceptance copy.
Keep client paths and original requests under ignored `.cuanta/`. Mark reconstructed fields
in `recovery_note`; missing historical request text cannot be claimed as an exact replay.

The trial script previews commands and caps with `--dry-run`, selects one trial with `--only`,
and skips acceptance commands and checks with `--skip-accept`. Live execution requires enough
of the total cap to reserve the next trial's cap. It launches only sandbox mandates, stops
on unknown costs or an exceeded cap, and labels reported and token-priced estimated costs.
Each acceptance run uses a fresh owned copy, validates stored after-image hashes, runs the
commands and file/regex checks, and removes the copy. Full output, costs, estimates, turns,
tokens and first-request cache metrics are saved in `summary.json` and a Markdown table.
It prints `cuanta runs accept` and `cuanta runs reject` commands; the builder decides.

On POSIX use `uv run --no-sync python scripts/dev/gate.py`, `focus.py` or `trial.py` with the
same arguments. Acceptance commands are argument lists on POSIX and use cmd on Windows.
