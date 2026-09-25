# Graph integration — verified command surface

Read this before Phase 0.5. Everything here was verified against a real deployment; anything
unverified carries a `[VERIFY]` marker and must be reported, never guessed into a command.

## The knowledge split this integration exists to enforce

| | Graph (AST-derived) | Rulebook (`CLAUDE.md`) | Facts file (`docs/GROUND_TRUTH.md`) |
|---|---|---|---|
| Holds | what the code IS: symbols, calls, imports, inheritance, blast radius | what it SHOULD be and WHY: contracts, flag defaults, traps, proven-not-bugs | expensive derivations from EXTERNAL sources: framework internals, vendor API semantics |
| Derived | automatically, always | from experience; unrecoverable if lost | once, at real cost |
| Ages | no — re-index and it is current | yes — needs a ceiling and a budget | no — append-only, dated, cited |
| Cost | ~2k tokens per query | loaded in full, every message | read on demand |

> The graph owns navigation. The rulebook owns judgment. The facts file owns expensive
> external derivations. Nothing is stored in two places.

No AST parser can ever hold "this deprecated-looking call is a working compatibility shim —
flagging it CRITICAL is the bug", or "flag OFF must be byte-identical to legacy". That is
institutional judgment; it is not in the syntax tree and never will be. That is why the
rulebook survives — and why, once a graph exists, the rulebook must be aggressively emptied of
everything structural.

---

## graphify — VERIFIED surface

Verified against graphify **0.9.11** (`graphify --help`, the tool's own `SKILL.md`, and a live
`graphify update` + query run). The CLI binary is `graphify`; the Python package is
**`graphifyy`** — the names differ, do not "correct" one into the other.

### Install (verified — from graphify's own skill source)

```bash
uv tool install graphifyy          # preferred; graphify's own installer tries uv first
pip install graphifyy              # fallback
uv tool install --upgrade graphifyy   # upgrade
```

Presence check, in this order:

```bash
command -v graphify && graphify --version
```

### Index / re-index (verified non-interactive, no LLM, no API key)

```bash
graphify update .                  # builds graph.json from scratch if absent; incremental after
graphify update . --force          # overwrite even when the rebuild has fewer nodes (post-refactor)
```

`graphify update` is the **only** fully non-interactive build path and it is the one Phase 0.5
uses in every branch. Verified: run in a directory with no `graphify-out/`, it performs a full
AST extraction, clusters, and writes the artifact tree — exit 0, no prompts, no network.

### Query (verified)

```bash
graphify query "<question>" --budget 2000    # BFS traversal; --budget caps output tokens
graphify query "<question>" --dfs            # trace one specific path instead
graphify explain "<symbol>"                  # node + neighbors, plain language
graphify path "<A>" "<B>"                    # shortest path between two nodes
graphify affected "<symbol>" --depth 2       # reverse traversal = blast radius
```

### Artifacts (verified)

```
graphify-out/graph.json          # the graph — every query reads this
graphify-out/GRAPH_REPORT.md     # plain-language report
graphify-out/graph.html          # interactive visualization
graphify-out/manifest.json       # build manifest
graphify-out/cache/              # AST cache, versioned by graphify release
```

`graphify-out/wiki/index.md` (agent-crawlable wiki) is produced only by the richer
agent-driven pipeline with `--wiki`, **not** by `graphify update`. Do not promise it in a
generated rulebook unless the directory is actually present on disk.

### Claude Code wiring (verified)

```bash
graphify claude install     # writes a graphify section into CLAUDE.md + a PreToolUse hook
graphify claude uninstall
```

Phase 0.5 does **not** run `graphify claude install` by default: it writes into the same
`CLAUDE.md` this skill owns, and two writers on one file is exactly the double-payment this
upgrade removes. Offer it in the final report as an optional extra, nothing more.

### Semantic build — NOT a plain CLI command

The richer build (docs, papers, images, INFERRED edges, community naming) runs through
graphify's own agent-driven skill pipeline (`/graphify <path>`), needs an LLM backend, and is
interactive by nature. **Never call it from this skill.** Phase 0.5 is a bootstrap, not a
research run. Mention it in the final report as the user's optional next step.

---

## Other graph tooling

Detection may be written from observable filesystem/config evidence. Install commands may not.

- **MCP graph server already configured** — evidence: an entry in `.mcp.json`,
  `.claude/settings.json`, or `~/.claude.json` whose command or name matches a graph server
  (`graphify-mcp`, `*-graph-*`, `code-graph*`). Detection is safe to write.
  Installing or registering an arbitrary graph MCP server is
  `[VERIFY: MCP graph server registration command — confirm per server before release]`.
- **Language servers / LSP indexers** — detection from a `.lsp`, `compile_commands.json`, or an
  editor config is safe. Install commands are
  `[VERIFY: language-server install command — confirm per tool before release]`.

## Failure policy

A graph is an accelerator, never a dependency. If install, init, or index fails for any
reason — network, permissions, unsupported language, non-zero exit — record the failure,
continue the whole pipeline with `GRAPH_MODE=none`, and write grep-based navigation rules
instead. **A failed graph install must never abort the skill.**

Equally: a stale graph is worse than no graph, because agents distrust it and re-explore,
paying for both. Every branch that wires a graph must also write the re-index instruction into
the rulebook and into the docs-updater contract.
