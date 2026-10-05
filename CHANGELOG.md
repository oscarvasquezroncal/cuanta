# Changelog

All notable changes to cuanta are documented here.
This project follows [Semantic Versioning](https://semver.org/). The vendored
claude-agent-forge keeps its own changelog in `src/cuanta/assets/forge/CHANGELOG.md`.

## [Unreleased]

### Added
- **Readable runs in both languages.** Console runs follow `ui.language` and global `--lang`.
  A six-row launch card and a short result lead with scope, effective limits, stop reason,
  partial accounting and executed roles. Engine guarantees, context-pack details and route
  audits are available with `--verbose` and Spectrum. A live line shows streamed tokens,
  elapsed time, role and the last tool/file at most twice per second (plain: once per minute).
  The app shares the live line, folds technical Result rows under Details/Detalles, and
  shows the same three-line failures. Unexpected errors name a daily, redacted traceback log;
  listener tracebacks are copied into that daily log too.
- **A mandate from a file.** `cuanta mandate --from FILE` (`-f`; also `cuanta queue add -f`) reads
  the whole mandate from a file. The file is split into fields only when, outside code fences, it
  has a `TYPE:` line naming a known type, a `WHAT:`/`QUÉ:` label, a `WHY:`/`EVIDENCE:`/`POR QUÉ:`/
  `EVIDENCIA:` label or a REQUEST block, or it opens with a `TYPE:` line (any value; right after a
  title, written `TYPE:`/`TIPO:` in capitals); then every label counts (`TYPE:`, `WHAT:`, `WHY:`,
  `EVIDENCE:`, `WHERE:`, `CONSTRAINTS:`, `TEST:`/`TESTS:`, `OUT OF SCOPE:`, or `TIPO:`, `QUÉ:`,
  `POR QUÉ:`, `EVIDENCIA:`/`EVIDENCIAS:`, `DÓNDE:`, `RESTRICCIÓN:`/`RESTRICCIONES:`,
  `PRUEBA:`/`PRUEBAS:`, `FUERA DE ALCANCE:`/`FUERA DEL ALCANCE:`, in any case, accents optional),
  a value other than WHY ends at a blank line unless the next line is indented, and the text around
  the labels joins the evidence. Otherwise the first line, or a heading that stands alone, is the
  what and the rest is the evidence, word for word, label-like lines included. A copied mandate
  template keeps only its REQUEST block, also when a log inside it is fenced with a bare ``` and when
  it is copied without its fences (its execution contract is dropped then); notes after its closing
  fence, and other text above an unfenced REQUEST block, join the evidence. A file that states no type runs as a feature, with one Note
  that says so, and under the auto profile on the balanced team, never the fast one; an unknown
  `TYPE:` is refused with a hint to use `-t/--type`, also when it is the file's only label. `--type`, `--where`, `--constraints`, `--tests`
  and `--out-of-scope` replace the file's values; `--what` replaces its what, which moves to the
  top of the evidence; `--why` is added after the file's evidence under "Added on the command
  line:". One Note names what the command line changed and another the fields taken from the
  file's labels. `--from` with `--evidence` is refused. A file that is not UTF-8 is refused with
  "save it as UTF-8" (a byte-order mark is honored), `~` is expanded, `-t` takes the same words as
  a file's `TYPE:`, a mandate or evidence file inside the project is never ranked or packed as a
  target of its own run (it stays in the change plan when the request names or anchors it), `queue
  add` stores absolute paths, and `queue run` prints the Notes of each queued file before it
  launches. `cuanta mandate -f
  mandato.md` is a complete command. On Windows cmd a `--what` value is limited by the shell to
  8,191 characters: put a long request in a file.
- **One argument is enough.** `cuanta run FILE` runs a file as the whole mandate; `cuanta feat`,
  `cuanta fix` and `cuanta audit` take the request as text and set the type. All four are aliases
  of `cuanta mandate` and take every option, `--from` included; the command's type wins over a
  file's `TYPE:` unless `--type` is given, with a Note when they differ; when the text alone is the
  path of an existing file, a Note says that `cuanta fix -f <file>` runs it as the mandate. Words that start with `-`
  are read as options, so quote the request and put `--` before text that starts with `-`.
  `cuanta help` and `cuanta ayuda` print one screen in the UI language (`--lang`, `ui.language` or
  `CUANTA_LANG`, then the system locale) with the six things people do.
- **Short options:** `-f` (`--from`), `-t` (`--type`), `-m` (`--model`), `-v` (`--variant`), `-p`
  (`--profile`) and `-s` (`--sandbox`) on `cuanta mandate`, its aliases and `queue add`.
  `cuanta runs` alone lists the last ten runs, and `cuanta runs show` without an id shows the last
  run you launched (a team or a loop, never one of its roles or fixes). `cuanta mandate --help`
  groups its options under What to do, How to run it, Limits and Advanced, one line each; the
  common path ends within 30 lines at 80 columns, with `--shape`, `--session`, `--keep` and
  `--cross-budget-usd` under Advanced.
- `cuanta init --template` writes only a missing `docs/MANDATE_TEMPLATE.md` from the vendored Forge
  copy, with no model and no detection, and exits 1 when the template is unusable or cannot be
  written. `cuanta doctor` gains a `template` row: a missing template warns with that fix, and one
  that exists but cannot be used (no fenced REQUEST block, or not a plain file inside the project)
  is named with what to do. Home and the header say `installed · template missing` or `installed ·
  template unusable`, and Run fix runs `cuanta init --template` in place whenever Home's next step
  or the Health view offers it.
- In the app, the Tell step gets a path box with **Load file** / **Cargar archivo**, takes text of
  any length, and loads a file whose path is pasted alone (`.md`, `.markdown`, `.txt`); with an
  empty story, **Next** loads the path in the box, a `/` or `~` sentence that names no file stays
  the story, `\ ` escapes from macOS drag and drop are undone, and a file that is not UTF-8 (a
  byte-order mark is honored) is refused in the file note; a `~` path whose home cannot be resolved counts as missing, and a
  file loaded or attached from inside the project is never ranked or packed as a target of its
  own run. A loaded file, or a paste that starts with a heading, states a
  `WHAT:`/`QUÉ:` value or has more than 1,200 characters, is a whole mandate: it is split like
  `--from`, keeps the rest as evidence in its own box, and needs only the type and the what; the
  app proposes the type from the title for you to confirm. The step's help says that the "more
  than 5 KiB" dialog is Windows Terminal's and that "Paste anyway" / "Pegar de todas formas" is
  safe. The Team step offers the main session model (which also runs the Orchestrator card), the
  Claude variant (**Effort from depth** sends no variant unless `runs.variant` is set, then the
  depth's effort; the fast-output variants need an explicit Opus pick, or an explicit Fast on a
  bug fix or a feature) and **Pure** for the balanced team too; an **Auto** chip returns to the automatic profile,
  and an explicit Fast returns to Auto when the engine leaves Claude or the kind becomes an
  investigation.

- Preparing a mandate shows every step that takes more than two seconds: `index · 854 files · 52 s`
  (the first index update, wherever it runs), `plan` and `forecast`. The console shows a step live
  once it passes two seconds; `--plain` prints one line when it ends (`+ index 854 files · 52 s`);
  `--json` streams `StepStarted`/`StepFinished` records on stderr, and `StepFinished` now carries
  `seconds`. A dry run is never silent. `cuanta index` and per-role dry runs show the index step,
  and the app's run screen lists the steps in its feed (`✓ índice · 854 archivos · 52 s`).
- `cuanta doctor`, Health and Home warn when `.cuanta` is over 500 MB and name its largest file with
  its size. It offers `cuanta index --rebuild` only when reclaimable index pages or old index
  backups would bring the total under 500 MB or free at least a quarter of it.

### Changed
- **`-V` is the global short for `--verbose` (surprising).** `-v` now means `--variant` on
  `cuanta mandate`, `pounce`, `run`, `feat`, `fix`, `audit` and `queue add`; `cuanta -v doctor` is
  refused with "No such option: -v". `-y` and `-V` are written on their own after a subcommand (a
  cluster such as `-sy` is refused).
- **Only the type and the what are required on the console (surprising).** `--why`, `--tests`,
  `--out-of-scope`, `--constraints` and `--where` are optional everywhere and never prompted; the
  prompt says "none stated" for them and WHERE says "unknown", in the native prompt and in the
  prompts of a team of separate launches (a bug with evidence keeps its regression-fixture
  default, an investigation keeps "a written report"). The app still asks "What must not change?"
  for short stories.
- No field is cut any more: WHY is no longer clipped at 8,000 characters into an evidence capsule,
  and the whole request travels on stdin (OpenCode: a prompt file). The 30,000-character guard
  covers only the command line; its hint names the system prompt and the protected paths.
- **Limits are opt-in (surprising).** A run has no spend cap, no turn limit and no wall-time limit
  unless you set one: `--max-budget-usd`, `--max-turns` and the new `--max-wall` (minutes) on
  `cuanta mandate`, `queue add` and `loop` (each fix); `[limits] budget_usd`, `max_turns` and
  `wall_min` in `.cuanta/config.toml` for every run; `[runs] limits = "depth"` to restore the depth
  caps and turn rails; and the app's Límites/Limits switch in the Team step, off by default.
  `--depth` now chooses model tiers, effort and the read budget only. Teams of separate launches
  lose their implicit $1.00 and depth caps. `--max-turns 0` now means no turn limit for that run.
  The launch card says `no limits` or lists the active ones, and hides the spend and turn
  guarantee rows that do not apply. Bench runs and trial replays set their caps explicitly.
  With `[limits]` values and `[runs] limits = "off"`, the app's switch starts on with exactly those
  values (every other field reads 0); an empty field takes the depth default only in depth mode
  or when you turn the switch on with nothing configured. The card's limits line and the dry-run
  `limits` key list the limits the launch applies (a turn limit is dropped on Codex and OpenCode),
  while the guarantee rows still say "Turn limit: not available" when one was asked for.
- A role pin set in `[routing] models.*` keeps the auto profile on the balanced team, where pins are
  honored; an explicit `--profile fast` with a pin naming another model is refused before launch.
- `cuanta mandate --docs auto|on|off` and `cuanta queue add --docs` choose the docs role for one run,
  over `runs.docs`; contradictions (`--classic` with `--docs off|auto`, `--docs off` with a docs pin,
  `--docs on` with `--simple`, an investigation or the fast profile) are refused before launch, and
  `--docs on` keeps the auto profile on the balanced team.
- The background listener writes to `.cuanta/logs/listener.log` (it was `.cuanta/listener.log`).
  Stored raw telemetry re-encodes JSON found inside string values compactly; email redaction covers
  non-ASCII local parts, and scalar values under secret-named keys are redacted. A body that is not
  OTLP gets HTTP 400. The background listener's own console output goes to
  `.cuanta/logs/listener.out.log`, rewritten at each start, so `listener.log` rotates on Windows too.

- **init keeps a Forge that is already initialized (surprising).** On a project whose
  `.claude/forge-state.json` says Forge is initialized, `cuanta init` launches no model: it keeps
  the forge-state, installs only missing vendored Forge files, writes a missing
  `docs/MANDATE_TEMPLATE.md` when the four agents exist, and verifies, in seconds instead of about
  40 minutes. `--refresh-forge` runs Forge again as before. The graph update runs in the background
  after init returns (in the foreground when Forge runs in the same init), and `--skip-graph` skips
  it. Every stage prints its time and init prints a total; telemetry that is already wired is not
  asked again.
- **init never writes beside your files (surprising).** Forge's version of a file you already have
  goes to `.cuanta/forge-suggested/` (`.claude` written as `claude`, so Claude Code never loads it)
  with a one-line +/- summary and a Note on how to adopt it; `.new.md` files an older init left in
  `.claude/` move there on the next init (identical copies are deleted). Missing gateway
  instructions in an agent or the mandate template, and a rulebook over its ceiling, are warnings
  with their fix: init ends `init complete` with a warning count and exits 0, instead of `init
  finished with problems`.

### Fixed
- A run is no longer killed after 15 minutes: `runs.repair_timeout_s` bounds the time spent in
  repair rounds only, counted from the first repair turn, and stops further repairs instead of
  halting the engine. This applies to the native team, fast runs and the per-role runner.
- A finished run never shows a step as running: steps cut off by a stop or an engine exit read
  `stopped`, and the result names the stop in one line with its number ("stopped by the time
  limit of 900 s", "stopped at the spend cap of $5.00", "the agent finished"), in the console,
  `cuanta runs show` and the app.
- A run that ends before the engine's result shows its cost and turns from what was received
  (telemetry requests priced with the catalog, or the stream's usage), marked `partial`, instead of
  `cost n/a` and `turns 0`. Partial costs count as lower bounds in totals and never calibrate
  forecasts. The ledger moves to schema 14 (`runs.partial`, `runs.max_wall_s`).
- The app's run screen settles its cards when a run is stopped or fails without a result.
- The telemetry listener never prints to the console. A record it cannot read is dropped and
  counted, the run gets one Note ("N telemetry records could not be read; details in
  .cuanta/logs/listener.log"), and tracebacks go to that file, echoed only with `--verbose`.
  Redaction now works on values before JSON encoding, so stored raw telemetry is always valid
  JSON (a key assignment such as `API_KEY=...` inside a tool input, or an email after a newline,
  used to corrupt it and drop the whole batch).
- The context pack and the change plan read the whole request: `path:line` and `path:start-end`
  anchors from every field, the evidence included, come first in the pack with their cards and the
  anchored lines, the native scout gets the pack too, and the launch card names anchors that did not
  resolve (missing, ambiguous, past the end of the file, protected or left out by the budget). A long
  request takes at most a quarter of the pack budget. The change plan no longer guesses new files
  from tokens such as `2.1` or `POST /consult`.
- Read-only words inside one numbered phase of a phased mandate no longer make a feature, fix or
  refactor read-only for the whole run; when a writing request still compiles read-only, the card
  says which words did it.
- The docs decision reads the evidence too and recognizes the `docs-updater` agent and docs paths
  (`docs/`, `CHANGELOG.*`, `README.*`); links are ignored. The launch card and the result name the
  rule, the term and the field that turned docs on or off.
- `--pure` plans pure: the route plan, the scout's tier, the agents file, the estimate, the forecast
  and the audit all use the forced model, with the reason `pure`; a premium or higher pure model keeps
  the pipeline without a scout unless `--shape scout` or a scout pin asks for one, and the live launch
  card now prints the shape line. Pure and fast runs no longer teach the tier learner.
- The forecast counts the request's own tokens (every field, the evidence included), and a mandate
  with several numbered phases or steps gets one line saying the forecast covers one change.
- A wall limit that fires after the final result, or while cuanta verifies it, no longer fails a
  finished run; a team's shared deadline also stops repair, resume and rotation launches (the team
  ends partial with the time-limit line), role runs record the team's limit, and a huge `--max-wall`
  no longer crashes the timer. A wall halt or a user stop during verification is no longer reported
  as "session closed", and a user stop always reads "stopped by the user" unless the run finished.
- Per-role results label partial costs and their total ("spent $1.80 (partial) of $5.00") instead
  of `n/a`; Codex and OpenCode runs cut before their turn ended are partial too; similar-run
  estimates skip partial runs; `cuanta runs show` of a team gives the team's stop.
- Out-of-scope files the index never holds (lock files, a backticked `.env`, `go.mod`, `*.tf`,
  `*.pem`) are guarded again. Relative anchors resolve to the deeper file they name. A prohibition
  scoped to one phase ("en esta fase") no longer guards the whole run, and a phase prohibition on a
  path the request also names for change is dropped with a note. Read-only words after the first
  numbered phase of any field no longer freeze a writing request. Absolute and third-party paths
  never become new-file targets, and ranges longer than the pack window are reported as packed in
  part.
- The docs rule is narrower in the evidence: docs words count only on headings, numbered phase or
  step lines and bold lines; the agent name and docs paths count anywhere except log lines; negated
  mentions ("no actualices la documentación") never turn docs on; a docs path starts with `docs/`
  or `doc/`.
- The listener stays quiet when the ledger fails to open or close; a value the ledger cannot store
  no longer drops its batch (it becomes a `telemetry_unreadable` marker, and `/health` reports
  `dropped`); non-finite numbers are stored as strings, so stored raw is strict JSON; `init` and
  `refresh` show the unreadable-telemetry Note too.
- In the app, per-role team cards hide the spend and turn guarantee rows that do not apply, typing a
  limit refreshes the forecast, the three limit fields wrap at 80 columns, validation skips the
  hidden turns field, the run summary labels estimated and partial costs, and the metrics panel says
  `cap none` instead of `$0.0000`. Stop and limits lines use the singular for one ("found 1 new
  error", "limits: 1 turn").
- A governed run whose governor turn was answered before its result settles at that result, so a
  wall limit during the final verification no longer fails it. A team repair or a Codex finish
  resume cut by the shared wall ends the team with the time-limit line and no verification after
  the deadline; a role stopped by the wall before its launch finishes as skipped. Trials and the
  bench treat a partial (lower-bound) cost like an unknown one and stop before the next paid run.
- A phase runs from its labelled line to the next heading of the same or a higher level, so a
  trailing section such as `## Reglas generales` is global again for read-only words and
  prohibitions. The docs negation counts only a negated change, creation or invocation verb that
  governs the docs term, or "sin"/"without" right before it ("has no README yet" keeps docs on).
  Home-relative and variable paths (`~/`, `$HOME/`, `%APPDATA%/`) never become new-file targets.
  An OTLP record that overflows while decoding becomes a counted marker and the rest of the batch
  lands.
- In the app, per-role model picks in the team cards survive limit, Limits and depth refreshes on
  the same engine, and the Team step follows configured role pins under `auto` (the balanced team).
- A missing `docs/MANDATE_TEMPLATE.md` no longer stops a mandate when the four Forge agents exist
  (`docs/MANDATE_TEMPLATE.md not found → run cuanta init first`): a native mandate (a Claude team,
  or OpenCode's single session) writes it when it launches, after the engine checks, from the vendored Forge copy (the project
  name from its manifest, else the folder name, and the first `*-senior` agent filled in), says so
  in one Note and continues. Its dry run and the app's preview show the planned `write:` line and
  write nothing, and a run in an isolated copy never writes it and uses the vendored copy. An
  existing file, a link or a path outside the project is never written; fast, simple and
  investigation runs and teams of separate launches never read it.
- `Ctrl+C` prints `interrupted`, and adds the run id and `see it: cuanta runs show <id>` only when
  the command recorded a run, also when it lands while the result prints. "nine lives: re-run to
  resume" appears only when a re-run resumes: `cuanta init` with finished stages and `cuanta queue
  run` with mandates still queued; a queued mandate that finished green leaves the queue even when
  `Ctrl+C` lands right after it. `Ctrl+C` or end of input at a prompt ends the same way instead of
  `unexpected Abort` with exit 2. The JSON error reports exit code 130. A run stopped by `Ctrl+C`
  during its start snapshot is recorded as interrupted instead of running, and one stopped there by
  another error is recorded as failed; a team of separate launches
  stopped by `Ctrl+C` records completion partial (failed when no role finished) and the stop
  reason `interrupted before the run finished`; an interrupted `cuanta init --dry-run` writes
  nothing; an interrupted or failed `cuanta loop` is settled with the cost of its finished fixes.
- The balanced team's main session model (`-m`, or the app's main session model) is the
  orchestrator's model in the plan, the team lines and the cards, and an orchestrator pin on
  another model is refused before launch; a forced main-session model never teaches the router an
  orchestrator tier. Fast-output variants (`-v fast-*`) run on a balanced team
  whose main session is Opus. A fast run's launch card no longer lists team role lines that say
  `--pure`.
- `cuanta runs` hints match `runs show`, and `runs show` inside a `--sandbox` copy shows the stored
  report of the original project.
- In the app, a long pasted mandate is no longer flattened into a read-only investigation (its
  title sets the type), a `#` line after the first line of a short story no longer becomes its what, going Back to
  tell another story no longer stays silently on the Tell step, and after **Fast** the model and
  variant boxes show the fast choices ("By kind of change") instead of the balanced ones.
- The index, detection and run snapshots leave out what git ignores: `.gitignore` at every level,
  `.git/info/exclude` and the global excludes file, read as files (cuanta still never runs git).
  Files git tracks stay. A folder holding `pyvenv.cfg` or `conda-meta` is a virtualenv whatever its
  name. `[detect] exclude` stays for the rest and means the same thing everywhere. Above 20,000
  files the index stops before reading anything and prints the five largest folders and the
  `[detect]` lines to paste. A real project with `.venv312`, `.venv_ci` and an Odoo checkout went
  from a silent 1.5-hour dry run to a 0.6 s stop, and to 802 files indexed in 7 s once excluded.
- `cuanta index --rebuild` reclaims space: it writes a new database file, carries agent notes and
  economy summaries over, swaps it in, deletes the old file and earlier index backups
  (`index.db.rebuild-*.bak`, `index.db.corrupt-*.bak`, `index.db.new-*`) and reports
  `reclaimed_bytes`; a rebuild used to keep the whole old index as a backup, so `.cuanta` never
  shrank. While another process holds the index (on Windows), the rebuild stops with a message and
  a hint and leaves the index as it was.
- A dry run on a large project no longer prints nothing until the prompt: the first index build
  ran silently inside the team-shape check, and `cuanta index --rebuild` was silent for 52 s.
- init no longer resets a completed `.claude/forge-state.json` to Phases 0 and 0.5, which sent the
  model through Phases 1 to 6 again and rewrote `CLAUDE.md`; `--skip-forge` no longer resets it
  either. When Forge does rewrite `CLAUDE.md` (`--refresh-forge`, a fresh project, `cuanta
  refresh`), init and refresh say so with the line counts and keep your previous version in
  `.cuanta/backups/CLAUDE.md.bak`.
- Each kept file is listed once instead of two or three times (console, JSON, app), a `.new.md`
  agent no longer replaces yours in the agents file of native Claude runs, and `cuanta doctor`
  reports the gateway lines (the fix in the message) and files an older init left beside yours.

## [0.5.0] - 2026-10-01

### Release pipeline
- Before the first npm release, exact setup-uv 10.2.0 references and Node 22/24 smokes replace
  unsupported references/runtime checks. Publish uses Node 24 with OIDC-capable npm, keeps the
  first-publish token fallback and skips an already registered version.
- The npm wrapper lets shared-console Ctrl+C/Ctrl+Break reach the child directly and waits for
  its exit code; piped POSIX input retains signal forwarding.

- One npm workflow validates committed version metadata, builds the tarball, and smokes it on
  Windows, Linux and macOS. Manual dispatch never publishes; tag pushes publish with provenance
  after all smokes pass. POSIX smoke also checks process-tree cleanup, timeout, Ctrl+C and the launcher.
- The full gate stays local and records the exact working tree without changing the staging
  index. Release tagging requires that green record, package version parity and existing Git guards.
- Portable sandbox types, scoped graph-spawn test doubles and drained Windows process jobs
  fix the defects found in the prior CI run.


- **npm distribution.** A zero-dependency Node launcher runs the Python CLI/TUI through a
  versioned, atomic cache, preferring uv and falling back to Python 3.12/3.13. Packages include
  the wheel and hash-pinned runtime requirements, with no lifecycle scripts. `--version`
  exits before project setup; local tarball smoke covers installation, help and npx.

### Faster preparation and verification
- `--verify auto|affected|full|off` on mandate, run, feat, fix and audit. Auto skips read-only
  investigations and unchanged runs, then selects affected tests. A full fallback uses xdist only
  when the project declares it. `[verify] timeout_s = 600` bounds post-run verification; zero
  disables the wall. Timeouts are inconclusive, with a command to run by hand, and preserve the
  completed report. The launch card, live line and short result expose verification and timings.
- Index writes share one transaction; unchanged file stamps reuse hashes and status counts avoid
  loading every record. JEV reuses identical requests, bounds each call and the run, and switches
  to local fallback after an unavailable request without changing consent or redaction.

Teams by provider, forecast envelopes, a live governor, scout and senior execution, a warm queue,
and a fast implementer that runs Claude features and bug fixes in one verified session by default.
Includes the unpublished 0.4.0 development changes; mixed-provider presets were retired
before this release preparation. Release artifacts and publication are separate steps.

### Added
- Default models per provider and tier in `model_tiers.toml`: Claude `haiku`, `sonnet`, `opus`;
  GPT `gpt-6-luna`, `gpt-6-sol`, `gpt-6-sol`. Frontier and older generations are never defaults and
  stay available by pinning.
- A GPT team (`mandate --engine codex` on a pipeline mandate) runs one launch per role with anchored
  handoffs, cuanta's checks between writing roles, shared budgets, salvage and guards. Its dry run
  lists each role's model.
- The app's Team step offers "Claude team" and "GPT team". Cards list each role's model with its price
  per million tokens, the model menu lists only the provider's models with prices, and the preview
  lists each role's model. `docs/TEAMS.md` explains how each team runs.
- A forecast before launch: the Team step and the dry run show the P50, the P90, the margin to the
  cap and the expected cache warmth, with the verdict and the first suggestion when the forecast is
  tight or infeasible. `--json` adds an `envelope` object with the buckets, each role's forecast and
  stop rules. `docs/TEAMS.md` explains the forecast.
- Every launched mandate stores its forecast before it starts; its actual is the run's recorded cost.
  `cuanta instinct calibration` and the app's Instinct view show the P50 error, the P90 coverage
  and the P90 error per provider and task type. Unknown costs stay n/a. The ledger moves to schema 13
  (a `forecasts` table); the migration runs on first open and is idempotent.
- `instinct.envelope` (off by default) asks Jev, with consent, to adjust the forecast from a
  numbers-only summary. The call is priced first and capped, and its answer is weighted by its track
  record.
- A governor for team roles. It reads each role's live stream and projects its spend. A Claude role
  launches with `--input-format stream-json` when the installed help lists it, and gets one turn,
  "termina ahora: aplica lo que está completo, escribe el handoff y lista lo que falta", at 85% of
  its share, two requests before its cap, or when, after three requests, the projection puts the
  cap at most four requests away and before the plan is done. Without the flag the launch is
  unchanged and the cap and salvage stop the role. A Claude role of a team of separate launches
  restarts once in a fresh session with a checkpoint handoff when that costs less than continuing.
  A native Claude team is governed as one session against the mandate cap; the main agent reads the
  finish turn when the running subagent returns.
- A Codex role is stopped when its estimated spend (items, elapsed time, and the model's cost per
  second in earlier runs) reaches 85% of its share. cuanta then resumes the thread with
  `codex exec resume` for a short finish turn that lists the changed files, when the installed
  resume help allows it; otherwise the role is salvaged. The stopped launch's cost is recorded as an
  estimate, never zero.
- Result's Consumption tab shows a governor panel ("Gobernador" in Spanish) with each reaction, its
  time, spend and limit, the estimated saving, the blocked calls with the tokens avoided, and which
  roles had the read discipline enforced or best effort. `cuanta runs show --json` and
  `cuanta mandate --json` add a `governor` object; team steps add `rotated`, `stopped`, `resumed` and
  `read_discipline`. Below 30 rows the Result screen scrolls so its tabs keep room.
- `runs.read_max_lines` (400) sets the line count above which a `Read` without a range is blocked.
- The scout and senior shape for features and fixes. A read-only scout on the economy model
  (`haiku` or `gpt-6-luna`, the new `scout` role, pinnable with `--role-model scout=...`) explores
  with the index tools and the read discipline and returns an evidence pack: `file:line` facts, a few
  snippets, risks, test links and the confirmed edit set. cuanta checks the pack against the working
  copy, takes each snippet's text from the working copy, trims it to about 6,000 tokens (snippets first, then facts beyond the first twelve, never the
  edit set), stores it as a capsule and shows it in Result. The senior works from the pack and the
  edit set only, with a read budget of at least its edit set plus two; its reads outside them count
  as exploration leak for the governor, and its edits outside the edit set are flagged in Result,
  named in its handoff or not. The tester gets the diff
  and runs `cuanta test --affected`. `docs/TEAMS.md` explains the shape.
- The shape is chosen before launch: the scout shape when the pipeline forecast's exploration is
  above `runs.scout_threshold` (35%) of its tokens, the pipeline otherwise. `--shape scout` or
  `--shape pipeline` forces it on features, fixes and refactors, and a pin decides it too: an
  `analyst` pin keeps the pipeline, a `scout` pin runs the scout. `--shape scout` is refused where
  no scout can run: investigations, `--simple`, opencode and `--route off`. The app's run keeps the
  shape its Team step showed.
- `runs.scout_mode`: `native` (default) runs the Claude team's scout as a subagent in its one session,
  with the pack captured from the session for Result; `launch` runs it as its own read-only launch
  followed by one launch per role. The GPT team and `--cross-engine` always launch the scout. Each
  run records the mode, and a native run records whether the main agent called the scout.
- `runs.docs` (`auto` by default, `on`, `off`) decides when the docs role runs; see Changed.
- A warm queue. `cuanta queue add` takes the options of `cuanta mandate` and checks them when it
  queues; `cuanta queue list` shows the run order; `cuanta queue run` runs the queued mandates back to
  back, so each one after the first starts on a warm prefix; `cuanta queue clear [ID...]` removes
  them. The run order keeps the same engine and model together and the queue order otherwise. Each
  mandate keeps its own cap, depth and `--sandbox`. `queue run` shows the order and asks first
  unless `--yes`, stops at the first failed mandate unless `--keep-going`, and keeps the failed and
  unrun mandates queued. The CLI and the app's Home show "warm prefix until HH:MM" ("prefijo
  caliente hasta HH:MM") from the last Claude request and the saved `cache.ttl_s`, or unknown when no
  TTL is saved for the auth mode. The queue lives in `.cuanta/queue.json`, which keeps each
  mandate's options and request text until it finishes green or is cleared; `--sandbox` mandates
  run as their own group. A run skips a mandate that left the queue after it was listed, and each
  write holds a short lock, so a `queue add` from another shell is never lost. Home shows "Mandate
  queue unreadable" when the file cannot be read. `docs/TEAMS.md` explains it.
- Metrics per run. Result's Consumption tab shows a metrics panel ("Métricas" in Spanish): the
  forecast's P50 and P90 against the actual cost, the share of the cap used and P90 minus actual,
  forecast and observed tokens by bucket (start, exploration, writing, verification, handoff), the
  cost per accepted change, the tokens per changed line of an accepted isolated-copy patch, the
  blocked reads with the tokens they avoided, graceful finishes and rotations with their estimated
  savings, the warm-cache share of first requests and of all requests, the scout's pack size and
  the senior's input tokens. `cuanta costs --metrics` adds the same per run, and `--json` adds a
  `metrics` list. Unknown values stay n/a, never zero. A governed run records its governor even when
  it never reacted, and a native session records whether it ran with the read-discipline hooks, so
  their finishes and blocked reads show 0 instead of n/a. `docs/TEAMS.md` explains each metric.
- Spectrum has a Trend tab ("Tendencia"): the cost per accepted change over the last 20 runs of each
  provider and task type, with a line from the oldest run to the newest. `cuanta spectrum --trend`
  prints it (`--last N` sets the runs), and `--json` adds a `trend` object.
- `cuanta bench run --compare read-discipline|scout|warm-queue|finish` runs one V5 comparison as
  arms of the same task and model: read discipline on and off; the scout and senior shape against
  the pipeline, both routed; two mandates back to back in one copy, like a warm queue; and runs
  with a tight cap. `--arm-cap ARM=USD` sets each arm's cap per run. The scout arm runs the native
  scout and the tight-cap arm runs with the governor, whatever the config says. A comparison starts
  only when the bench cap left covers all its arms, and then runs them all. Without `--yes` it lists
  the planned runs, each cap, the settings each arm forces and a worst case that allows an overshoot
  past each cap (`--overshoot-usd`; by default the largest the ledger measured, at least $0.10), and
  spends nothing. `cuanta bench report` states each target as met, missed or n/a with the measured
  numbers, from the stored run records: exploration-leak tokens with and without the discipline,
  counted the same way in both arms (reads outside the change plan, and reads that break the
  discipline's rule: a Read without a range of a file above the line limit, a whole-tree content
  Grep); the scout's saving with acceptance parity and the scout mode that ran (n/a when the scout
  did not run or neither arm was accepted); the second mandate's first-request cache reads over its
  fixed prefix; and, per run, whether a run was cut by its cap (a budget stop without a final
  answer) although a graceful finish was possible (n/a when no run reached the finish rule).
  `--json` adds `targets`. The `proof` suite adds `shop-restock-report`, a feature whose request
  names no file and whose change needs reads across five modules.
- `runs.governor` (on by default) turns all live steering off when false: finish turns, Codex stop
  and resume, and rotation.
- `cuanta mandate --classic` runs one mandate the V4 way, for comparisons: the pipeline shape (no
  scout), docs on, read discipline off and the governor off. The run records `mode: classic`, which
  `cuanta costs --metrics --json` reports as `mode`; `cuanta queue add` keeps the flag. Trial specs
  take `mode = "classic" | "v5"`, and the trial summary records the mode, the provider, the tokens
  by kind, the first request's fixed tokens, the blocked reads, the forecast's P50 and P90 against
  the actual and the outcome.
- Engine-qualified role pins are honored or refused before launch with the reason.
- Roles pass a structured, accumulating handoff: a summary, decisions, facts anchored as `path:start-end`
  with line hashes, the plan's edit/read/verify sets, open questions and a status. Anchors are checked
  against the working copy and stale ones are marked; the chain is compacted to the depth's budget and
  full texts stay in capsules.
- cuanta runs the project's type check, lint and build between roles, in the working copy and outside any
  engine sandbox, with a timeout that stops the whole process tree. A failing writer gets one repair turn;
  results appear as their own phase with $0 model spend. Only commands from cuanta's change plan run.
- Claude's print-mode permission denials are stored as `permission_denied` events with tool and path.
- Fast Claude implementation profiles, pure model pins, installed effort and output-speed
  variants, and Team controls in English and Spanish.
- Same-session verification and repair, baseline error separation, ordered green steps, warm
  sandbox reuse and time forecasts grouped by implementation configuration.
- Phase timing for runs and roles, with request durations, time to first token, tool and hook
  time, and cuanta's launch and verification work. Missing measurements stay unknown, and
  overlapping service durations are distinguished from complete wall time. Historical process
  dates are not presented as a complete measurement of preparation through cleanup.

### Changed
- `runs.profile` defaults to `auto`: Claude features and bug fixes, including the fix steps of
  `cuanta loop`, run with the fast implementation profile. Refactors, investigations, other
  engines, `--classic`, runs that choose another shape or routing, Claude Code versions that
  cannot take follow-up turns and projects without a typecheck command or `lint` script keep the
  balanced team. A fast run without `--model` uses the variant measured fastest for its kind among
  those accepted on every trial task of that kind: Opus 5.5 at low effort for features, Opus 5.5
  at high effort for bug fixes. Fast runs stop at the native cap, the turn rail and the repair
  limits; the live governor steers team runs. `--profile auto` restores the default when the
  project config sets another profile, and the Team screen offers the by-kind model choice.
- `--role-model role=model` pins a model within the team's provider; a pin to the other provider is
  refused before launch with the reason. `--cross-engine` now runs each role as its own launch on the
  same provider.
- The tester asks for the standard tier instead of premium.
- A Claude role's native cap now sits that model's P90 overshoot in dollars below its share ($0.08
  until three overshoots are recorded, never under half the share), instead of a proportional cut
  that left small shares, such as a GSAP fix's senior, without room to finish.
- Read discipline is on by default for team roles (`runs.pipeline_read_discipline`): Claude roles
  run cuanta's hooks, which block unbounded large reads and whole-tree content searches and send raw
  test commands to `cuanta test`, logging each blocked call with the tokens it would have cost. A
  team role's settings file is generated even in a full session so the hooks apply. Codex roles get
  the rules in their prompt, as best effort. Single launches keep `runs.read_discipline` off.
- On Windows, a GPT team's tester stays on Codex and is told not to run node, npm or npx commands
  (builds or tests); cuanta runs the checks itself between roles.
- Team recommends a provider, not a preset, from measured cost per accepted change. Codex pipeline
  mandates that ran as one session are left out of the count.
- Bug and fix mandates get their own budget. A team of separate launches holds a repair share for
  one repair turn after a failed check: 15% of the cap when docs runs, or docs's share when docs is
  off. The repair turn gets that share plus the writer's left-over, so a writer that spent its whole
  share still gets its repair. Feature and other mandates split the cap as before.
- From the command line, a GPT team is capped by `--max-budget-usd`, else the `--depth` cap, else the
  `budget.usd` setting, else the normal depth's cap, as in the app; `--cross-budget-usd` sets the cap
  and `--max-budget-usd` bounds it. A Claude team run with `--cross-engine` keeps its $1.00 default.
  The dry run shows the cap.
- `cuanta costs` groups a run of one launch per role under its provider; only runs whose roles used
  more than one engine stay under cross-engine.
- Docs is optional. The docs role runs only when the request asks for docs (docs, documentation,
  documentación, README, CHANGELOG, guide, guía and similar words in WHAT, WHERE or TESTS), and never
  in trials (isolated copies); `runs.docs = on` restores the old behaviour, and a
  `--role-model docs=...` pin runs it on any run. When docs is off, its
  share becomes the repair reserve of a team of separate launches, for features as for fixes.
- Recorded the V5 real-cost comparison: including failed attempts, cost per accepted change fell
  11% for Claude and 37% for GPT against classic on this sample; neither team accepted the GSAP fix.
  The V5 audit and forecast-error targets were missed. Historical E1 costs are distinguished from
  their V4 replays in the report.
- Recorded the WhatsApp feature trial: Claude produced a candidate rejected by lint; GPT was
  blocked by its environment and changed no files. No WhatsApp patch was accepted or applied.
- Cross-engine budgets finish: role floors, shares from role history once there are three samples, Claude
  native caps a learned margin below the share, salvage handoffs after a budget stop, optional docs and
  verified-tester skips, Codex overruns charged to the remainder, and a blocked role stops the pipeline.
  Runs end complete, complete with optional roles skipped, partial (with the cause) or failed, shown in
  the result, `runs show` and `cuanta costs`.
- The working copy is checked after every role; a protected change stops the pipeline before the next role
  and names the role. Verification outputs stay out of recorded sandbox patches.
- Pipeline roles get the owned index server by default (`runs.pipeline_index_tools`): Codex through a
  per-launch `mcp_servers` entry, Claude through the generated profile. Role prompts lead with the anchored
  chain and how to open a range. The `page` tool accepts `a-b` ranges.
- Recorded the indexed E1/T1 replay with frozen requests, observed models, cache,
  source guards and costs per accepted attempt. The repeated Sonnet audit met its
  $0.30 target; blocked comparisons and bounded GSAP recovery limits remain explicit.
- Bench adds two read-only investigation tasks with delivered-answer keyword and real
  file:line checks. Shape, pack and depth options retain actual run settings and numerical
  phase anatomy/read efficiency by condition; baseline uses a single read-only context.
  Automatic pack disabling preserves index tools and plans, and source edits reject answers.
- `models stats --files` reads existing closed snapshots for file/task/mix history priors
  and retrospective known-cost allocation. Unknown prices, unallocated cost and stale or
  estimated samples stay visible; the query creates, migrates and repairs no stores.
- Result Consumption and Spectrum show read efficiency with an explicit file-based formula.
  Successful raw and Cuanta page reads count unique source files; investigations count cited
  reads and code work counts edited or cited reads. Saved sandbox aliases normalize to source
  paths. Missing pipeline reports retain the previous token-based utilization fallback.
- Result Consumption and Spectrum show an exclusive request-phase anatomy with tokens,
  requests and observed costs. Spectrum JSON includes safe per-agent phase records;
  investigation advice identifies observed delegated contexts and possible handoffs.
  Costs adds phase medians by task type with explicit coverage, preserving billed totals.
- Tool attribution distinguishes empty-source defaults from explicit agent signals. Closed
  delegation windows resolve local tools before conservative API timestamp brackets; explicit
  SDK and named-agent requests retain their attribution. A scrubbed real capture covers the
  analyst reads without changing token or cost totals.
- Bench runs accept `--index on|off` and report observed exploration and actual change
  boundaries. An index suite adds a protected-path fix while preserving existing suites.
  Disabling automatic index use retains lexical protection and explicit index commands.
- Completed runs and outcome changes refresh indexed findings and task history. Verified
  sandbox notes survive copy removal; changed anchors remain stale for revalidation.
- Map search shows ranked reasons, file cards, impact and fresh or stale findings. Result
  and Spectrum show observed index use, returned-token estimates and manifest-based guards
  without counting provider copies of owned MCP calls twice.
- Optional owned MCP sessions expose seven bounded local index tools, with negotiated
  stdio lifecycle and metadata-only per-run call logging. Real Claude negotiation and
  parent/analyst reads were observed; complete protected-write coverage remains unverified.
- A normal MCP disconnect preserves the successful startup connection and its duration
  instead of reporting the session's clean shutdown as a failed server.
- Mandates and cross-engine roles receive deterministic indexed context packs before
  volatile request data. Senior and tester roles receive editable-file cards and fresh
  anchored facts; protected write suggestions are excluded. The local `pack --for`
  command shows bounded excerpts, estimated tokens and inclusion reasons at zero spend.
- Native trial specifications normalize matching engine-qualified role model references
  before routing, so resolved model IDs do not silently fall back to the default policy.
- Cross-engine pipelines reserve a budget share for each role, carry unused shares forward,
  and show the complete pipeline duration. Estimates use completed runs of the same shape
  and depth, with a historical calibration factor when direct history is unavailable.
- Windows Team identifies unverified Codex builds; cuanta performs project checks outside the
  engine sandbox. Unreadable sandbox results retain their copy and cost without becoming applicable.
- The maintainer's Git workflow, verification gates and release steps move from
  `CONTRIBUTING.md` to `docs/DEVELOPMENT.md`, and the README's Contributing section becomes
  Maintenance.
- `SECURITY.md` directs vulnerability reports to GitHub private vulnerability reporting
  instead of a public issue requesting a private channel.

### Fixed
- Stop, in the app's pipeline screen, stops a team run of one launch per role: the running role ends
  and no later role starts. A stop during cuanta's checks between roles ends the running check and
  starts no other.
- Scout & senior on the GPT team: the senior is told that the scout's pack replaces the analyst's
  plan, so it no longer stops for a missing plan; the scout treats index card flags as hints; and a
  card is flagged "generated; do-not-edit" only by its path or an explicit do-not-edit note, not by
  any rule that mentions generated code.
- Read-discipline hooks now run on Windows: hook commands use a POSIX interpreter path, because Claude Code
  runs them through Git Bash.
- Pins written as `engine:resolved-name` (for example `claude:claude-sonnet-5`) were ignored.
- Sandbox trial budget reservations persist across separate selections and retries, block unknown
  spend, and leave native request headroom before launching another trial.
- Hook timing reads Claude's `total_duration_ms` attribute, including retained events whose
  normalized duration was zero.
- Fast runs deny Claude's `Workflow`, agent coordination and scheduling or cloud tools, so one
  native session does the work; Ultracode keeps its workflows but not the scheduling tools.
- The background sandbox cleanup on Windows breaks away from job objects that allow it, and hands
  the copy to its worker before the command exits, so another launch cannot sweep a copy that is
  still being recycled.

### Removed
- Mixed teams: `mandate --mix` and its presets `claude-only`, `claude-plans-codex-writes` and
  `codex-plans-claude-writes`, the app's mix chips, and `docs/MIXED_TEAMS.md`.
- cuanta no longer accepts contributions: `CONTRIBUTING.md` and the bug report, feature
  request and pull request templates are removed.

## [0.3.0] - 2026-09-26

Engine guarantees, honest cost reporting, cache and turn limits, and the first PyPI release workflow.

### Added
- Engine guarantees in Team and CLI: spend caps, turn limits, read-only behavior and telemetry
  are identified as enforced, checked after the run or unavailable. Cross-engine launches show
  each role's guarantees and warn before launching an engine that cannot enforce the cap.
- A tag-triggered PyPI Trusted Publisher workflow with separate verification, build and publish
  jobs, followed by installation of the published version on Windows, Linux and macOS.
- User-only `scripts\git\push.cmd --tag` checks the release version and changelog, a clean
  `main` matching the remote, successful CI for that exact commit, and absent local/remote tags
  before creating and publishing an annotated version tag.
- PyPI installation instructions, a security reporting policy, and bug, feature and pull request
  templates linked to the contribution workflow.
- `docs/CONTRACTS.md` records external contracts with their version, status, date and evidence.
- Claude mandates have a depth-based turn limit, configurable through `CUANTA_MAX_TURNS`,
  `runs.max_turns` or `--max-turns`; the ledger records the limit and turns used.
- Single-context Claude investigations limit built-in tool definitions. An M0 A/B probe on
  Claude Code 2.1.282 cut first-request tokens from 30,588 to 15,454.
- `cuanta probe cache-ttl` measures a bounded prompt-cache window in a temporary project;
  without `--yes` it previews the plan. Measurements record authentication mode, engine version,
  model and date in user config when the result is usable.
- Result and Spectrum show whether a Claude run's first request found a warm or cold cache;
  Team and Home show an estimated prefix window, or unknown without a usable measurement.
- A per-test pytest timeout and disabled xdist worker restarts make hung or crashed tests fail
  with diagnostic output.
- Health reports the size of the home `AGENTS.md` and its estimated share of a first request.
- Public agent instructions linking the project rules and guarded Git workflow.
- Repository Git workflow on `main`: setup, guarded commit and user-only push scripts,
  a POSIX hook stripping AI attribution, a guard against committing ZIP archives,
  and temporary-repository integration tests.
- Parallel milestone gates with pytest-xdist and serialized fixed-port listener tests.
- `cuanta instinct use NAME --global` sets a user-level backend, and `instinct show`
  identifies whether the effective value came from the project, user or default config.
- Instinct fallback reasons appear in decisions, the wizard, Result and `doctor`; setup
  checks cover missing keys and key/base URL mismatches.
- Graphify health runs its launcher and degrades graph-dependent prompts and tools when
  the launcher is broken.
- `cuanta bench run|report`: runs fixed tasks under three conditions (plain `claude -p`,
  cuanta with routing off, cuanta with routing auto) with the engine version and main model
  pinned, randomized order, a fresh repository copy per run and caps per run and per bench.
  Hidden acceptance tests decide each run; a capped run counts as not accepted. The report is
  Markdown plus SVG charts (tokens per accepted task, success rate, cost) with medians and
  ranges; `--readme` refreshes a README section. `bench/tasks` ships the five-task `mini` suite and the `full` suite, which adds two public
  repositories (boltons, more-itertools) fetched over HTTPS at pinned commits with a checked
  sha256.
- A read-only Benchmark screen in the app (command palette).

- Every run keeps its complete final report in `.cuanta/runs/<id>/report.md` (never truncated)
  next to `run.json`. A Result screen opens when a run finishes: status, type, duration, cost,
  the split between the fixed session context and your request, the report rendered as
  Markdown with clickable `file:line` references, the changed files with their diff, and
  consumption per agent. It saves the report to `docs/` (investigations go to
  `docs/investigations/`), copies it, continues with the report's NEXT request prefilled,
  opens Spectrum and exports a Markdown run report. The Ledger opens any run's result.
- Mandates never run the pipeline template without Forge agents. The guided flow offers
  "Initialize the project first" (with the usual cost of an init) or a clearly labeled simple
  mode (one agent, no project knowledge); the CLI takes `--simple`. The header and Home show
  whether Forge is installed.
- A clarity check before Next and Launch when a request is very general, with Improve with AI,
  Add details and Launch anyway. A low-clarity request is capped at the standard tier and never
  raises the scope.
- Instinct decisions carry the run they belong to; decisions made while typing or previewing
  are flagged as previews, repeated ones are deduplicated, and outcomes are recorded on the
  run's decisions (ledger schema v5).
- Claude investigation mode: read-only (Read, Grep, Glob and graphify; Write and Edit are
  denied), one architecture-analyst run or one agent in simple mode, and a structured report —
  summary, findings with `file:line`, risks, open questions, suggested next step. The vendored
  Forge mandate template gains the same investigation variant. Investigations route the
  analyst to standard and docs to economy, and open on the Report tab with "Save to docs/"
  first.
- The guided flow asks each intent its own questions: an investigation asks what to understand,
  the questions to answer, the deliverable and the folders in scope; a feature asks what it
  should do and its acceptance criteria; a refactor asks what should improve and its
  invariants. Each field shows one good example as its placeholder.
- Lean sessions (`runs.session = lean`, the default; `--session lean|full` per run): runs cuanta
  launches on Claude Code get only the MCP servers they need (none) through a generated
  `--mcp-config` with `--strict-mcp-config`, and a `--settings` file with `disableAllHooks` and
  every installed plugin disabled. Credentials are never touched. Codex and OpenCode keep
  their configured plugins and MCP servers; their sandbox and permission guarantees differ.
- The fixed session overhead — the first request's context, the plugins, MCP servers and hooks
  that loaded, a server that failed to connect, the startup time — shows on the Result screen,
  in Spectrum and in `cuanta spectrum`. Health warns about MCP servers that fail to connect, a
  user-level claude-agent-forge older than the vendored one, and a slow session start.
- `cuanta bench run --session lean|full|both` (lean by default) and `--task`; the report shows
  each condition per session profile with the first-request context of every run.
- `cuanta mandate` and `pounce` print the run's report after the summary (Markdown rendered in
  pretty mode, raw in plain, `report_text` in `--json`) and where it was saved.
- `cuanta runs list | show <id> [--markdown] | open <id>`: recent runs with a stored report, a
  run's report, consumption and session overhead, the human run report, and the app opened on
  that run's Result screen. Run ids accept a unique prefix.
- Exports: CSV is one table per file and "all tables" writes a ZIP with one CSV each, in UTF-8
  with a BOM for Excel; JSON is pretty-printed and leaves out the raw event payloads unless
  you ask for `--include-raw` (a checkbox in the app); `cuanta ledger export --all`.

### Changed
- Codex investigations and analyst roles explicitly use `read-only`; editing runs and roles
  use `workspace-write`, with no extra writable roots or writable temporary directories.
  Only temporary copies owned by cuanta skip Codex's Git repository check.
- OpenCode investigations and read-only analyst roles are refused with a reason: the verified
  permission profile permits shell write bypasses when graphify is allowed.
- Missing costs remain `n/a` in the ledger, Result and totals, and `null` in JSON exports.
  Known token prices produce labelled estimates; capped cross-engine, benchmark and retry
  flows stop before more work when remaining spend cannot be calculated. Historical ledger
  values are preserved because old zeroes lack enough provenance to reinterpret safely.
- OpenCode runs stop when reported step costs reach the cap, or a capped step omits cost,
  retaining the ending reason and reported usage. A step can exceed the cap before reporting.
- CI and release actions use verified Node 24 implementations and immutable action pins.
- Performance tests run in a dedicated process after discovery and parallel tests, with
  unchanged timing budgets and coverage accumulated across both required gate phases.
- Investigation prompts request the report in the request's language, and displayed reports
  begin at their first heading while the raw report stays available. New runs store whether
  the shape was a single context or a pipeline; ambiguous older runs display unknown.
- A visual pass on the app: buttons are three rows tall (primary filled, secondary quiet) with
  a visible focus ring; button rows wrap instead of clipping their labels; cards separate by
  surface color instead of borders; the header facts are compact chips; label/value facts wrap
  inside their own column. Home shows the large Michi only when the project is empty, and the
  next step is one line with one button. The mandate mode is a labeled "Guided | Expert"
  control, completed wizard steps are clickable with a "Step 2 of 4" label, and the wizard's
  text areas grow with their content. Loading panels show a skeleton instead of a blank area.
- The heuristic scope is type-aware: a short investigation without evidence of breadth is
  trivial or normal, never complex. Instinct moves a role away from your default only when it
  is confident, and the reason is shown.

### Fixed
- Codex usage retains the requested model for pricing. Catalog models have standard price
  rows; cumulative token totals and cache/reasoning subsets are counted once. Missing rates
  stay unavailable, and estimates are not presented as actual subscription billing.
- Team previews route within the selected engine, matching the launched model plan.
- CLI help defers output setup until command execution; the app applies its responsive
  classes before mounting, avoiding a redundant first-frame layout pass.
- Home table headings use plain text, avoiding an unnecessary emoji dictionary import during
  cold startup while preserving the English and Spanish labels.
- Windows descendant discovery excludes terminated process objects whose handles remain
  open while preserving live descendants. SQLite migration tests close their connections.
- TUI tests await deferred focus and mounting work before shutdown, and process tests wait
  for children to finish, keeping exact assertions and timing budgets unchanged.
- POSIX process discovery excludes its own `ps` probe, preventing false orphan reports.
- CI tests cover graph tooling present and absent without relying on host installations,
  and verify repair commands for Windows, Linux and macOS explicitly.
- Wizard Launch and Preview validate required fields before opening the pipeline;
  investigation questions are extracted from Spanish and English enumerations, with
  the story as a fallback.
- The pipeline displays only roles that run, and Team offers installed engines while
  preserving per-role model choices.
- Closing the app immediately after launch no longer processes late input events;
  test caches and reports are routed outside the repository root.
- `cuanta models probe --yes` now spends: `--yes` is a global flag and was never reaching the
  command.

## [0.2.0] - 2026-09-24

Model routing with Jev, a prompt assistant, and a friendlier app.

### Added
- A model catalog per engine (`cuanta models list|refresh|tier|stats|probe`): Claude Code
  aliases and ids with the `availableModels` allowlist and default model from your settings,
  Codex from `codex debug models` and `~/.codex/config.toml`, OpenCode from
  `opencode models --verbose`. Tiers (economy, standard, premium, frontier) ship as data in
  `assets/model_tiers.toml`; models without an anchor are placed by price, and any tier can be
  overridden per project. The catalog is cached in `.cuanta/models.json`.
- Model routing: a `[routing]` policy (mode auto/fixed/off, engine order, per-role tiers, caps,
  presets save/balanced/best, `min_confidence`), Instinct questions per role with redacted
  state, escalation after a persistent failure, and a learning log that prefers the cheapest tier
  that keeps succeeding. `cuanta route --dry-run` explains every choice.
- Routed mandates: Claude Code receives the Forge agents through `--agents` with only `model`
  (and `effort`) changed, and the orchestrator through `--model`; Codex and OpenCode run a single
  model. `mandate`/`pounce` accept `--route`, `--preset`, `--role-model role=model` and
  `--override-env-model`, and print the team before launching.
- A routing audit after every routed run compares the planned model with the one telemetry saw
  per agent, explains mismatches, and shows up in the mandate summary, the app and
  `cuanta spectrum`.
- A Models screen in the app: the catalog with a tier editor, refresh and the opt-in probe;
  the routing editor (mode, preset, a tier and an "Instinct decides" switch per role, engine
  order, budget caps, frontier models); and what worked per task, role and tier.
- The Instinct screen shows the Jev connection (key, endpoint, model, latency, this week's
  spend, Test connection), decisions as sentences ("Mandate scope → normal (72%)") and exactly
  what a remote backend receives.
- A guided four-step mandate (intent, description, limits, team and cost) with a live clarity
  meter, one-click chips (use the last failure, pick a file, split the task, see an example),
  suggestions from the graph and the rulebook, an optional "Improve with AI" rewrite shown as
  a diff, per-agent model overrides and a p50–p90 cost estimate. Expert mode keeps the form.
- A first-run welcome (what cuanta does, your engines, a routing preset), `?` help for every
  screen, tooltips on the key controls, and clickable engine and listener chips.
- An experimental cross-engine pipeline (`mandate --cross-engine`): each role runs as its own
  headless run on its routed engine and hands a JSON capsule to the next, under its own cap.
- `cuanta test --affected`: runs only the tests related to files changed since the last full
  run (file-name heuristics plus the graphify graph), last failures first; falls back to the
  full suite with a stated reason. Every mandate ends with one full-suite verdict.
- Jev through OpenRouter: `TYPESAFE_BASE_URL` (with `TYPESAFE_API_BASE` as an alias), cost from
  `usage.cost`, and a connection check that asks one `noul` question instead of trusting
  `/v1/models`. Jev `score` questions are supported.
- Settings for the app background (`ui.background = solid|terminal`) and the listener
  (`ui.listener = auto|manual`); the header's listener chip starts and stops it with a click.
- `cuanta ui --web` serves the first frame and a Michi favicon.
- Terminal detection by capability (console window class, VT support, ancestors), with a
  `CUANTA_TERMINAL=modern|legacy` override and a doctor check.
- A root `LICENSE` and this changelog.

### Changed
- Prompts reach the engines through stdin (`claude -p`, `codex exec -`) or an attached file
  (`opencode run --file`), never the command line. Long `--evidence` is kept to about 8,000
  characters inline; the full text is stored as a capsule the prompt points to. A command line
  over 30,000 characters fails with a cuanta message before anything is launched.
- A run whose cost the engine does not report is estimated from the price table, or shown as
  `n/a` when the model has no price; it is never `$0.00`. The ledger schema moves to v4
  (nullable costs, routing decisions, routing audits).
- The app is fully translated: application results carry message ids that the app renders in
  English or Spanish, while the CLI keeps its English output.
- Home offers "New mandate" as the primary action once a project is initialized, with
  "Refresh knowledge" next to it; the listener is no longer suggested as the next step.

## [0.1.0] - 2026-09-22

First release.

### Added
- Token observability for Claude Code, Codex and OpenCode: OTLP listener, SQLite ledger,
  `cuanta spectrum` token maps, leaks and utilization, import of Claude transcripts.
- The test gateway (`cuanta test`) that clusters failures into signatures and keeps full logs
  in capsules (`cuanta cat`).
- Mandates (`cuanta mandate`, `cuanta pounce`), the fix loop, and Instinct decision backends
  (heuristic, Jev, LLM).
- `cuanta init` and `cuanta refresh` with the vendored claude-agent-forge 0.4.0.
- The Textual app (`cuanta ui`) in English and Spanish.
