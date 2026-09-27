# Mixed teams

A mixed team runs each pipeline role (analyst, senior, tester, docs) as its own launch, on Claude or
Codex, with any model from that engine's catalog. cuanta passes an anchored handoff from role to
role, checks the working copy between roles, runs the project's checks itself, and keeps the whole
pipeline inside one spending cap.

## Choosing a team

From the command line:

```bash
cuanta mandate --cross-engine --mix claude-plans-codex-writes --cross-budget-usd 1.30 \
  --type feature --what "..." --why "..." --out-of-scope "..." --sandbox
```

| Preset | Analyst | Senior | Tester | Docs |
|---|---|---|---|---|
| `claude-only` | Claude | Claude | Claude | Claude |
| `claude-plans-codex-writes` | Claude | Codex | Claude | Claude |
| `codex-plans-claude-writes` | Codex | Claude | Claude | Claude |

A preset chooses each role's engine; the router still picks the model tier. Pin a model with
`--role-model role=engine:model`, for example `--role-model senior=codex:gpt-6-sol`. Every pin is
either honored or refused before anything launches, with the reason:

- the model is not in the catalog (`cuanta models` lists it);
- the launch cannot use that engine (a native Claude run cannot route a role to Codex; add
  `--cross-engine`);
- the tester is pinned to Codex on a Windows host, where Codex cannot run builds;
- the route chose a different model.

Team also recommends a preset for the task type from measured runs: the preset with the lowest
cost per accepted change, once it has an accepted run. Without accepted runs it recommends none.

In the app, the Team step shows the same three presets as chips. Without a preset the mandate runs
as the native Claude pipeline, where every role shares one session. With a preset, each card shows
the role's engine, model and share of the cap, what the engine guarantees, how the role receives
context, and any warning.

## What each engine guarantees

| Guarantee | Claude | Codex |
|---|---|---|
| Spend cap | enforced natively | checked after the run; no native spend limit |
| Turn limit | enforced | not available |
| Read-only analyst | checked after each role from the working copy | enforced by the `read-only` sandbox |
| Telemetry | checked after the run | checked after the run |
| Index tools | owned MCP server, generated per launch | owned MCP server through a per-launch `mcp_servers` entry |

The index server is on by default for pipeline roles (`runs.pipeline_index_tools`): every
cross-engine role, and native Claude pipelines when cuanta generates their session profile. Set it to
`false` to give roles text packs only.

## How roles build on each other

Each role ends with a JSON handoff: a summary, decisions, facts anchored as `path:start-end` with the
claim, the plan's edit, read and verify sets, open questions, the next step and a status (done,
partial or blocked). When a role does not return valid JSON, cuanta builds the handoff from its last
text and the line ranges it actually read. The files changed always come from cuanta's snapshots,
never from the role's own report.

Before a handoff is passed on, every anchor is checked against the working copy. A range whose lines
changed is marked stale and listed separately; it is never passed as fresh. Each role receives the
merged chain of all earlier roles, compacted to about 1,200, 2,000 or 3,000 tokens for quick, normal
and deep runs. Full texts stay in the capsule store and are referenced by id. Roles are told to open
the anchored ranges, not whole files, and not to re-read files the chain covers unless an anchor is
stale.

Earlier indexed pipelines had the tools and a connected server, yet feature and fix roles called
only `note`: the index instructions came last in the role prompt, after the role's own reading
habits and the pack excerpts. The role prompt now leads with the anchored chain and says how to open
a range, so a role reaches for `page` instead of a whole-file read.

## Verification between roles

After each role that changes code, cuanta runs the change plan's verify commands (type check, lint,
build and tests detected for the project) in the working copy, as the user, never inside an
engine's sandbox. Each command has a timeout and its first errors are kept as `path:line` lines. The
commands come only from cuanta's own plan; a command suggested by a model is never run.

When a check fails, the writer gets one repair turn inside its remaining share, and cuanta checks
again. If it still fails, the tester receives the results. Verification appears in the result as its
own rows, with its time and $0 model spend. Sandbox runs verify by default; in-place runs list the
commands before they start.

## Budgets that finish

- **Shares.** Each role has a floor (analyst 12%, senior 20%, tester 8%, docs 4% of the cap). The
  rest is split by the planned cost of each role, or by the median cost of earlier runs once every
  role has three completed runs with the same engine, model, task type and depth. Later roles' shares
  are reserved before a role starts, and unspent money flows forward.
- **Soft stop.** A Claude role's native cap sits below its share by a margin learned from measured
  overruns, 10% until there are enough of them.
- **A budget stop is partial, not fatal.** cuanta saves a salvage handoff from what the role read and
  the pipeline continues when the remainder still covers the next roles' floors.
- **Optional roles.** Docs, and the tester once verification has passed, are skipped with a recorded
  reason when the remainder is below their floor.
- **Blocked roles.** A required role that reports it is blocked stops the pipeline as partial, and
  the result names the cause. After a partial handoff, the next role is told to treat the chain as
  its plan and continue.
- **Codex.** Codex has no live spend signal; its cost is checked after the run, any overrun is taken
  from the remainder, and the result shows it.

A run ends in one of four states, shown in the result, `cuanta runs show` and `cuanta costs`:
complete; complete with optional roles skipped; partial; failed. The cost per accepted change counts
every attempt.

## Guards between roles

cuanta snapshots the working copy after every role. A change to a protected path stops the pipeline
before the next role starts and names the role that made it.

## The Windows limitation

On Windows, Codex's sandbox cannot run the project's build. In the default elevated mode the sandbox
account cannot start `node`; in the unelevated mode any child process that uses pipes fails, so
`next build` stops when it starts its workers. cuanta therefore runs the checks itself, keeps the
tester on Claude by default, and refuses a Codex tester pin on this host.

A file Codex creates inside cuanta's private copy can be unreadable to the user, because the copy
folder only grants access to its owner. Before a Codex writer runs, cuanta creates the plan's new
files so they stay readable, removes the ones that stay empty, and reports any file it cannot read.

## Measured

On a production Next.js landing page (`docs/trials/2026-09-27-mixed-teams.md`), seven of nine
cross-engine trials ended complete and the other two named their cause. Two features were accepted
with Claude only ($0.91 per accepted change), two with Codex plans, Claude writes ($1.18), and one of
two with Claude plans, Codex writes ($1.99). A GSAP fallback fix was not accepted in any preset
within $1.00 to $1.50. A Codex writer overran its share by $1.34, which cuanta charged to the
remainder and reported. Each role there is a separate launch with its own context, verification and
docs, so a cross-engine Claude-only team costs more than the native Claude pipeline for the same
change.
