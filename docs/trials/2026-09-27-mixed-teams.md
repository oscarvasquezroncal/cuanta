# Mixed teams on a production landing page

X3 runs the same three requests with three team presets on a retained private snapshot of a production Next.js landing page. Every trial runs in a sandbox copy, the original project receives no changes, and no patch is applied. Requests are frozen: the Organization JSON-LD feature (the fully specified X1 request), the sitemap, robots and canonical feature (T1b-2) and the GSAP reveal fallback fix (T1c). Acceptance reuses the frozen commands and checks from E1 and V4, plus the V4 GSAP harness for the fix.

All trials run in cross-engine mode so the same pipeline code runs. Docs is optional (Haiku 4.5). Claude costs are the CLI's subscription usage estimates; Codex costs are token-price estimates. Neither is an invoice.

| Condition | Team | Cap per task |
|---|---|---|
| A | Claude only (Sonnet 5 analyst, senior and tester) | $1.00 |
| B | Claude plans, Codex writes (Sonnet 5 analyst, Codex gpt-6-sol senior, Sonnet 5 tester) | $1.30 |
| C | Codex plans, Claude writes (Codex gpt-5.6-terra analyst, Opus 5.5 senior, Sonnet 5 tester) | $1.50 |

## Trials

| Trial | Completion | Cost | Verification between roles | Acceptance | Outcome |
|---|---|---|---|---|---|
| x3-jsonld-A | complete | $0.9650 of $1.00 | senior #1 passed in 13.3 s | 2/2 commands, 1/1 checks | accepted |
| x3-jsonld-B | complete | $0.7774 of $1.30 | none (no code change) | 2/2 commands, 0/1 checks | rejected |
| x3-jsonld-C | complete | $1.3128 of $1.50 | senior #1 passed in 13.4 s | 2/2 commands, 1/1 checks | accepted |
| x3-seo-A | complete | $0.8561 of $1.00 | senior #1 passed in 13.0 s | 2/2 commands, 3/3 checks | accepted |
| x3-seo-B | complete | $1.2081 of $1.30 | senior #1 passed in 16.3 s | 2/2 commands, 3/3 checks | accepted |
| x3-seo-C | complete | $1.0413 of $1.50 | senior #1 passed in 12.9 s | 2/2 commands, 3/3 checks | accepted |
| x3-gsap-A | partial | $0.9492 of $1.00 | senior #1 passed in 16.1 s | 3/3 commands, 0/2 checks; GSAP harness 3/14 | rejected |
| x3-gsap-B | partial: tester has no remaining reserved budget | $2.1725 of $1.30 | senior #1 failed in 11.5 s | not run: the trial passed its cap, so the runner stopped before acceptance; GSAP harness blocked (the patch changes CSS) | rejected |
| x3-gsap-C | complete | $1.4096 of $1.50 | senior #1 passed in 15.2 s | 3/3 commands, 0/2 checks; GSAP harness blocked (the patch changes CSS) | rejected |

## Roles

| Trial | Role | Engine and model | Share | Native cap | Cost | Overrun | Salvaged | Handoff tokens | Covered | Read | Re-read |
|---|---|---|---|---|---|---|---|---|---|---|---|
| x3-jsonld-A | analyst | claude claude-sonnet-5 | $0.2800 | $0.2520 | $0.3007 | - | yes | 0 | 0 | 2 | 0 |
| x3-jsonld-A | senior | claude claude-sonnet-5 | $0.3393 | $0.3054 | $0.3042 | - | no | 59 | 2 | 2 | 2 |
| x3-jsonld-A | tester | claude claude-sonnet-5 | $0.2751 | $0.2476 | $0.1966 | - | no | 322 | 2 | 2 | 2 |
| x3-jsonld-A | docs | claude claude-haiku-4-5 | $0.1985 | $0.1786 | $0.1635 | - | no | 508 | 2 | 4 | 0 |
| x3-jsonld-B | analyst | claude claude-sonnet-5 | $0.3640 | $0.2760 | $0.3509 | - | yes | 0 | 0 | 4 | 0 |
| x3-jsonld-B | senior | codex gpt-6-sol | $0.4811 | $0.4811 | $0.0436 | - | no | 79 | 4 | 0 | 0 |
| x3-jsonld-B | tester | claude claude-sonnet-5 | $0.7495 | $0.5684 | $0.1676 | - | no | 196 | 4 | 1 | 1 |
| x3-jsonld-B | docs | claude claude-haiku-4-5 | $0.7379 | $0.5596 | $0.2152 | - | no | 382 | 4 | 8 | 4 |
| x3-jsonld-C | analyst | codex gpt-5.6-terra | $0.3826 | $0.3826 | $0.4950 | $0.1124 | no | 0 | 0 | 0 | 0 |
| x3-jsonld-C | senior | claude claude-opus-5-5 | $0.5334 | $0.4001 | $0.2562 | - | no | 296 | 0 | 1 | 0 |
| x3-jsonld-C | tester | claude claude-sonnet-5 | $0.5916 | $0.4437 | $0.4027 | - | no | 454 | 1 | 2 | 1 |
| x3-jsonld-C | docs | claude claude-haiku-4-5 | $0.3461 | $0.2596 | $0.1588 | - | no | 651 | 2 | 7 | 2 |
| x3-seo-A | analyst | claude claude-sonnet-5 | $0.2800 | $0.2100 | $0.2839 | - | yes | 0 | 0 | 8 | 0 |
| x3-seo-A | senior | claude claude-sonnet-5 | $0.3561 | $0.2670 | $0.2090 | - | no | 157 | 8 | 1 | 1 |
| x3-seo-A | tester | claude claude-sonnet-5 | $0.3871 | $0.2903 | $0.1605 | - | no | 436 | 8 | 3 | 1 |
| x3-seo-A | docs | claude claude-haiku-4-5 | $0.3466 | $0.2599 | $0.2026 | - | no | 981 | 10 | 7 | 5 |
| x3-seo-B | analyst | claude claude-sonnet-5 | $0.3640 | $0.2730 | $0.3003 | - | yes | 0 | 0 | 8 | 0 |
| x3-seo-B | senior | codex gpt-6-sol | $0.5317 | $0.5317 | $0.4301 | - | no | 177 | 8 | 5 | 2 |
| x3-seo-B | tester | claude claude-sonnet-5 | $0.4136 | $0.3102 | $0.2430 | - | no | 476 | 11 | 3 | 3 |
| x3-seo-B | docs | claude claude-haiku-4-5 | $0.3267 | $0.2450 | $0.2347 | - | no | 662 | 11 | 8 | 6 |
| x3-seo-C | analyst | codex gpt-5.6-terra | $0.3863 | $0.3863 | $0.3604 | - | no | 0 | 0 | 0 | 0 |
| x3-seo-C | senior | claude claude-opus-5-5 | $0.6577 | $0.4932 | $0.2724 | - | no | 360 | 0 | 1 | 0 |
| x3-seo-C | tester | claude claude-sonnet-5 | $0.7066 | $0.5299 | $0.1844 | - | no | 637 | 3 | 3 | 3 |
| x3-seo-C | docs | claude claude-haiku-4-5 | $0.6828 | $0.5121 | $0.2241 | - | no | 690 | 3 | 8 | 3 |
| x3-gsap-A | analyst | claude claude-sonnet-5 | $0.2800 | $0.2100 | $0.2547 | - | yes | 0 | 0 | 8 | 0 |
| x3-gsap-A | senior | claude claude-sonnet-5 | $0.3853 | $0.2890 | $0.3375 | - | yes | 156 | 7 | 6 | 5 |
| x3-gsap-A | tester | claude claude-sonnet-5 | $0.2879 | $0.2159 | $0.2065 | - | no | 239 | 7 | 3 | 3 |
| x3-gsap-A | docs | claude claude-haiku-4-5 | $0.2013 | $0.1510 | $0.1505 | - | yes | 426 | 7 | 6 | 5 |
| x3-gsap-B | analyst | claude claude-sonnet-5 | $0.3640 | $0.2730 | $0.3535 | - | yes | 0 | 0 | 6 | 0 |
| x3-gsap-B | senior | codex gpt-6-sol | $0.4785 | $0.4785 | $1.8189 | $1.3405 | no | 138 | 5 | 8 | 3 |
| x3-gsap-C | analyst | codex gpt-5.6-terra | $0.3863 | $0.3863 | $0.4557 | $0.0694 | no | 0 | 0 | 0 | 0 |
| x3-gsap-C | senior | claude claude-opus-5-5 | $0.5624 | $0.4218 | $0.3166 | - | no | 340 | 0 | 2 | 0 |
| x3-gsap-C | tester | claude claude-sonnet-5 | $0.5670 | $0.4253 | $0.3934 | - | no | 465 | 2 | 5 | 2 |
| x3-gsap-C | docs | claude claude-haiku-4-5 | $0.3342 | $0.2507 | $0.2439 | - | no | 1000 | 4 | 4 | 0 |

## Cost per accepted change

| Cohort | Attempts | Accepted | Spend | Per accepted change |
|---|---|---|---|---|
| X3 A | 3 | 2 | $2.7703 | $1.3851 |
| X3 B | 3 | 1 | $4.1579 | $4.1579 |
| X3 C | 3 | 2 | $3.7637 | $1.8818 |
| X1 JSON-LD trio (Claude alone, mix, Codex alone) | 3 | 2 | $2.2497266 | $1.1248633 |
| V4 T1 cohort | 7 | 6 | $3.09550835 | $0.5159181 |
| V4 E1 cohort | 6 | 4 | $4.98831550 | $1.2470789 |

## Acceptance against the X3 criteria

- Claude roles against their share: the largest overshoot was 7.4% (x3-jsonld-A, analyst); native caps sat 10% below the share in the first trial and 24 to 25% below it once the learned margin updated from measured overruns.
- Codex overruns, charged to the remainder and shown in the result: x3-jsonld-C analyst $0.1124; x3-gsap-B senior $1.3405; x3-gsap-C analyst $0.0694.
- Re-reads of files the handoff already covered, per receiving role, against the analyst's own reads (Claude analysts only: Codex reads through its shell are not attributed, only reads through the index tools are):

| Trial | Analyst reads | Mean re-reads per receiving role | Ratio |
|---|---|---|---|
| x3-jsonld-A | 2 | 1.33 | 0.67 |
| x3-jsonld-B | 4 | 1.67 | 0.42 |
| x3-seo-A | 8 | 2.33 | 0.29 |
| x3-seo-B | 8 | 3.67 | 0.46 |
| x3-gsap-A | 8 | 4.33 | 0.54 |
| x3-gsap-B | 6 | 3.00 | 0.50 |

Receiving roles include the writer, which must open a file before Claude's Edit tool changes it, and the docs role, which reads the files it documents.

## Findings

- **Most pipelines finished.** Seven of nine trials ended complete. The GSAP fix ended partial twice. Under Claude only, the senior stopped at its share before finishing; that run's result did not name the cause, which cuanta now does ("the change may be incomplete"). Under Claude plans, Codex writes, the Codex senior spent $1.8189 against a $0.4785 share, which left the tester no budget: for that trial the criterion that no pipeline stops because an earlier role used its share is not met, and cuanta now names the overrunning role in the stop message. Claude analysts hit their native cap in six trials (two went over their share, by 7.4% and 1.4%); each time cuanta saved a partial handoff and the next role continued.
- **One Codex senior refused a partial handoff.** In the first Claude plans, Codex writes trial the Codex senior followed its role instructions literally and reported itself blocked because the salvaged analyst handoff had no plan JSON, so it changed nothing while the tester and docs still ran. cuanta now stops a pipeline when a required role reports that it is blocked, names the cause, and tells the role after a partial handoff to treat the chain as its plan. The next Codex senior (the sitemap, robots and canonical feature) continued from a salvaged analyst and was accepted. The first two trials ran before this change; the other seven ran with it. The blocked trial was not repeated: the remaining $1.31 of the matrix cap could not absorb a possible Codex overrun.
- **Codex has no live spend limit.** Codex roles are checked after they end, and every overrun is charged to the remainder and shown. The largest was $1.3405 on the GSAP fix, which took that trial past its task cap; the matrix and the plan totals stayed inside their caps.
- **Verification by cuanta replaced builds inside Codex's sandbox.** Every writer's change was checked with the project's type check, lint and build in 11 to 17 seconds at no model cost. One check failed (lint on the Codex GSAP fix); its writer had already spent past its share, so it got no repair turn.
- **The GSAP fix was not accepted in any condition.** Claude only ran out of budget after changing a shared helper; the Codex writer changed the right components but broke lint and rewrote the global stylesheet; Opus moved the fallback into the shared helper and the stylesheet, which the frozen checks do not accept and the V4 harness cannot evaluate. The fix needs more than $1.00 to $1.50 with these teams.
- **Measured recommendation.** For features in this matrix, cost per accepted change was lowest with Claude only ($0.91 per accepted change across the two accepted features, against $1.18 for Codex plans, Claude writes and $1.99 for Claude plans, Codex writes, which had one of its two features accepted). Team recommends a preset from every cross-engine run in the project's ledger, which here also includes the earlier X1 and V4 cross runs; on this snapshot it shows "Measured feature runs recommend Claude only: $0.9106 per accepted change (attempts: 2)" and, for fixes, no recommendation because no fix was accepted.
- **Against earlier cohorts.** X1's Claude-only JSON-LD change cost $0.2030562 as a native pipeline in one session; the cross-engine Claude-only runs here cost $0.86 to $0.97 because every role is a separate launch with its own context, verification and docs. The mix is reported as measured: it costs more than Claude alone on these tasks.
