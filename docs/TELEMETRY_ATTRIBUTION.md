# Agent attribution

An empty query source with no explicit agent attributes receives the raw metadata marker
`attributed="default"`. It remains uncertain even when its display name is main. Named agent
attributes and explicit query sources such as sdk take precedence over inferred windows.

The resolver first matches an Agent or Task tool decision to a subagent_completed event by
run, session and agent_type. It infers only inside a closed, unambiguous window. Otherwise,
adjacent API requests from the same known agent can bracket a tool by timestamp. Missing
session identities, conflicting neighbors, open windows and overlapping different agents
remain unresolved. Decision labels do not increase the tool consumption count.

Historical completion-only captures retain the prior inference only when their session has
no decision or completion-window evidence. Repeated resolution preserves labels and totals.

The approved real investigation fixture in
`tests/fixtures/telemetry/real_investigation.json` preserves 127 events, relative timing,
agent names, tool names, bytes, tokens and costs. Its 31 calls inside the analyst window
contain 25 Read, 2 Glob, 3 Grep and 1 Bash, totaling 56,725 result bytes. An explicit parent
SDK request occurs 36 ms after the Agent decision and stays main. The 12 API requests
retain their combined $0.54546 usage cost.

Paths and identifiers are anonymized; path depth and final extension are preserved. Times
shift to a fixed 2026-01-01 base. Raw prompts, commands and source text are omitted using
an allowlist. Existing stored agent values remain unchanged in the fixture; its default
marker is derived from the original empty source and absent explicit attributes. The source
ledger was opened read-only and its byte hash remained unchanged.

Owned MCP observations without a caller session remain uncertain. The capture proves the
observed payload and this resolver's behavior; it does not guarantee that every engine
version emits complete delegation boundaries.

Request anatomy assigns each selected usage event exactly once. Start is the first request
per run, session and agent. Writing is an output-dominant request after that agent's last
observed tool call; its comparison uses fresh input plus cache write, excluding cache read.
Other requests are exploration unless they match a handoff. A handoff is the next parent
request after a matched closed child window with no intervening parent tool work. Its cache
write must match the final child output within 50% plus 256 wrapper tokens. It is a heuristic,
not proof that a model summarized the child or that those tokens were avoidable.

The real fixture partitions into 2 start, 8 exploration, 1 writing and 1 handoff requests:
65,648, 367,203, 59,855 and 49,255 tokens respectively. Their costs are $0.2483865,
$0.1839955, $0.0615582 and $0.0515198, preserving 541,961 tokens and $0.54546 in total.
The child output is 4,769 tokens; the next parent writes 6,717 cache tokens after 16.485 s.
The explicit parent SDK request inside the child window remains a parent request.

Second-context markers require a closed, unambiguous delegation window and an observed child
start. Possible re-summary markers come from the handoff heuristic. Investigation advice
suggests a single context; neither marker is a causal waste or billing estimate. JSON anatomy
contains event identities and numerical usage, without raw prompts or tool parameters.

Phase medians count complete priced attempts only. Missing role usage, any unknown request
price, or fewer observations than a known positive role turn count exclude the attempt.
This conservative turn check can exclude otherwise valid captures because provider turn
semantics vary. Coverage is displayed; missing costs never become zero. Billed economic
attempt totals, outcomes and cost per accepted change remain separate and unchanged.

Read efficiency counts unique successful source files from raw Read, View, NotebookRead and
Cuanta page calls. Pending, denied and failed observations, searches and handling cards do
not count as source reads. Repeated calls, line ranges and owned/native mirrors cannot increase
the file count. Citations reuse the report's file:line parser and deduplicate paths; the parser
scans the report text, including fenced examples, and does not validate a cited line's truth.

For investigations, the numerator is observed reads cited by the report. For code work it is
observed reads either edited or cited. Unread citations or edits cannot inflate the ratio above
one. Missing reports differ from observed empty reports; a zero denominator stays unavailable.
Source and recorded sandbox roots map absolute paths to relative source paths, including saved
copies that were removed. External paths and prefix lookalikes are not basename-matched.
Cross-role read efficiency includes observed child reads while legacy Spectrum token totals
retain their selection scope. Missing pipeline reports preserve the v1 token heuristic.

## File history and allocated cost

`cuanta models stats --files` reads existing compatible closed index and ledger snapshots.
It never creates either store, migrates a schema, repairs corruption, refreshes models or
calls an engine. Missing, incompatible, linked or busy snapshots are visibly unavailable.
Ordinary `models stats` keeps its existing behavior.

The index owns task history and its existing 30-day half-life, outcome, retry and stale-anchor
weights. No file-priors ledger migration exists. Each grouped attempt's known cost is allocated
once across unique files with observed read/edit/cite history, using normalized existing
history priors. Duplicate actions and cross-engine child roles cannot multiply the allocation.
Rows group by file, task type and mix; cross-engine cost is not attributed to an individual
role model. These retrospective allocations are heuristics, not measured or causal file cost.

Unknown prices remain unavailable, with explicit unknown samples and known subtotals. Known
cost with no usable history remains unallocated. The report conserves known total as allocated
plus unallocated cost. Estimated-price and stale-history samples remain visible. Deleted or
stale file history can still explain past allocation; it does not claim current source freshness.

## Investigation benchmarks

The dedicated `investigation` suite contains Next cart/checkout and Python pricing tasks;
mini and full task sets stay the same. For example:

```text
cuanta bench run --suite investigation --reps 1 --shape single --pack on --depth normal
```

Without `--yes`, the bench shows the plan and ceiling without spending. Answer acceptance
uses the delivered root report, all configured case-insensitive keywords and complete
file:line patterns; cited files must exist inside the prepared sandbox and lines must be in
range. Windows drive-prefixed paths, escaped or absent source and empty reports fail. These
bounded checks do not prove the explanation is semantically correct. Investigations also
reject every observed source edit, even if their answer passes. Code-task acceptance remains
the configured command suite.

Actual shape, pack and depth accompany every numerical run record. Baseline investigations
use a single read-only context and no Cuanta pack even when another shape is requested.
`--pack off` disables automatic packs independently from index/MCP; plans and tools remain.
Explicit `cuanta pack` stays available. Omitted shape/depth preserve existing Cuanta defaults.

Reports show observed request-phase totals with coverage and file utilization by condition.
The ratio sums useful/read counts from covered runs only; absent reads/reports stay unknown.
Preloaded packs do not count as observed source calls. Phase prices do not replace billed run
costs. Historical numerical reports without these fields load with conservative empty defaults;
raw delivered answer text is transient for acceptance and is absent from bench numerical JSON.

## Runs that end before their result

When the engine is halted, crashes or is interrupted before its result event, the run's cost and
turns come from what was received. The run's `api_request` events (telemetry) are used first,
each with its own reported cost when present and the price table otherwise; without telemetry,
the stream's assistant usage is priced instead and stored as one `result_usage` row per model.
Turns are the distinct main-thread assistant message ids (subagent requests, which carry a
`parent_tool_use_id`, count toward cost only). The run is marked `partial`: totals show it as a
lower bound (≥), and it never calibrates forecasts, estimate errors, role history or cap margins.
Stream output tokens are message-start snapshots, so a stream-priced partial cost undercounts.

## Records the listener cannot read

A log record, metric point or span that the mapper cannot read is dropped and replaced by a
`telemetry_unreadable` event (source `cuanta`, the run id from `cuanta.run_id`, raw holding only
the record name and the error class). The run result counts these events in one Note, `/health`
reports `unreadable`, and the traceback goes to `.cuanta/logs/listener.log`. Redaction runs on
values, so JSON stored inside a string value (such as `tool_input`) is parsed, redacted and stored
again in compact form.

Codex and OpenCode runs cut by a wall limit or a user stop before their turn completed are
partial too and are priced from the received telemetry (`sse_event:response.completed` rows for
Codex); zero-token usage rows are ignored, so a cut run with no usage shows n/a, never $0.
Governor and budget stops keep their own accounting.

`/health` reports `unreadable` (records the mapper could not read) and `dropped` (events the
ledger could not store). An event the ledger cannot store becomes a `telemetry_unreadable` marker
with the error class; non-finite numbers are stored as the strings `NaN`, `Infinity` and
`-Infinity`, and integers outside the signed 64-bit range read as 0 while raw keeps the original.
A run served by an already-running background listener counts its markers right after the engine
exits, without the linger and drain of a listener cuanta starts itself, so a record lost in the
engine's final export can be missing from the Note.

Decoding is guarded per record as well: a log record, metric point or span whose numbers overflow
while decoding becomes a `telemetry_unreadable` marker for its run and the rest of the POST lands
with 200; only a payload whose resource structure cannot be read gets 400.
