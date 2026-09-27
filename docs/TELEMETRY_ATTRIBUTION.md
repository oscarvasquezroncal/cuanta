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
