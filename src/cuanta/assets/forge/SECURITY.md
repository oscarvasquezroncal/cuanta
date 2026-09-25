# Security

`claude-agent-forge` is a set of Markdown instructions plus two installer scripts. It ships no
runtime and no service, so this policy is short and describes what is actually true.

## What the skill does not do

- **No network calls.** The one exception is the graph install in Phase 0.5 (`uv tool install
  graphifyy` / `pip install graphifyy`), which runs only when the repo is large enough to earn a
  graph, and which fails soft — the pipeline continues without a graph.
- **No git operations, ever.** Not `commit`, `add`, `stash`, `checkout`, or `restore`. Ignore
  rules are written as a file edit. The only git-related output is a commit message *proposal*
  you run yourself.
- **No execution of your repo's code** beyond the commands your own manifest or CI config already
  declares — the test, lint, typecheck and build commands it detects and copies verbatim.
- **No telemetry, no analytics, no data leaving your machine.**

## What it writes

Documentation and agent contract files only: `CLAUDE.md` (root and per-directory),
`.claude/agents/`, `AGENTS_GUIDE.md`, the `docs/` artifacts, `HISTORIAS.md`, and
`.claude/forge-state.json`. Never source, configs, or tests. Runs are autonomous, so **review the
diff** — that is the intended safety net, and why the docs say to commit or stash first.

## Supported versions

The latest release only. Older versions are not patched; run `/refresh-agents` after updating.

## Reporting an issue

Open an issue on [the repository](https://github.com/oscarvasquezroncal/claude-agent-forge). For
something you would rather not post publicly, say so without the details and a private channel
will be arranged. There is no bug bounty and no guaranteed response window — claiming otherwise
would invent a process this project does not have.
