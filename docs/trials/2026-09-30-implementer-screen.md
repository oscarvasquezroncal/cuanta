# The implementer screen, 2026-09-30

Q3 ran two frozen feature requests on a private copy of a production Next.js landing page, once
per Claude model and variant, with the fast implementation profile: one native Claude session,
one pinned model for every request, verification and same-session repair by cuanta. Every trial
ran in a sandbox copy; nothing was applied to the project.

- **Tasks:** the Organization JSON-LD feature (quick depth) and the sitemap, robots and canonical
  feature (normal depth), with the frozen texts, acceptance commands (`npx tsc --noEmit`,
  `npm run build`) and build-output checks of the earlier R3 trials.
- **Models:** Opus 5.5 and Sonnet 5, the newest Sonnet in the installed Claude Code 2.1.283
  catalog (Sonnet 5.5 is not listed there). Efforts low, medium, high, xhigh and max for both;
  the fast-output setting (`fast-*`) for Opus only, as the catalog declares it.
- **Baseline:** the balanced V5 default, once per task, with its Haiku roles moved to Sonnet 5 and
  helper requests pinned to Sonnet 5.
- **Order:** one shuffled list of all rows, seed 20260930, so no variant always ran on a warm cache.
- **Caps:** $30.00 for the screen. Each trial reserved its native budget plus one in-flight request
  (Sonnet $1.60, Opus $3.20, fast Opus $6.40; three tails for a baseline). Costs are what the CLI
  reported (subscription usage estimates).

Total spend: **$12.7239** of $30.00 over 32 trials. No row was dropped for the cap.

## Variants checked before any spend

A loopback capture of each variant's first request, at no cost, showed the exact model and
`output_config.effort` for all fifteen fast variants; the five `fast-*` variants also sent the
fast-mode beta. Fast runs expose 15 tools after Q2 removed delegation, coordination and scheduling
tools (CC-29).

**Fast output did not take effect.** At runtime every request of the ten `fast-*` trials reported
`speed=normal`, and the CLI priced them at standard Opus 5.5 rates. The `fast-*` rows are therefore
standard-speed Opus runs that only requested fast mode; this account did not get it.

**Ultracode was not run.** It exists in print mode (CC-26), but its workflows let agents keep
spending while the main loop waits, Claude Code checks the native budget only on main-loop events,
and cuanta has no hard stop for Claude sessions. No reservation could bound a trial inside the
cap, so its six rows are recorded as blocked by the cap.

## Results

Wall is the whole `cuanta mandate --sandbox` command as the user waits for it: index refresh,
forecast, sandbox copy, the engine, verification and repairs, and the after-image capture; the
copy's removal runs afterwards in the background. Every cell is one trial. Repairs are the
same-session repair rounds of that trial.

| Variant | JSON-LD wall | cost | repairs | SEO wall | cost | repairs | Outcome |
|---|---:|---:|---:|---:|---:|---:|---|
| Sonnet 5 medium | 101.8 s | $0.0968 | 0 | 84.1 s | $0.1789 | 0 | accepted both |
| Opus 5.5 fast-low | 119.8 s | $0.2087 | 0 | 73.4 s | $0.0680 | 0 | accepted both |
| Opus 5.5 low | 119.7 s | $0.2571 | 1 | 76.3 s | $0.1637 | 0 | accepted both |
| Opus 5.5 fast-high | 125.0 s | $0.1201 | 0 | 86.8 s | $0.2096 | 0 | accepted both |
| Sonnet 5 low | 123.4 s | $0.1838 | 0 | 93.2 s | $0.1029 | 0 | accepted both |
| Opus 5.5 fast-medium | 130.0 s | $0.2684 | 1 | 98.4 s | $0.1777 | 0 | accepted both |
| Sonnet 5 high | 137.0 s | $0.2258 | 0 | 112.7 s | $0.2246 | 0 | accepted both |
| Opus 5.5 medium | 183.7 s | $0.3419 | 1 | 113.8 s | $0.1006 | 0 | accepted both |
| Opus 5.5 xhigh | 203.6 s | $0.5581 | 1 | 147.5 s | $0.3248 | 0 | accepted both |
| Opus 5.5 high | 260.1 s | $0.5206 | 1 | 92.6 s | $0.2252 | 0 | accepted both |
| Opus 5.5 fast-xhigh | 246.7 s | $0.6965 | 1 | 113.0 s | $0.1919 | 0 | accepted both |
| Sonnet 5 xhigh | 282.9 s | $0.4187 | 0 | 119.2 s | $0.1869 | 0 | accepted both |
| Balanced V5 baseline | 153.8 s | $0.3335 | 0 | 420.5 s | $1.2332 | 0 | accepted both |
| Opus 5.5 max | 335.4 s | $0.9600 | 1 | 323.9 s | $0.9461 | 0 | accepted both |
| Opus 5.5 fast-max | 230.0 s | $0.7647 | 0 | 340.4 s | $1.0092 | 0 | SEO only: JSON-LD hit the quick-depth turn limit |
| Sonnet 5 max | 661.2 s | $1.0373 | 0 | 178.3 s | $0.3885 | 0 | SEO only: JSON-LD hit its $1.00 native budget |
| Ultracode (Opus, fast Opus, Sonnet) | — | — | — | — | — | — | blocked by the cap |

Outcomes were recorded with `cuanta runs accept|reject` after the acceptance commands, the frozen
checks and a read of each diff against its request. One trial (Opus 5.5 fast-medium, JSON-LD)
first failed acceptance because `npm run build` crashed the Node process (`0xC0000005`) in the
acceptance copy; cuanta's own verification had built it green, and a rerun of the acceptance step
on the same after-images passed every command and check.

**Ranking** (accepted on both tasks, by total wall; ties by repairs, then cost):

1. **Sonnet 5 medium** — 185.9 s, no repairs, $0.2757.
2. **Opus 5.5 fast-low** — 193.1 s, no repairs, $0.2767.
3. **Opus 5.5 low** — 196.0 s, one repair, $0.4208.

Because fast output never took effect, ranks 2 and 3 are the same effective setup: Opus 5.5 at low
effort and normal speed. Q4 therefore takes the next variant in ranking order with a different
effective setup, Opus 5.5 fast-high (rank 4, 211.8 s), and runs it as Opus 5.5 high without the
fast-mode request. The three Q4 variants are Sonnet 5 medium, Opus 5.5 low and Opus 5.5 high.

Single trials are noisy. Opus at the same effort with and without the fast-mode request, both at
normal speed, took 260.1 s and 125.0 s on JSON-LD at high effort, and 183.7 s and 130.0 s at
medium.

Against the baseline, the top three were 2.9–3.1 times faster in total and 3.7–5.7 times cheaper;
the gap is in the larger feature (73–84 s against 420 s), where the baseline's analyst, senior,
tester and docs roles run one after another.

## Where the time went

Median seconds per phase over the 32 trials (phases can overlap; the request and tool times are
the sums of their intervals). The appendix lists every trial.

| Phase | Median | Range |
|---|---:|---:|
| Index refresh | 4.5 s | 3.5–9.7 s |
| Forecast and plan | 8.4 s | 6.4–24.0 s |
| Sandbox copy | 15.2 s | 13.3–29.5 s |
| Engine start to first event | 0.9 s | 0.8–3.1 s |
| Model requests | 43.7 s | 16.4–350.8 s |
| Tools | 16.5 s | 0.0–92.4 s |
| Verification (typecheck, lint, build) | 16.1 s | 9.7–78.1 s |
| Repair turn (7 trials, all JSON-LD) | 40.1 s | 22.7–54.8 s |
| After-image capture | 2.2 s | 1.8–5.4 s |

The median trial took 127.5 s. cuanta's own phases (index, forecast, copy, engine start,
verification and after-image) took a median of 64 s per trial (41–108 s), or 31 s without
verification.

- **The warm copy did not save time here.** It was reused after the first trial (no files
  copied), but each reuse still re-links all ~34,000 dependency files and walks the tree to check
  for drift. That took 13–30 s, as long as a fresh copy (15 s).
- **Writers try to run the checks themselves.** Bash is not pre-approved in fast runs, so the
  attempt is denied, and several reports say the checks were not run. cuanta then ran them; the
  denied attempt costs a turn.
- **Effort is the main cost and time lever.** Max effort was the slowest and most expensive level
  for both models and failed twice on the small feature (turn and budget rails). Low and medium
  were the fastest and cheapest for standard Opus and for Sonnet; among the `fast-*` runs, high
  beat medium on both.

## Purity

Every fast trial's recorded requests used only the chosen model, with no title or helper request
from another model. The baselines used only Sonnet 5 and Opus 5.5 (no Haiku). This verifies
CC-27's helper and title pins for these runs; no subagent ran, because fast runs deny delegation
tools.

## Limits

- One trial per cell; see the noise example above.
- The shuffled order still let some trials start on a warmer prompt cache than others.
- Reports are judged as a soft note only: the JSON-LD request asked to flag the phone number as a
  probable placeholder; 13 of the 14 accepted JSON-LD runs did, and Opus 5.5 fast-low (ranked
  second) did not.
- Costs are CLI estimates under a subscription, not invoices.

## Appendix: every trial

Seconds per phase; n/a means the phase did not occur.
| Trial | Wall | Index | Forecast | Copy | Start | Requests | Tools | Verify | Repair | After-image |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| jsonld sonnet 5 medium | 101.8 | 5.9 | 9.7 | 15.9 | 0.9 | 19.7 | 0.4 | 35.8 | n/a | 2.7 |
| jsonld opus 5.5 low | 119.7 | 4.2 | 7.8 | 14.7 | 1.0 | 43.6 | 0.3 | 35.9 | 22.7 | 1.9 |
| jsonld opus 5.5 fast-low | 119.8 | 6.5 | 12.8 | 15.2 | 0.9 | 23.4 | 0.3 | 41.1 | n/a | 3.7 |
| jsonld sonnet 5 low | 123.4 | 4.0 | 7.2 | 15.1 | 0.9 | 35.1 | 13.0 | 36.0 | n/a | 1.9 |
| jsonld opus 5.5 fast-high | 125.0 | 6.8 | 13.2 | 28.8 | 0.9 | 27.3 | 15.6 | 12.5 | n/a | 4.1 |
| jsonld opus 5.5 fast-medium | 130.0 | 4.4 | 7.6 | 14.9 | 1.1 | 48.9 | 0.3 | 36.3 | 23.9 | 2.0 |
| jsonld sonnet 5 high | 137.0 | 3.6 | 6.5 | 13.8 | 0.9 | 51.6 | 12.5 | 36.8 | n/a | 1.9 |
| jsonld baseline | 153.8 | 7.5 | 24.0 | 21.4 | 0.9 | 52.1 | 18.1 | 12.7 | n/a | 4.1 |
| jsonld opus 5.5 medium | 183.7 | 7.5 | 15.5 | 28.3 | 0.9 | 67.7 | 0.4 | 50.3 | 34.9 | 2.2 |
| jsonld opus 5.5 xhigh | 203.6 | 3.6 | 6.5 | 13.5 | 1.0 | 107.8 | 30.2 | 34.4 | 43.1 | 4.0 |
| jsonld opus 5.5 fast-max | 230.0 | 3.5 | 6.5 | 13.6 | 0.9 | 152.8 | 51.5 | 15.0 | n/a | 1.9 |
| jsonld opus 5.5 fast-xhigh | 246.7 | 4.2 | 8.0 | 14.3 | 0.8 | 121.2 | 39.8 | 46.4 | 40.1 | 2.8 |
| jsonld opus 5.5 high | 260.1 | 6.8 | 13.6 | 28.5 | 0.9 | 100.4 | 49.4 | 54.4 | 41.1 | 3.9 |
| jsonld sonnet 5 xhigh | 282.9 | 4.8 | 7.0 | 14.7 | 0.9 | 152.3 | 12.7 | 78.1 | n/a | 2.5 |
| jsonld opus 5.5 max | 335.4 | 4.1 | 7.2 | 14.3 | 0.9 | 235.7 | 30.6 | 36.0 | 54.8 | 2.2 |
| jsonld sonnet 5 max | 661.2 | 4.4 | 8.3 | 14.4 | 0.9 | 342.4 | 42.9 | 16.9 | n/a | 2.1 |
| seo opus 5.5 fast-low | 73.4 | 4.2 | 7.4 | 15.9 | 0.9 | 16.4 | 0.0 | 15.0 | n/a | 2.0 |
| seo opus 5.5 low | 76.3 | 4.5 | 8.7 | 15.8 | 0.9 | 18.1 | 0.0 | 14.7 | n/a | 2.5 |
| seo sonnet 5 medium | 84.1 | 4.0 | 7.5 | 14.2 | 0.8 | 16.9 | 15.9 | 12.9 | n/a | 1.9 |
| seo opus 5.5 fast-high | 86.8 | 3.7 | 6.4 | 13.5 | 0.9 | 28.6 | 7.0 | 14.6 | n/a | 1.9 |
| seo opus 5.5 high | 92.6 | 3.5 | 6.4 | 13.3 | 0.8 | 29.7 | 9.0 | 14.4 | n/a | 4.8 |
| seo sonnet 5 low | 93.2 | 4.8 | 8.6 | 15.2 | 0.9 | 20.6 | 17.2 | 13.0 | n/a | 2.1 |
| seo opus 5.5 fast-medium | 98.4 | 5.5 | 7.0 | 28.3 | 0.9 | 22.3 | 6.2 | 15.2 | n/a | 2.0 |
| seo sonnet 5 high | 112.7 | 4.5 | 8.6 | 17.0 | 0.9 | 30.9 | 23.8 | 12.9 | n/a | 2.1 |
| seo opus 5.5 fast-xhigh | 113.0 | 4.7 | 8.1 | 15.5 | 0.9 | 34.7 | 20.6 | 17.6 | n/a | 2.3 |
| seo opus 5.5 medium | 113.8 | 6.9 | 16.8 | 26.0 | 3.1 | 24.2 | 5.2 | 15.0 | n/a | 4.0 |
| seo sonnet 5 xhigh | 119.2 | 4.4 | 8.7 | 14.1 | 0.8 | 43.8 | 26.2 | 12.6 | n/a | 2.0 |
| seo opus 5.5 xhigh | 147.5 | 6.7 | 14.0 | 28.2 | 0.9 | 32.7 | 37.5 | 17.2 | n/a | 4.1 |
| seo sonnet 5 max | 178.3 | 6.9 | 12.6 | 25.4 | 0.8 | 55.5 | 65.8 | 9.7 | n/a | 4.2 |
| seo opus 5.5 max | 323.9 | 9.7 | 13.7 | 29.5 | 0.9 | 178.2 | 92.4 | 21.0 | n/a | 5.4 |
| seo opus 5.5 fast-max | 340.4 | 8.0 | 12.9 | 26.4 | 1.1 | 222.4 | 91.8 | 14.2 | n/a | 1.8 |
| seo baseline | 420.5 | 4.1 | 13.4 | 14.7 | 0.9 | 350.8 | 29.4 | 14.2 | n/a | 1.9 |
