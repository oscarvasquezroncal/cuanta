# The implementer on hard tasks, 2026-10-01

Q4 ran the three best variants of the [implementer screen](2026-09-30-implementer-screen.md) on
two hard tasks: the GSAP fix, whose earlier Claude results were mixed and were judged on frozen
regexes, and the WhatsApp ordering feature, which the F1 Claude team failed. Every trial used the fast
implementation profile on a private copy of a production Next.js landing page: one native Claude
session, one pinned model for every request, verification and same-session repair by cuanta.
Trials ran one at a time in sandbox copies. Accepted WhatsApp steps were applied only to each
variant's own copy of the private copy; nothing was applied to the real landing page.

- **Variants:** Sonnet 5 medium, Opus 5.5 low and Opus 5.5 high (the screen's ranks 1, 3 and the
  next distinct effective setup; fast output never took effect, so Opus fast-low equalled Opus
  low).
- **Caps:** $30.00 for Q4. Each trial reserved its native budget plus one in-flight request
  (Sonnet $3.60, Opus $6.20 per trial). Costs are what the CLI reported (subscription usage
  estimates).
- **Acceptance commands:** `npx tsc --noEmit`, `npm run lint` and `npm run build` in a fresh
  copy of each trial's after-images, plus the frozen file checks.

Total spend: **$5.9033** of $30.00 over 10 trials. No row was dropped for the cap, and no trial
hit a usage limit.

## The GSAP fix

The frozen T1c request asks that content revealed by GSAP animations never stays hidden when an
animation fails.

**Outcome on behaviour.** A trial is accepted when typecheck, lint and build pass and the V4
GSAP probe passes for Reveal and RevealText. The probe transpiles the candidate's components and
its real `src/lib/gsap.ts`, runs 24 scenarios (5 healthy controls and 19 injected GSAP failures:
outer context, inner setup, animation, reduced-motion set, with and without stagger) and checks
that every required element ends visible and opaque. AnimatedUnderline and DrawnSprig are decorative SVG strokes; they are
reported, not required.

**The frozen regex checks** from T1 still ran. They look for one exact form: a `catch` without a
variable that sets `style.visibility` in `Reveal.tsx` and `RevealText.tsx`. The request names
neither the files nor the form of the `catch`, so the regex verdict is reported next to the
behaviour verdict for comparison only.

| Variant | Wall | Cost | Repairs | tsc, lint, build | Probe: Reveal, RevealText | Other components | Frozen regexes | Outcome |
|---|---:|---:|---:|---|---|---|---|---|
| Sonnet 5 medium | 182.2 s | $0.4446 | 0 | pass | fail: animation fault leaves Reveal (plain, stagger) and RevealText at opacity 0 (Reveal 7/9, RevealText 4/5) | AnimatedUnderline 5/5, DrawnSprig 5/5 | pass | rejected |
| Opus 5.5 low | 111.8 s | $0.3080 | 0 | pass | fail: Reveal 2/9, RevealText 1/5 (only the healthy controls pass) | AnimatedUnderline 1/5, DrawnSprig 1/5 | fail | rejected |
| Opus 5.5 high | 180.2 s | $0.5585 | 0 | pass | **pass** (Reveal 9/9, RevealText 5/5) | AnimatedUnderline 1/5, DrawnSprig 1/5 | fail | **accepted** |

- **Sonnet 5 medium** wrapped the animations in the exact `catch {}` form the regexes expect, and
  its fallback restores `visibility`, but an animation that fails after `autoAlpha` set opacity
  to zero leaves the content transparent. The regexes would have accepted it.
- **Opus 5.5 low** added a fallback class to the root element and CSS that reveals everything
  under it. The class is added only when plugin registration throws or GSAP has not booted four
  seconds after load; once GSAP is ready, a failure inside a component never adds it. Checked by
  hand, because the probe keeps the original hidden-selector model for added CSS.
- **Opus 5.5 high** added a shared `forceVisible` helper that removes the inline opacity,
  transform and filter GSAP wrote and sets `visibility`, called from the failure paths of Reveal
  and RevealText (the only components it changed), plus a CSS failsafe for roots GSAP never
  armed. Content ends visible in every Reveal and
  RevealText scenario. The regexes reject it because the fallback lives in the shared helper.
- Both Opus diffs rewrote the line endings of `globals.css` (319 CRLF and 908 LF lines before,
  all CRLF after), so the stored patch shows the whole file as changed.

**History, judged on behaviour with no new spend.** The two rejected R3 Claude runs (classic and
V5) recover from setup failures but leave Reveal and RevealText at opacity 0 when the animation
fails (Reveal 7/9, RevealText 4/5), the same failure as Sonnet 5 medium here; their regex
rejections agree with behaviour. The regexes and the behaviour disagreed earlier, in E1: the
Sonnet fix passed the regexes and failed on opacity, and the Opus fix failed the regexes and
passed the probe 24/24. In Q4 they disagree in both directions again: the regexes accept the
Sonnet fix that fails on behaviour and reject the Opus 5.5 high fix that passes.

## The WhatsApp feature, in three steps

The frozen F1 request was split by its numbered sections into three ordered requests, each with
the F1 commands and the checks that apply to it, frozen before the first run:

1. the typed catalog and the pure link builder `buildWhatsAppOrderLink` (sections 1 and 7);
2. the multi-product order with persistence (section 3);
3. the per-product buttons, the floating button, the Product JSON-LD and the analytics event
   (sections 2, 4, 5 and 6), with the F1 build-output checks.

Each variant worked in its own copy of the private copy. After each step, the F1 link-builder
harness (eight cases: normalisation, encoding, multiple items, totals, empty orders, missing
phones) ran on the after-images, and a reviewer read the diff against the step's request. An
accepted step was applied to that variant's copy with `cuanta runs apply` before its next step.
A step is accepted when the commands and checks pass, the harness passes, every numbered
requirement is met and no runtime regression is introduced in existing behaviour.

| Variant | Step 1 | Step 2 | Step 3 | Total | Outcome |
|---|---|---|---|---:|---|
| Sonnet 5 medium | 206.2 s, $0.2701, rejected | not run | not run | 206.2 s, $0.2701 | stopped at step 1 |
| Opus 5.5 low | 131.2 s, $0.3161, accepted | 236.3 s, $0.7447, accepted | 160.8 s, $0.4255, accepted | **528.3 s, $1.4863** | **every step accepted** |
| Opus 5.5 high | 239.7 s, $0.4998, accepted | 355.8 s, $1.2253, accepted | 247.5 s, $1.1106, accepted | 842.9 s, $2.8357 | every step accepted |

Every WhatsApp trial passed its commands and checks with no repair round, and every after-image
passed the harness 8/8.

- **Sonnet 5 medium, step 1** built a correct link builder, but it also rewired the product
  buttons, which step 1 did not ask for, and read `window.location.href` while rendering. The
  server and the browser then render different links, a hydration mismatch on every product
  card. Rejected, so its later steps were not run.
- **Opus 5.5 low** kept a server-safe link (`#productos`) and rewrote it on click; its cart is a
  client drawer over the existing Zustand store, persisted through a mirror store with
  `skipHydration` and rehydrated after mount; step 3 tracks order starts on the product buttons,
  the cart and the floating button.
- **Opus 5.5 high** used a static catalog URL, moved focus into the cart drawer, kept persistence
  in one effect with a versioned and guarded storage format, and tracked every `wa.me` link
  through one delegated listener. Its floating button follows the section or product card in the
  middle of the viewport and hides over the footer.
- All three variants rewired the product buttons in step 1. Opus edits again normalised mixed
  line endings (`globals.css`, `Header.tsx`), which inflates stored patches.
- The request's analytics item ("clic en cualquier «Pedir por WhatsApp» o en enviar el pedido")
  was read as the order buttons the feature adds, as in the F1 review; Opus 5.5 low does not
  track the generic header, hero and footer links, and Opus 5.5 high does.

## Targets

| Target | Result |
|---|---|
| GSAP fix accepted on behaviour in 240 s or less | **met**: Opus 5.5 high, 180.2 s |
| Every WhatsApp step accepted, each in 5 minutes or less | **met** by Opus 5.5 low (131.2 s, 236.3 s, 160.8 s); missed by Opus 5.5 high (step 2 took 355.8 s) and by Sonnet 5 medium (step 1 rejected) |
| No run ending red while the repair loop had rounds left | **met**: all 10 trials ended green on cuanta's checks (typecheck, lint, build) with no repair round; the frozen GSAP regexes, which failed for both Opus runs, are acceptance checks outside the repair loop |

## Time history

The columns use different timing definitions, so they are not ratios of one another. T1 keeps its
archived `duration_s`, whose boundary is not independently proven; E1 and the V4 replays list
attempt seconds from the grouped ledger and, where captured, trial-driver seconds; R3, F1 and Q4
are the whole command as the user waits for it.

| Task | T1 | E1 and V4 | R3 classic / V5 | Q4 fast |
|---|---|---|---|---|
| GSAP fix | 140 s archived; accepted on review with a known limit (staggered children stay hidden); regexes passed; not probed | E1 Sonnet 285 s attempt / 307 s driver: regexes 2/2, behaviour failed (opacity); E1 Opus 293 s / 311 s: regexes 0/2, probe 24/24; both rejected on the regexes. V4:T1 replay 151 s attempt / 178.8 s driver: regexes 2/2, scoped probe 14/14, accepted | 612 s / 388 s: regexes failed and behaviour failed (Reveal 7/9, RevealText 4/5), both rejected | Opus 5.5 high 180.2 s: regexes failed, behaviour passed, accepted. Sonnet 5 medium 182.2 s: regexes passed, behaviour failed. Opus 5.5 low 111.8 s: both failed |

| Task | Earlier | Q4 fast |
|---|---|---|
| WhatsApp feature | F1 (deep, V5 team, one request): 1,026 s, rejected (lint) | Opus 5.5 low 528.3 s over three accepted steps; Opus 5.5 high 842.9 s over three accepted steps; Sonnet 5 medium stopped after a rejected first step (206.2 s) |

## Where the time went

Seconds per phase. Wall is the whole `cuanta mandate --sandbox` command as the user waits for it;
phases can overlap, and request and tool times are sums of their intervals.

| Trial | Wall | Index | Forecast | Copy | Start | Requests | Tools | Verify | After-image | Requests (n) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| GSAP Sonnet 5 medium | 182.2 | 4.7 | 10.2 | 16.9 | 0.9 | 104.5 | 15.3 | 12.9 | 2.8 | 16 |
| GSAP Opus 5.5 low | 111.8 | 4.6 | 9.9 | 14.3 | 0.9 | 48.5 | 3.6 | 15.2 | 2.1 | 6 |
| GSAP Opus 5.5 high | 180.2 | 4.6 | 10.0 | 15.1 | 1.0 | 97.5 | 42.1 | 15.9 | 2.4 | 10 |
| WhatsApp 1 Sonnet 5 medium | 206.2 | 2.6 | 4.7 | 21.7 | 1.6 | 70.9 | 6.7 | 76.5 | 5.5 | 14 |
| WhatsApp 1 Opus 5.5 low | 131.2 | 2.3 | 4.5 | 22.7 | 0.9 | 38.0 | 0.5 | 43.5 | 7.8 | 11 |
| WhatsApp 2 Opus 5.5 low | 236.3 | 2.5 | 4.7 | 30.2 | 1.0 | 113.5 | 4.9 | 73.2 | 2.6 | 19 |
| WhatsApp 3 Opus 5.5 low | 160.8 | 2.6 | 4.8 | 34.9 | 0.8 | 70.5 | 14.4 | 23.9 | 1.8 | 9 |
| WhatsApp 1 Opus 5.5 high | 239.7 | 4.2 | 4.8 | 50.5 | 1.1 | 79.2 | 1.4 | 75.8 | 3.8 | 21 |
| WhatsApp 2 Opus 5.5 high | 355.8 | 2.5 | 4.6 | 45.6 | 0.9 | 206.2 | 7.8 | 79.3 | 4.5 | 28 |
| WhatsApp 3 Opus 5.5 high | 247.5 | 2.0 | 4.2 | 16.1 | 0.9 | 192.5 | 1.7 | 21.8 | 1.9 | 17 |

- **Model requests** are the largest phase in 8 of 10 trials (38–206 s); in the first WhatsApp
  step of Sonnet 5 medium and of Opus 5.5 low, verification took longer. High effort took 1.8–2.7
  times the request time of low effort on the same WhatsApp step.
- **Verification** took 43–79 s in the WhatsApp step 1 and 2 trials, where cuanta checks every
  ordered step (three to six steps per trial), against 13–24 s for one final check in the GSAP and
  step 3 trials. There, the sandbox copy or the tools often took longer (GSAP Opus 5.5 high: tools
  42.1 s).
- **The sandbox copy** took 14–51 s and varied between runs on the same copy (16–51 s for
  Opus 5.5 high).
- No repair round ran: all 10 writers finished green on the first pass.

## Purity

Every recorded request of the 10 trials used the chosen model (Sonnet 5 or Opus 5.5), with no
title or helper request from another model. No subagent ran, because fast runs deny delegation
tools; the subagent pin stays source evidence only (CC-27).

## Limits

- One trial per cell. Q3 showed the same setup varying by a factor of two between runs.
- The WhatsApp steps were reviewed by reading diffs and by the link-builder harness; no browser
  test ran, so hydration, focus and layout are judged from code.
- The GSAP probe uses a minimal DOM and a fault-injecting GSAP; it does not cover a real
  browser, ScrollTrigger timing or visual layout.
- Both measuring tools changed during Q4, before the verdicts they produced. The GSAP probe now
  evaluates a candidate's insert-only CSS with the original hidden-selector model (a
  conservative lower bound; failing rows were checked by hand) and was revalidated on the
  untouched copy: 5/5 healthy and 19/19 faults reproduced. The F1 link-builder harness gained a
  `process.env` shim after Opus 5.5 high's first step failed to load in it (its constants read
  `process.env` at module load, which Next.js provides); its eight cases are unchanged, the
  untouched copy still fails and the reference implementation still passes 8/8.
- Costs are CLI estimates under a subscription, not invoices.
