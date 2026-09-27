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
