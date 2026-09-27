# Index acceptance and benchmark observations

## Local acceptance

The synthetic Next landing fixture contains 16 files and 5,344 source bytes. Its CSS has
1,250 lines, with the report anchor spanning lines 1190–1225 and the changed opacity at 1200.
The actual local index puts the four cart and checkout modules in the top five with reasons.
Stored CSS findings appear on cards and packs, then retain their provenance as stale after
the anchored line changes. The GSAP plan preserves the hero guard and sends its current
finding to the senior pack. Offline launch checks inspect Claude deny settings and tools.

Measurements on Windows 11 10.0.22631, AMD64, Python 3.12.13:

| Fixture | Files | Full index | Three changes | Original budget |
| --- | ---: | ---: | ---: | --- |
| Python inventory | 300 | 4.073 s | 0.356 s | full <10 s; incremental <2 s |
| Synthetic Next landing | 16 | 0.633 s | — | full <10 s |

These measurements use local extraction. The small synthetic fixture has wildcard npm
dependencies without an installed lock; it does not establish a production Next build.
Its failed-initialization regression intentionally reproduces the GSAP fallback bug.
Existing performance thresholds and benchmark mini/full suite membership remain unchanged.

## Capped sandbox comparison

Two identical tiny checkout fixes ran through the trial driver with Sonnet 5, simple single
context, quick depth and a $0.55 cap each. The index-on condition also enabled the owned MCP
server; the index-off condition retained lexical change-plan protection. External acceptance
executed the repaired function and checked that the protected source remained unchanged.
Each immutable trial changed only checkout.py; original fixture sources stayed unchanged.

| Index | Reported usage cost | Seconds | Turns | Raw reads | Index calls | Exploration estimate | Accepted | Guard / unplanned edits |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| on | $0.0866196 | 13.78 | 2 | 0 | 0 | 0 tokens | yes | 0 / 0 |
| off | $0.0864016 | 10.75 | 3 | 1 | 0 | 8 tokens | yes | 0 / 0 |

The real API model was claude-sonnet-5 in both runs. Both started cold: the first request
was 19,340 tokens with the index and 18,019 without it; fixed context was 18,793 versus
17,584. The on run connected Cuanta successfully but made no exploration calls to it.
The pack avoided a raw read on this tiny task, while the extra fixed context made reported
usage cost $0.000218 higher. One run per mode cannot establish general savings.
Exploration figures estimate returned bytes separately from API usage tokens.

Total reported subscription usage was $0.1730212. This is an estimate of consumed usage,
not a billing statement. The shared V2/V3 $3 benchmark allowance retains $2.8269788.
Investigation answer validation remains pending C5. Hooks and complete MCP writer coverage
remain unverified; owned tools stay opt-in outside this explicit comparison.
