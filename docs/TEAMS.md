# Teams

A pipeline mandate runs as a team of roles: analyst, senior, tester and docs, or, in the
[scout and senior shape](#scout--senior), a scout in place of the analyst. A team uses one
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
| Scout | economy | economy |

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
- the pinned role does not run in the chosen shape, for example an analyst pin with
  `--shape scout` or a scout pin with `--shape pipeline`;
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
`codex exec` prints, so cuanta would count the parent's cost and miss the children's. A Codex role
can also spawn a subagent on its own: in the recorded mixed-teams fix trial the senior did, and the
child spent about $0.84 that the ledger never saw. cuanta therefore launches and resumes every Codex
role with `--config features.multi_agent=false` and `--config features.multi_agent_v2=false`.

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

- **Shares.** Each role has a floor (analyst and scout 12%, senior 20%, tester 8%, docs 4% of the
  cap). The rest is split by the planned cost of each role, or by the median cost of earlier runs
  once every role has three completed runs with the same engine, model, task type and depth. Later
  roles' shares are reserved before a role starts, and unspent money flows forward.
- **Soft stop.** A Claude role's native cap sits below its share by that model's overshoot in
  dollars: the 90th percentile of how far its earlier runs went past their native cap (about one
  request), or $0.08 until the model has three such runs. The cap never drops below half the share.
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

## Governor

While a Claude role runs, cuanta reads its stream and projects its spend: the cost of the next
requests from the context each one rereads, and whether the cap arrives before the plan's edits are
done.

- **Stream-json input.** When the installed Claude Code lists `--input-format` in its help, cuanta
  launches each Claude role of a team of separate launches, and the session of a native Claude team,
  with `--input-format stream-json`. The prompt goes in as one stream-json user message, stdin stays
  open until the run's result, and cuanta closes it then (contract CC-12). Other launches keep
  plain-text stdin. Without the flag, the launch is the same as before, and the soft stop and
  salvage above still end a role that reaches its cap.
- **Finish now.** When a role has spent 85% of its share, when two more requests would reach its
  native cap, or when the projection says the cap arrives before the plan is done and is at most four
  requests away, cuanta sends one turn: "termina ahora: aplica lo que está completo, escribe el
  handoff y lista lo que falta". The projection waits for three requests of the running agent, so
  its context growth is measured, not guessed from the first request. The turn asks for the JSON
  handoff, with status partial when work remains. Claude reads it between tool calls, after at most
  one more tool call. It is sent once per role. When it cannot be sent, the progress shows it, and
  the native cap and salvage stop the role instead.
- **A native Claude team.** The whole session is governed against the mandate's cap. The finish
  turn reaches the main agent only: a subagent that is running when it is sent finishes its own work
  first, and the main agent reads the turn when that subagent returns. cuanta cannot cut a subagent
  short.
- **Rotation.** In a team of separate launches, when a fresh session costs less than continuing,
  cuanta asks the role for a checkpoint handoff, ends that session and starts the role again with its
  prompt and the checkpoint. The comparison is the remaining requests rereading the current context
  at the cache-read price, against one checkpoint request plus the role's fixed prefix written once
  and reread. A role rotates at most once, never after it has spent 85% of its share, and does not
  restart when the checkpoint leaves 15% of its share or less; its checkpoint is then its handoff.
  The result lists both launches, the first marked as rotated, and that run stores the estimated
  saving as `rotation_saving_usd`.
- **Codex stop and finish.** Codex reports no spend while it runs (contract CX-10), so cuanta
  estimates a Codex role's spend from its completed items and elapsed time: the role's forecast per
  request, and the model's median cost per second over at least three earlier finished runs. When
  the estimate reaches 85% of the role's share, or two more items would reach it, cuanta ends the
  process tree with the same teardown a stop uses. When the installed `codex exec resume --help`
  shows the `exec resume` usage with a `SESSION_ID`, and lists `--json`, `--config`, `--model` and
  `--skip-git-repo-check`, it then runs
  `codex exec resume <thread> -` (the sandbox as `-c sandbox_mode=...`, the working folder as the
  process's; contract CX-12) with one short finish turn that lists the files changed in the working
  copy, and asks for the handoff. Work still running at the stop is lost. The stopped launch is
  recorded with the estimated cost, labelled as an estimate, never zero. When the thread cannot be
  resumed, or the finish turn fails, the stopped role is salvaged as a role that hit its cap: a
  partial handoff from the changed files, and the pipeline continues when the remainder covers the
  next floors. The governor checks the estimate at each Codex event; a single long item is not
  interrupted.
- **Read discipline, on by default in teams.** Every Claude role of a team of separate launches, and
  the session of a native Claude team whose profile cuanta generates, runs with cuanta's
  `PreToolUse`/`PostToolUse` hooks (contracts CC-10 and CC-11): a `Read` of a file above
  `runs.read_max_lines` lines (400) without an offset and limit is blocked and pointed to a line
  range, or the Cuanta page tool when the role has it; a content `Grep` over the whole tree without a path, glob or type is blocked; raw test
  commands (pytest, npm test, npx jest or vitest and the like) run as `cuanta test`. Each blocked call
  is logged with the tokens the read would have cost (file size ÷ 4). A team role's own settings file
  is generated even in a full session so the hooks apply. `runs.pipeline_read_discipline = false`
  turns this off; single launches keep `runs.read_discipline` (off by default). Codex cannot run
  these hooks, so a Codex role gets the same rules in its prompt only, labelled best effort.
- **The Result panel.** The Consumption tab of a governed run shows a governor panel ("Gobernador"
  in Spanish): each reaction with the seconds since the role started, the spend and the limit, the
  trigger and its outcome; the estimated saving (a rotation's saving once it restarted, and a Codex
  stop's projected overrun past the role's limit, n/a when the plan gives no end, such as a role
  that has used its planned requests before its first planned edit; a finish turn saves no dollars,
  since the native cap bounds the role either way); the blocked calls with the tokens avoided; and
  which roles had the read discipline enforced or best effort. Below 30 rows the Result screen
  scrolls, and its tabs keep at least 16 rows. `cuanta runs show --json` and `cuanta mandate --json`
  add a `governor` object with the same facts; the mandate's own JSON leaves `blocked` null for a
  native session, whose blocked calls `runs show` counts from the ledger.
- **Off.** `runs.governor = false` wires no governor: no finish turn, no Codex stop or resume and no
  rotation, and Claude launches keep plain-text stdin. The native cap, the USD margin and the
  salvage still end a role that reaches its cap.

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
  left-over. A feature whose docs is off holds docs's share the same way (see
  [Docs is optional](#docs-is-optional)).
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

## Scout & senior

A feature or a fix whose reading dominates its cost runs in the scout and senior shape: a cheap,
read-only scout explores and hands the senior a short evidence pack, so the senior spends its
premium tokens on the change, not on exploring.

- **When.** Before launch, cuanta forecasts the mandate in the pipeline shape. When the forecast's
  exploration bucket is above `runs.scout_threshold` (0.35) of its tokens (start, exploration,
  writing, verification and handoff together), the mandate runs as scout and senior; otherwise the
  pipeline stays. Refactors and investigations keep their shape. `--shape scout` or
  `--shape pipeline` forces it (`--shape single|pipeline` still chooses an investigation's shape).
  A pin decides it too: `--role-model analyst=...` keeps the pipeline, `--role-model scout=...`
  runs the scout shape, so a pin never depends on the forecast. `--shape scout` is refused for
  investigations, with `--simple`, on opencode and with `--route off`, where no scout could run.
  The dry run and the Team step say which shape runs and why, and `--json` adds a `shape` object
  (`pinned` is true when a pin chose it). The app's run keeps the shape its Team step showed.
- **Scout.** The `scout` role, on the economy tier (`haiku`, `gpt-6-luna`); pin it with
  `--role-model scout=...`. It is read-only (Read, Grep and Glob on
  Claude; the `read-only` sandbox on Codex) and runs with the index tools and the read discipline of
  the other team roles. It ends with an evidence pack in JSON: `file:line` facts, the few snippets
  the change needs, risks, test links and the confirmed edit set, the files the senior may edit.
- **The pack.** cuanta drops facts and snippets whose lines are not in the working copy and paths
  that leave the project, replaces each snippet's text with the lines of its range in the working
  copy, so the senior never reads a snippet the scout made up, caps each section (40 facts, 12 snippets of 40 lines, 8 risks, 12 tests)
  and trims the pack to about 6,000 tokens: snippets first, from the last, then facts beyond the
  first twelve. The edit set is never trimmed; a pack still over its budget says so. When the scout
  confirms no edit set, the senior gets the change plan's; when it returns no pack, the senior gets
  the ranges it read. The pack is stored as a capsule (`cuanta cat cap:...`) and Result's
  Consumption tab shows it in a "Scout and senior" panel ("Explorador y senior"), as do
  `cuanta runs show` and `--json` (`scout`).
- **Senior.** It receives the request, the pack and the edit set, and no transcript of the scout.
  Its prompt carries a read budget from the forecast's stop rules, never fewer reads than the edit
  set has files plus two. Every read outside the pack's
  files and the edit set counts as exploration leak: the governor counts it live, and Result lists
  the files. The senior may edit a file outside the edit set only when it must and names it in its
  handoff's `plan.edit`; Result flags every such edit, named or not.
- **Then.** cuanta runs its checks, as after any writing role. The tester gets the diff of the
  changed files against their text before the senior, the scout's test links and
  `cuanta test --affected`, and is told not to explore again. On Windows a GPT tester runs no tests
  (see below).
- **Claude team.** `runs.scout_mode = native` (the default until the bench picks) runs the scout as a
  subagent of the one Claude session: the main agent is told to call the scout first, pass the senior
  only the pack and its edit set, and give the tester the changed files and `cuanta test --affected`.
  cuanta cannot step in between subagents, so it trims nothing there; it reads the pack from the
  session afterwards for Result and counts the dispatched prompt tokens of the scout, senior and
  tester. The edits it flags are the changed files the senior subagent wrote with its own edit
  tools (Edit, Write, MultiEdit, NotebookEdit) outside the edit set; the tester's test files, the
  docs-updater's edits and changes made through shell commands are not counted. A run where the
  main agent never called the scout says so in Result (`scout.dispatched` is false). Leaks are not
  measured in a native session. `runs.scout_mode = launch` runs the scout as its own read-only
  launch, then one launch per role, as below. `--cross-engine` always launches the scout. Each run
  records its mode (`scout.mode`).
- **GPT team.** A read-only `gpt-6-luna` scout launch, then the senior launch with the pack, through
  the per-role runner; the run records `launch` as its mode. Codex's own subagents stay off.

### Docs is optional

The docs role runs only when the request asks for docs: one of docs, documentation, documentación,
documentar, documenta, documente, documenten, README, CHANGELOG, guide, guides, guía, guías,
docstring, docstrings, release notes or notas de la versión, as whole words in any case and with or
without accents, in its WHAT, WHERE or TESTS. WHY, CONSTRAINTS and OUT OF SCOPE are not read, so
"keep the CHANGELOG in sync" in CONSTRAINTS leaves docs off, and the bare word "document" does not
count (`document.querySelector`). It never runs in trials, the isolated copies of `--sandbox`.
Pinning the docs role (`--role-model docs=...`) asks for it: docs then runs, in trials and with
`runs.docs = off` too. The start of the run says which applies. `runs.docs = on` runs docs always,
as before; `off` never, unless pinned.
When docs is off, a team of separate launches holds the docs share as its repair reserve, for
features as for fixes, and a native Claude session is told not to call the docs-updater.

## Warm queue

Mandates queued together run back to back, so each one after the first finds its prompt prefix
(system prompt, tools, project rules and agents) still in the cache and reads it instead of writing
it again.

- **Queue.** `cuanta queue add` takes the options of `cuanta mandate` (`--type`, `--what`,
  `--engine`, `--model`, `--max-budget-usd`, `--depth`, `--sandbox` and the rest) and checks them
  when it queues: an unknown option, a missing field or a bad choice is refused then, because the
  queue runs unattended. Global options such as `--json` and `--project` belong to the queue command
  and are not stored; paths such as `--evidence` are read when the mandate runs. The queue lives in
  `.cuanta/queue.json`, and its ids (`q1`, `q2`, ...) are never reused. The file keeps each
  mandate's options as typed, the request text included, until the mandate finishes green or is
  cleared, whatever `privacy.store_prompts` says: the queue needs them to run it later. Each write
  holds `.cuanta/queue.write.lock` for a moment, so a `queue add` from another shell during a run
  is never lost.
- **Order.** `cuanta queue list` shows the order `queue run` uses. Mandates on the same engine and
  model run together, and `--sandbox` mandates form their own group: each isolated copy has its own
  folder, which Claude Code puts in its prompt, so a copy probably does not share a warm prefix with
  the project. The groups follow the order in which their first mandate was queued, and each group
  keeps the queue order. A mandate without `--model` groups with the others on that engine's
  default model. `--preset`, `--depth` and role pins do not split a group, although they can change
  the models a mandate's roles run on. Inside a mandate the roles keep their order.
- **Run.** `cuanta queue run` shows the order and asks first; `--yes` skips the question, and without
  a terminal the command only shows the order. Each mandate runs as `cuanta mandate` would, with its
  own cap, depth, shape and isolated copy, and never stops to ask. A mandate that finishes green
  leaves the queue. The run stops at the first failed mandate and keeps it and the rest queued;
  `--keep-going` runs the rest and keeps only the failures. One `queue run` at a time per project: a
  second one is refused while `.cuanta/queue.lock` exists, and a crashed run's lock can be deleted.
  Before each mandate, the run reads the queue again and skips one that has left it since the list
  was shown (another run finished it, or `queue clear` removed it): it shows as "no longer queued"
  and is not launched. `queue clear` removes only the mandates it listed when it asked.
- **Warm prefix.** `queue list`, `queue run` (after each mandate) and the app's Home show "warm
  prefix until HH:MM" ("prefijo caliente hasta HH:MM"): the last Claude request plus the saved cache
  TTL (`cache.ttl_s`, measured by `cuanta probe cache-ttl`). It is unknown when no TTL is saved for
  the current auth mode, on Codex, and before a Claude request that used the cache. Home shows the
  line only while mandates are queued. A `queue.json` that cannot be read shows "Mandate queue
  unreadable" ("Cola de mandatos ilegible") on Home instead, and `cuanta queue list` names the
  problem.

## Metrics

Each attempt (a mandate, or the first run of a team of separate launches) gets its metrics from the
ledger, its run record and, for an isolated copy, its patch. Result's Consumption tab shows them in
a metrics panel ("Métricas" in Spanish), `cuanta costs --metrics` lists them per run for its window
(`--json` adds `metrics`), and Spectrum's Trend tab and `cuanta spectrum --trend` follow the cost
per accepted change. A value cuanta cannot know stays n/a, never zero.

- **Forecast against actual.** The stored forecast's P50 and P90 against the attempt's recorded
  cost. The cap used is the actual divided by the forecast's cap (the run's cap without a forecast);
  P90 minus actual is negative when the run went past its P90. By bucket, the forecast's tokens stand
  next to the tokens seen in the run's requests: start is the whole context of each agent's first
  request; exploration is what each later request added (fresh input and cache writes) outside
  handoffs; writing is the output of every request; handoff is what a session took in when a
  subagent returned. Verification stays n/a: its tokens are not attributed to their own bucket yet,
  so they count in exploration. A role of a team of separate launches receives its handoff in its
  first request, so it counts in start. A run without request telemetry, such as a Codex run
  (contract CX-10), has no observed buckets.
- **Cost per accepted change.** An accepted attempt's cost; n/a when the run is not accepted or its
  cost is unknown. Tokens per accepted changed line divide every token of the attempt by the added
  and removed lines of its patch. Only an isolated copy (`--sandbox`) keeps the patch, so an
  in-place run shows n/a.
- **Blocked reads.** The reads the read-discipline hooks blocked and the tokens they would have cost
  (file size ÷ 4). Known when a role or the native session ran with the hooks, so a run with the
  hooks and nothing blocked shows 0; n/a otherwise.
- **Finishes and rotations.** Finish turns and Codex stops that were sent, and rotations that
  restarted, each with the governor's estimated saving; a finish turn saves no dollars, so its saving
  is n/a. A governed run records its governor even when it never reacted, so it shows 0; n/a when
  no governor watched the run (`runs.governor` off, classic mode, or a launch that cannot be
  steered).
- **Warm cache.** Cache reads divided by cache reads, cache writes and fresh input, over the first
  request of each launch and over every request.
- **Scout and senior.** The pack's tokens after cuanta's checks, and the input tokens the senior read
  (fresh input, cache reads and cache writes), from the senior launch or the senior subagent.
- **Trend.** For each provider and task type, its last 20 attempts (`--last N`): the cost per
  accepted change, counting every attempt as `cuanta costs` does, and a line of that value after
  each run, oldest first, with a dot before the first accepted change.
- **Mode.** `cuanta costs --metrics --json` reports each run's `mode`: `classic` for a run launched
  with `--classic` (see below), `v5` otherwise.

## Classic mode

`cuanta mandate --classic` runs one mandate the way V4 did, so the same task can be compared with
and without V5. For that run only it forces the pipeline shape where the scout could run (features,
fixes and refactors), turns docs on (`runs.docs = on`), turns the read discipline off
(`runs.read_discipline` and `runs.pipeline_read_discipline`) and turns the governor off
(`runs.governor`). Investigations and `--simple` mandates keep their own shape, as they did in V4.
`--shape scout` is refused with `--classic`. The run's metadata records `mode: classic`; a run
without it counts as `v5`. `cuanta queue add --classic` keeps the flag for the queued mandate.

Classic does not bring back the proportional margin that V5B removed: a Claude role's native cap
still sits the USD margin below its share. That margin applies only to a Claude team of separate
launches (`--cross-engine`); the Claude team runs natively in one session capped by the mandate's
cap, so a comparison of the native Claude team is unaffected. A GPT team's roles have no native
cap, so the margin does not apply to them either.

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
