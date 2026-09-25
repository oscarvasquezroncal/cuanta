# Agent instructions

Read [CLAUDE.md](CLAUDE.md) for project rules and [CONTRIBUTING.md](CONTRIBUTING.md)
for the Git workflow and verification commands before changing this repository.

- Preserve the hexagonal layers, strict typing, and English/Spanish catalogs.
  Follow the no-comments/no-docstrings rule and run the required gates.
- Work only on `main`. Start with `git status`; when a remote exists, run
  `git fetch` and `git pull --ff-only`. Stop and ask if history has diverged.
- After the gates pass, commit only through `scripts/git/commit.cmd`, with
  separate conventional commits for each change type. Only the user runs
  `scripts/git/push.cmd`. Do not push, force, rebase, stash, clean, or rewrite
  published commits.
- Add no AI attribution to commit messages: no AI coauthor trailers,
  generated-by footers, Claude session trailers, or session links.
- Never commit secrets, credentials, local user paths, client details, or private
  session prompts. Keep local plans and reports under the ignored `.cuanta/`
  directory and keep `.claude/settings.local.json` untracked. Never commit ZIPs.
- Keep the agreed milestone scope. Do not weaken tests to obtain a passing gate.
  Skip an OS-specific test only when the feature is Windows-only by design,
  with that reason in the skip.
- Record the handoff locally and stop at the milestone boundary.
