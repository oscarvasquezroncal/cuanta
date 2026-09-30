# WhatsApp ordering: F1 implementation trials

Neither implementation was accepted. The Claude candidate failed lint, and the GPT team
reported an access failure before changing any files. A passing link-builder harness for
Claude does not establish that the complete feature works.

Implementation trials ran on September 30, 2026 UTC.

## Scope and method

Both teams received the same frozen Spanish request for a production Next.js landing page:
reuse its typed catalog; add individual and combined WhatsApp orders; persist the cart safely
for server rendering; add an accessible floating button; emit Product/Offer JSON-LD and an
optional analytics event; and expose a pure, encoded link builder. The contact remains a
placeholder; no real contact details were introduced.

One read-only investigation cost $0.3498164, exceeding its $0.30 sub-cap by $0.0498164. The
implementation caps were frozen at $2.85 for Claude and $2.70 for GPT, within the $6.30 F1
budget. The investigation stopped at its native spend cap after 105.28 s
(`error_max_budget_usd`) and was already recorded as rejected; that outcome was preserved.
This was a spend-budget stop, not an account usage quota or reset. Both trials used V5 defaults and deep depth, sequentially, in isolated copies. The
retained source was never patched. No candidate was repaired or retried after its trial.

Before implementation, the private link-builder harness failed on the unchanged source and
passed all eight checks on a reference implementation in a disposable owned copy. Those
checks cover phone normalization, product/quantity/variant/page URL, encoding, multiple
items, priced totals, empty or zero-quantity orders, and missing or digit-free phones.

## Measured results

| Team | Cost | Cap | Trial-driver duration | Changed files | Outcome |
|---|---:|---:|---:|---:|---|
| Claude | $2.7608653 reported | $2.85 | 1,026.19 s | 10 | Rejected |
| GPT | $0.10981924 estimated | $2.70 | 461.31 s | 0 | Rejected: environment blocked implementation |

Claude cost is the CLI-reported subscription usage estimate. GPT cost is estimated from
token usage: $0.01477804 for the scout and $0.09504120 for the senior. Trial-driver durations
include launch/setup overhead; separate acceptance and harness work is excluded.

The implementation attempts cost $2.87068454. Including the investigation, F1 cost
**$3.22050094 of $6.30**, leaving $3.07949906. There were no unknown F1 costs, account usage-limit
failures, deferred trials, or retries. Zero accepted changes means cost per accepted feature
is undefined.

| Acceptance | Claude | GPT |
|---|---|---|
| TypeScript | Pass | Pass on unchanged source |
| Lint | Fail | Pass on unchanged source |
| Build | Not run after lint failure | Pass on unchanged source |
| Generated Product/Offer JSON-LD | Unverified: build skipped | Fail |
| Generated WhatsApp URL | Unverified: build skipped | Pass from existing source |
| Builder export and floating-button file checks | Pass | Fail |
| Pure link-builder harness | 8/8 pass | Fail: export absent |
| Complete request | Not accepted | Not implemented |

The acceptance runner stops commands at the first failure. Claude's generated HTML checks
therefore do not prove a JSON-LD or URL defect; no fresh build output was available to check.
GPT's successful compiler, lint and build checks only validate the existing source.

## What the teams delivered

Claude stored changes for the order builder, cart drawer and persistence, product buttons,
optional availability, analytics, structured data, floating button and page/styles wiring.
Lint rejected the synchronous `setPageUrl` call inside an effect in `WhatsAppFab.tsx` under
`react-hooks/set-state-in-effect`. The recorded handoffs were
`architecture-analyst → frontend-senior → frontend-senior`; the senior used Opus. These
observations do not establish that the planned scout/tester sequence ran. The retained
report contains an initial analyst-wait message rather than a completed delivery report.
Section-aware behavior for an empty cart and mobile content clearance were not established
by this evaluation.

GPT ran `scout (gpt-6-luna) → senior (gpt-6-sol)` and produced no changes. The senior reported
that the isolated directory was inaccessible and that an index request's source range was
outside the current source. The overall mandate was partial and failed even though the
individual CLI processes returned success. No tester completed and no feature was delivered.
This is an environment-blocked attempt, not a measured implementation-quality result.

One fresh review examined both stored candidates against the request and acceptance
evidence. Neither is an accepted patch. Acceptance and harness copies were removed; stored
after-image hashes were checked, the frozen request and source hashes were preserved, and
dependency/state guards reported no changes. Nothing was applied to the production site.
