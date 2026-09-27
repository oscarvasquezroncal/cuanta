# Investigation benchmarks

Two tasks in the `investigation` suite exercise the Next.js cart and checkout flow
and Python price calculations. They require an answer with configured keywords and
real, bounded `file:line` references. Every source edit rejects an investigation.
These checks establish citation validity and keyword coverage; they do not prove
that an explanation is semantically correct.

The existing `mini` and `full` suites retain their tasks and command acceptance.
For investigations, the baseline launches a read-only single context without
delegation or packs. `--shape`, `--pack` and `--depth` configure Cuanta runs; the
report records the options that actually ran. Disabling automatic packs leaves
index tools, learning and source protection available.

The opt-in fake engine produces source-derived answers and real telemetry event
shapes. Six CLI runs, two tasks across baseline, Cuanta and routed conditions,
passed answer acceptance with no source changes. Their observed read efficiency
was 1.0 and their anatomy contained API requests. These are deterministic plumbing
checks, not provider performance or cost measurements.

Numerical benchmark JSON includes anatomy and read efficiency and omits raw
answers. Phase totals remain observational heuristics. Read efficiency uses
unique successfully read files: cited files read divided by files read for an
investigation. Unknown observations remain unknown.

## Provider trials

Four paired sandbox trials use Sonnet 5, normal depth, a single context and the
index enabled, toggling automatic packs for each task. Each trial has a $0.40
cap. They use the trial driver; they are not paid baseline bench CLI runs.
The first launch reached a provider session limit, reported $0.00 and was
rejected. The provider reported a 10:10 America/Lima reset on September 27.
After waiting for that reset, the single retry passed. All four measured
conditions passed AnswerCheck, original-source hashes and sandbox safety checks.
They made no source, dependency or state edits, and their copies were removed.

| Task | Pack | Run ID | Reported USD | Driver seconds | API requests | Observed reads | Read efficiency |
|---|---|---|---:|---:|---:|---:|---:|
| Next checkout | off | 01M3HPNK38EHNWXP3E1RKQTTES | 0.1144122 | 34.41 | 5 | 4 | 1.0 |
| Next checkout | on | 01M3HPR5S0QE7F1SY2DZ1NXNY9 | 0.0772380 | 18.25 | 1 | 0 | unknown |
| Python pricing | off | 01M3HPTVDG4FPPHBTBEN4B6S3Q | 0.0588814 | 21.45 | 4 | 1 | 1.0 |
| Python pricing | on | 01M3HPX1GWCP5H6G2EGK1AE4GR | 0.0274654 | 16.89 | 2 | 0 | unknown |

The four accepted attempts cost $0.2779970, or $0.06949925 per accepted
investigation. Pack-on answers used their supplied context without observed raw
or index reads; an empty read denominator remains unknown. Pack-off Next had
five index calls and four raw reads, and Python had two index calls and one raw
read. Their observed index hit rates were 0.5556 and 0.6667.

| Task / pack | Start USD | Exploration USD | Writing USD | Handoff USD |
|---|---:|---:|---:|---:|
| Next / off | 0.0580420 | 0.0563702 | 0 | 0 |
| Next / on | 0.0772380 | 0 | 0 | 0 |
| Python / off | 0.0305016 | 0.0128070 | 0.0155728 | 0 |
| Python / on | 0.0137828 | 0 | 0.0136826 | 0 |

Observed phases conserve each attempt's reported cost. The start phase includes
the first response even when it delivers an answer; phase names do not establish
causal attribution. The pack-on attempts were cheaper in this sample, by
$0.0371742 for Next and $0.0314160 for Python. Order and cache state were not
controlled: the Next starts were cold, while Python reused cache. The result is
descriptive rather than evidence of a general causal saving.

One pair per task cannot establish general savings. Reported Claude costs are
usage estimates under the CLI subscription, not a separate invoice.
