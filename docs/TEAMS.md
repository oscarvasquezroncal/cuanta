# Teams

A pipeline mandate runs as a team of roles: analyst, senior, tester and docs. A team uses one
provider, Claude or GPT (Codex), and each role can use a different, current model from that
provider. Teams that mix providers were retired after 0.4.0; see [Retired: mixed teams](#retired-mixed-teams).

## Choosing the provider

The provider is the engine the mandate runs on. Claude is the default.

```bash
cuanta mandate --type feature --what "..." --why "..." --tests "..." --out-of-scope "..."
cuanta mandate --engine codex --type feature --what "..." --why "..." --tests "..." --out-of-scope "..."
```

Without `--engine`, the mandate uses the `engine` setting from `.cuanta/config.toml`. OpenCode keeps
its single-engine behaviour and is not a team provider.

In the app, the Team step offers two chips, **Claude team** and **GPT team** (**Equipo Claude** and
**Equipo GPT** in Spanish). Each role's card shows its model, its tier and its price per million
tokens (input and output). The card's menu lists only that provider's models, each with its price,
so a role can be pinned without leaving the provider. The preview, in the app and in
`cuanta mandate --dry-run`, lists each role's model.

Team also recommends a provider for the task type from measured runs: the provider with the lowest
cost per accepted change, once it has an accepted run and every attempt of that type has a known
cost. Without such a provider it recommends none. The count includes Claude pipeline mandates and
runs of one launch per role on a single provider; a Codex pipeline mandate that ran as one Codex
session is left out, because a GPT team does not run that way.

## Models per role

Each role asks for a tier, and the provider's default model for that tier fills it.

| Role | Tier | At quick depth |
|---|---|---|
| Analyst | standard | standard |
| Senior | premium | standard |
| Tester | standard | standard |
| Docs | economy | economy |

The Claude team's main session (the orchestrator) runs on standard as well.

| Tier | Claude team | GPT team |
|---|---|---|
| economy | `haiku` (Haiku 4.5) | `gpt-6-luna` |
| standard | `sonnet` (Sonnet 5) | `gpt-6-sol` |
| premium | `opus` (Opus 5.5) | `gpt-6-sol` |

The defaults live in `src/cuanta/assets/model_tiers.toml` (`[defaults.claude]`,
`[defaults.codex]`) and were checked on 2026-09-27 against `codex debug models --bundled` and the
OpenAI pricing page. The GPT team's premium default is `gpt-6-sol` because the current `gpt-6`
family has no premium-priced model: `gpt-6-astra` is frontier-priced ($10.00 input and $50.00
output per million tokens, 2.5 times Opus 5.5), and `gpt-5.6-sol` belongs to the older generation.
Frontier is never a default, and older generations such as `gpt-5.6-*` are never chosen by
default; they stay available by pinning.

A default wins over the general search when the model is in your catalog (`cuanta models`). When it
is missing, the router falls back to another model of that tier on the same provider, preferring the
engine's own default model, and steps to the nearest tier when none is left. Your routing policy's
caps and the depth's cap apply first. Tier overrides set with
`cuanta models tier` do not replace the defaults; pin the role instead.

## Pinning a role

`--role-model role=model` pins a role to a model of the team's provider. The model can be written
with its engine, as in `engine:model`.

```bash
cuanta mandate --role-model senior=sonnet ...
cuanta mandate --engine codex --role-model senior=gpt-5.6-sol ...
```

Every pin is honored or refused before anything launches, with the reason:

- the model is not in the catalog (`cuanta models` lists it);
- the model belongs to the other provider, for example
  `senior: codex:gpt-6-sol belongs to another provider; a team uses one provider (claude)`
  (in Spanish, `un equipo usa un solo proveedor`);
- the route chose a different model.

## How each team runs

### Claude team

The Claude team runs natively: one Claude session, with each role as a subagent from the agents file
cuanta generates for the run. Each subagent carries its role's model, from the defaults or your
pins.

Because the whole team is one session, cuanta cannot step in between its roles; any check that runs
inside the session is run by the roles themselves. When the session ends, cuanta checks the run: it
compares file snapshots, reports changes to protected paths and edits outside the plan, and refuses
to apply an isolated copy with a protected change. To have cuanta run the type check, lint and build itself
between roles, run the Claude team as separate launches with `--cross-engine`.

### GPT team

A GPT team (`--engine codex` on a pipeline mandate) runs one launch per role, in order: analyst,
senior, tester, docs. cuanta passes an anchored handoff from role to role, runs the project's checks
after each writing role, keeps every role inside its share of one spending cap, salvages a role that
stops on budget and checks the working copy after every role. Simple-mode mandates, and
investigations unless run with `--shape pipeline`, stay a single Codex session on one model. So do
the fix mandates of `cuanta loop --engine codex`: each fix runs as one Codex session.

Codex can already run subagents in one session, each on its own model (contract CX-11). cuanta does
not use them yet: a child's token usage appears only in its own rollout file, not in the JSONL that
`codex exec` prints, so cuanta would count the parent's cost and miss the children's.

The app caps a GPT team with the Team step's cap. From the command line, a GPT team's whole run is
capped by `--max-budget-usd`, else the `--depth` cap, else the `budget.usd` setting, else the normal
depth's cap, the same cap the app gives it; `--cross-budget-usd` sets the cap explicitly. A Claude team run with `--cross-engine` keeps its $1.00
default unless `--cross-budget-usd` is given. `--max-budget-usd` is always an upper bound on a team
run. The dry run shows the cap.

Stop, in the app's pipeline screen, ends a team run: the running role's process is stopped and no
later role starts. A stop while cuanta runs its checks between roles ends the running check's process
tree and starts no other check. The run ends partial, or failed when no role had started.

## What each engine guarantees

| Guarantee | Claude | Codex |
|---|---|---|
| Spend cap | enforced natively | checked after the run; no native spend limit |
| Turn limit | enforced | not available |
| Read-only analyst | checked after each role from the working copy | enforced by the `read-only` sandbox |
| Telemetry | checked after the run | checked after the run |
| Index tools | owned MCP server, generated per launch | owned MCP server through a per-launch `mcp_servers` entry |

The index server is on by default for pipeline roles (`runs.pipeline_index_tools`): every per-role
launch, and native Claude pipelines when cuanta generates their session profile. Set it to `false`
to give roles text packs only.

## How roles build on each other

This applies to teams that run one launch per role.

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

The role prompt leads with the anchored chain and says how to open a range, so a role reaches for
`page` instead of a whole-file read. Earlier indexed pipelines, whose index instructions came last,
called only `note`.

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

## Forecast before launch

Before a mandate starts, cuanta forecasts its cost from the plan, the depth, the team's models and
prices, the fixed context each launch pays first, the expected cache warmth and earlier runs. The
Team step and the dry run show one line, for example "Forecast $0.31 (P90 $0.45), margin $0.15,
warm cache (78%)", and `--json` adds an `envelope` object with the tokens by bucket (start,
exploration, writing, verification, handoff), each role's forecast and stop rules, and the verdict.

- **P50 and P90.** The P50 adds each role's forecast. The P90 is the P50 times the 90th percentile of
  actual/P50 once five runs of the same provider and type have a known cost; before that it is 1.6
  times the P50.
- **Margin and verdict.** The margin is the cap minus the P90. A forecast is infeasible when the
  P50 is over the cap, tight when the P90 is over the cap or within 15% of it, and comfortable
  otherwise. Tight and infeasible forecasts show the cheapest change first: a shallower depth, a
  cheaper model for the costliest role, a narrower WHERE, or the scout shape.
- **Fixed context.** Each launch's fixed first-request tokens come from earlier runs of the same
  model, role and shape when telemetry measured them; otherwise from measured medians (Claude one
  session 19.7K, native team main session 35.6K, analyst 19.5K, senior and tester 47.9K, docs
  34.8K; Codex 20K per role), marked as not measured. A Claude team in one session also pays for
  its main session: the pack, one task prompt and one report per role.
- **Cache warmth.** Each model has its own warmth. A model is warm when one of its requests falls
  inside the cache window (`cache.ttl_s`); its share is how much of the first request earlier warm
  starts of that model read from the cache. Claude cache writes are priced at the one-hour rate
  (twice the input price) when the window is longer than five minutes, and at the five-minute rate
  otherwise.
- **Fixes.** Bug and fix mandates are sized from fix history only, and the writer gets a planned
  repair turn. A team of separate launches holds a repair share for it: 15% of the cap when docs
  runs, or docs's share when docs is off. The repair turn gets that share plus the writer's
  left-over.
- **Stored with the run.** Every launched mandate stores its forecast before it starts, keyed by
  the run (the first role's run for a team of separate launches). The actual is the run's recorded
  cost; a cost that is not known stays n/a and is left out of the numbers.
  `cuanta instinct calibration` and the app's Instinct view show, per provider and type, the runs,
  the P50 error in dollars and percent, the P90 coverage and the P90 error.
- **Jev, opt-in.** With `instinct.envelope = true` and consent for Jev (`cuanta instinct use jev`),
  cuanta sends Jev a numbers-only summary of the forecast (plan sizes, warmth, reach, risk, earlier
  factors and margin; never code, paths or text) and asks for risk and exploration multipliers and
  a tier per role. The call is priced first and is not made above $0.001. The answer counts by its
  confidence times Jev's track record on earlier forecasts, which starts at 20%.

## Guards between roles

cuanta snapshots the working copy after every role. A change to a protected path stops the pipeline
before the next role starts and names the role that made it.

## The Windows limitation

On Windows, Codex's sandbox cannot run the project's build (contract CX-09). In the default elevated
mode the sandbox account cannot start `node`; in the unelevated mode any child process that uses
pipes fails, so `next build` stops when it starts its workers.

The GPT team's tester therefore stays on Codex and never runs node, npm or npx commands (builds or
tests): its prompt tells it not to, and cuanta runs the checks itself after each writing role. The Team step shows the limitation
on the senior and tester cards, and the dry run adds it as a line.

A file Codex creates inside cuanta's private copy can be unreadable to the user, because the copy
folder only grants access to its owner. Before a Codex writer runs, cuanta creates the plan's new
files so they stay readable, removes the ones that stay empty, and reports any file it cannot read.

## Retired: mixed teams

Version 0.4.0 offered `cuanta mandate --cross-engine --mix` with three presets: `claude-only`,
`claude-plans-codex-writes` and `codex-plans-claude-writes`, the last two putting Claude and Codex
roles in one team. `--mix`, the presets and the app's mix chips are gone, and a pin to the other
provider is refused before launch. `--cross-engine` on a Claude team replaces `claude-only`.

On a production Next.js landing page, Claude only cost $0.91 per accepted feature, Codex plans,
Claude writes $1.18 and Claude plans, Codex writes $1.99; a GSAP fallback fix was accepted by none
of them. The full matrix is in [the mixed-teams trial report](trials/2026-09-27-mixed-teams.md).
