from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from cuanta.adapters.instinct.heuristic import HeuristicInstinct
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.prices import load_prices
from cuanta.application.assistant import Improvement, changes, sent_payload
from cuanta.application.bench import BenchResult
from cuanta.application.cat_capsule import CapsuleView
from cuanta.application.doctor import CheckResult, DoctorReport, result
from cuanta.application.estimate import Estimate, estimate
from cuanta.application.home import HomeSnapshot, next_step
from cuanta.application.init_project import (
    STAGES,
    InitContext,
    InitOptions,
    InitReport,
    stage,
)
from cuanta.application.instinct import DecisionMaker
from cuanta.application.instinct_view import BackendStatus, JevCard, ProbeRow
from cuanta.application.intake import IntakeService, Understanding
from cuanta.application.loop import LoopReport
from cuanta.application.mandate import MandateReport
from cuanta.application.mandate_flow import (
    MandateOptions,
    MandatePreview,
    MandateSetup,
    resolve_budget,
)
from cuanta.application.models import CatalogView, ProbeOutcome
from cuanta.application.new_files import NewFilePair
from cuanta.application.results import ResultView, RunFile
from cuanta.application.routing import RoleStats, RoutePlan
from cuanta.application.spectrum import ALL_SESSIONS, Selection, SpectrumQuery, SpectrumResult
from cuanta.application.tests_view import Hairball, TestsSummary
from cuanta.domain.assistant import (
    EXAMPLES,
    Clarity,
    Suggestions,
    chips_from,
    heuristic_clarity,
    heuristic_gaps,
)
from cuanta.domain.cache import UNKNOWN_PREFIX, PrefixWindow
from cuanta.domain.capsules import Level
from cuanta.domain.config import Config
from cuanta.domain.detection import (
    Detection,
    DocsState,
    EngineInfo,
    ForgeState,
    GraphMode,
    SizeTier,
    Stack,
    VerifyDecision,
    VerifyTier,
)
from cuanta.domain.drafts import Draft
from cuanta.domain.engine import (
    EngineEvent,
    ModelUsage,
    RunResult,
    SessionStarted,
    StepUsage,
    ToolCall,
)
from cuanta.domain.fixes import Fix
from cuanta.domain.forge_verify import Finding
from cuanta.domain.instinct import Choice
from cuanta.domain.ledger import Capsule, Decision, Run
from cuanta.domain.loop import LoopGate, StopReason
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import msg, option_message
from cuanta.domain.models import ModelEntry, Tier, TierSource
from cuanta.domain.new_files import original_of, side_by_side
from cuanta.domain.progress import (
    Note,
    ProgressEvent,
    Status,
    StepFinished,
    StepStarted,
    finished,
    note,
    started,
)
from cuanta.domain.report import ContextSplit, file_refs, next_request, parse_sections
from cuanta.domain.routing import RoutingPolicy, default_requests, plan_route, roles_that_run
from cuanta.domain.telemetry import WiringPlan, WiringReport, WiringState
from cuanta.domain.terminal import TerminalKind, TerminalReport
from cuanta.tui.services import ALL_IMPORTED, LoopState, TelemetryPanel
from tests.ledger_fixture import fill

DETECTION = Detection(
    root="/work/shop",
    project_name="shop",
    stack=Stack(language="python", language_version="3.12", test_runner="pytest"),
    file_count=1284,
    size_tier=SizeTier.MEDIUM,
    docs_state=DocsState.EXISTS,
    forge_state=ForgeState.INITIALIZED,
    verify=VerifyDecision(VerifyTier.STRONG, "pytest + mypy"),
    graph_mode=GraphMode.CLI,
    graph_evidence="graphify 0.9.55",
    vcs=True,
    engines=(EngineInfo("claude", "/bin/claude", "2.1.280"),),
    run_note="",
)
CHECKS = (
    result("python", Status.OK, msg("doctor.python.ok", version="3.12.9")),
    result("engine claude", Status.OK, msg("doctor.engine.found", version="2.1.280")),
    result(
        "engine codex", Status.WARN, msg("doctor.engine.missing"), "npm install -g @openai/codex"
    ),
    result(
        "engine opencode", Status.WARN, msg("doctor.engine.missing"), "npm install -g opencode-ai"
    ),
    result("listener", Status.OK, msg("doctor.listener.on", port=4318, written="1,204")),
    result(
        "telemetry claude",
        Status.WARN,
        msg("wiring.none"),
        "cuanta telemetry on --engine claude",
    ),
)
FIXED_TIME = "2026-09-24T12:00:00Z"
PLACES = ("src/shop/cart.py", "src/shop/tax.py")
SIMILAR_RUNS = (
    Run("r-inv", "mandate", cost_usd=0.545, task_type="investigation", depth="normal"),
    Run("r-b1", "mandate", cost_usd=0.4, task_type="bug", depth="normal"),
    Run("r-b2", "mandate", cost_usd=0.9, task_type="bug", depth="normal"),
    Run("r-b3", "mandate", cost_usd=1.3, task_type="bug", depth="normal"),
)
CATALOG = tuple(
    ModelEntry(
        engine,
        name,
        name,
        provider,
        context=context,
        input_price=price,
        output_price=price * 5 if price is not None else None,
        resolved=resolved,
        default=default,
        tier=tier,
        tier_source=TierSource.ANCHOR,
    )
    for engine, name, provider, context, price, resolved, default, tier in (
        ("claude", "opus", "anthropic", 1_000_000, 4.0, "claude-opus-5-5", True, Tier.PREMIUM),
        ("claude", "sonnet", "anthropic", 1_000_000, 2.0, "claude-sonnet-5", False, Tier.STANDARD),
        ("claude", "haiku", "anthropic", 200_000, 1.0, "claude-haiku-4-5", False, Tier.ECONOMY),
        ("codex", "gpt-5.6-sol", "openai", 272_000, 4.0, "gpt-5.6-sol", False, Tier.PREMIUM),
        ("codex", "gpt-9-private", "openai", 0, None, "gpt-9-private", False, Tier.STANDARD),
    )
)
ROLE_STATS = (
    RoleStats("bug", "senior", "premium", 6, 5, 3.1, 6),
    RoleStats("bug", "tester", "standard", 4, 4, 0.8, 4),
)
RUNS = (
    Run(
        "01JABCDEF0000000000000TEST1",
        "test",
        "",
        started_at="2026-09-23T10:02:11Z",
        status="failed",
    ),
    Run(
        "01JABCDEF0000000000000MAND1",
        "mandate",
        "claude",
        started_at="2026-09-22T18:40:00Z",
        status="completed",
        cost_usd=2.4187,
    ),
    Run(
        "01JABCDEF0000000000000INIT1",
        "init",
        "claude",
        started_at="2026-09-21T09:15:30Z",
        status="completed",
        cost_usd=1.59,
    ),
)
HAIRBALLS = (
    Hairball(
        "sig-a1b2",
        9,
        "src/shop/cart.py:42",
        "fix",
        "AssertionError",
        "AssertionError: [total] expected 10 got 12",
        "tests/test_cart.py::test_total",
    ),
    Hairball(
        "sig-c3d4",
        3,
        "src/shop/tax.py:7",
        "",
        "KeyError",
        "KeyError: 'MX'",
        "tests/test_tax.py::test_rate",
    ),
)
RED = TestsSummary(
    "pytest", "red", 400, 12, 0, 1, 8.4, "cap-9f8e", "2026-09-23T10:02:11Z", HAIRBALLS
)
GREEN = TestsSummary("pytest", "green", 412, 0, 0, 1, 7.9, "cap-0001", "2026-09-23T11:00:00Z", ())
LOG = (
    "tests/test_cart.py::test_total FAILED",
    "Traceback (most recent call last):",
    '  File "src/shop/cart.py", line 42, in total',
    "    assert total == 10",
    "AssertionError: [total] expected 10 got 12",
    "tests/test_tax.py::test_rate FAILED",
    "KeyError: 'MX'",
)
AGENTS = ("architecture-analyst", "python-senior", "tester", "docs-updater")


def pipeline_events(agents: tuple[str, ...] = AGENTS, ok: bool = True) -> list[EngineEvent]:
    events: list[EngineEvent] = [SessionStarted("s1", "claude-sonnet-5")]
    for index, agent in enumerate(agents):
        spawn = f"spawn-{index}"
        events.append(ToolCall("Agent", spawn, {"subagent_type": agent}))
        events.append(ToolCall("Read", f"read-{index}", {"file_path": "a.py"}, spawn))
        events.append(
            StepUsage(ModelUsage("claude-sonnet-5", 1000 * (index + 1), 100), spawn, f"m{index}")
        )
    events.append(RunResult(ok, "success" if ok else "error_during_execution", 0.42, 9, "s1"))
    return events


def single_context_events(ok: bool = True) -> list[EngineEvent]:
    return [
        SessionStarted("s1", "claude-sonnet-5"),
        ToolCall("Read", "read-0", {"file_path": "a.py"}),
        StepUsage(ModelUsage("claude-sonnet-5", 1000, 100), "", "m0"),
        RunResult(ok, "success" if ok else "error_during_execution", 0.42, 1, "s1"),
    ]


def mandate_report(ok: bool = True) -> MandateReport:
    run = Run(
        "01JMANDATE0000000000000RUN1",
        "mandate",
        "claude",
        status="ok" if ok else "failed",
        cost_usd=0.42,
    )
    return MandateReport(
        run=run,
        ok=ok,
        changed_files=("src/shop/cart.py", "tests/test_cart.py"),
        tests="green" if ok else "not run",
        tokens_by_agent={"python-senior": 2100},
        utilization=0.4,
        handoffs=AGENTS,
        tool_calls=8,
        hint=Choice("normal", 0.72),
        run_file=".cuanta/runs/x.json",
    )


REPORT_TEXT = """## SUMMARY
The landing page renders its hero from `src/app/page.tsx:12` and loads fonts twice.

## FINDINGS
- The hero image has no width, see src/app/page.tsx:14.
- Fonts are imported in src/app/layout.tsx:3 and again in src/styles/globals.css:1.

## RISKS
None.

## NEXT
TYPE:            refactor
WHAT:            Load the fonts once, from layout.tsx
WHY / EVIDENCE:  src/styles/globals.css:1 imports them again
WHERE:           src/app/layout.tsx
OUT OF SCOPE:    the page content
"""


def sample_result(run_id: str = "01JMANDATE0000000000000RUN1", simple: bool = False) -> ResultView:
    text = REPORT_TEXT
    return ResultView(
        run=Run(
            run_id,
            "mandate",
            "claude",
            model="claude-sonnet-5",
            started_at="2026-09-24T10:00:00",
            ended_at="2026-09-24T10:00:17",
            status="ok",
            cost_usd=0.14,
        ),
        task_type="investigation",
        simple=simple,
        single=True,
        text=text,
        sections=parse_sections(text),
        changed_files=("src/app/page.tsx",),
        tokens_by_agent={"main": 53107},
        tests="not run",
        split=ContextSplit(53107, 1410),
        refs=file_refs(text),
        follow_up=next_request(text),
        report_path=f".cuanta/runs/{run_id}/report.md",
    )


def fixture_ledger(empty: bool = False) -> MemoryLedger:
    ledger = MemoryLedger()
    if not empty:
        fill(ledger)
        for run in RUNS:
            ledger.add_run(run)
    return ledger


DAILY = (0, 120_400, 88_000, 1_450_000, 0, 612_300, 2_004_000)


def snapshot(
    initialized: bool = True, runs: tuple[Run, ...] = RUNS, checks: tuple[CheckResult, ...] = CHECKS
) -> HomeSnapshot:
    detection = replace(
        DETECTION, forge_state=ForgeState.INITIALIZED if initialized else ForgeState.FRESH
    )
    report = DoctorReport(detection, checks)
    return HomeSnapshot(report, runs, DAILY, next_step(report), "0.4.0 (8b8a490)")


@dataclass
class FakeServices:
    home_snapshot: HomeSnapshot = field(default_factory=snapshot)
    project: Path = Path("/work/shop")
    calls: list[str] = field(default_factory=list)
    prefix: PrefixWindow = UNKNOWN_PREFIX
    prefix_engines: list[str] = field(default_factory=list)
    fail: str = ""

    latest: TestsSummary | None = None
    bench: BenchResult | None = None
    run_result: TestsSummary = RED
    copied: list[str] = field(default_factory=list)
    applied: list[Fix] = field(default_factory=list)

    def home(self) -> HomeSnapshot:
        self.calls.append("home")
        if self.fail:
            raise RuntimeError(self.fail)
        return self.home_snapshot

    def prefix_window(self, engine: str) -> PrefixWindow:
        self.prefix_engines.append(engine)
        return self.prefix

    def latest_tests(self) -> TestsSummary | None:
        return self.latest

    def run_tests(self) -> TestsSummary:
        self.calls.append("run_tests")
        return self.run_result

    def capsule(self, reference: str, level: Level) -> CapsuleView:
        capsule = Capsule(reference, "0" * 64, "x.log", "test", 400, len(LOG), "summary", "")
        if level is Level.L1:
            return CapsuleView(capsule, level, ((1, "2 hairballs from 12 failures"),), len(LOG))
        lines = tuple(enumerate(LOG, 1))
        return CapsuleView(capsule, level, lines[:5] if level is Level.L2 else lines, len(LOG))

    def doctor(self) -> DoctorReport:
        self.calls.append("doctor")
        return self.home_snapshot.report

    def apply_fix(self, fix: Fix) -> str:
        self.applied.append(fix)
        return "claude: on"

    def copy(self, text: str) -> bool:
        self.copied.append(text)
        return True

    engines: tuple[tuple[str, bool], ...] = (("claude", True), ("codex", False), ("opencode", True))
    events: list[EngineEvent] = field(default_factory=pipeline_events)
    block: bool = False
    released: threading.Event = field(default_factory=threading.Event)
    requests: list[MandateRequest] = field(default_factory=list)
    stops: int = 0
    evidence_files: dict[str, str] = field(default_factory=dict)

    def mandate_setup(self) -> MandateSetup:
        return MandateSetup(
            self.engines,
            "claude",
            ("claude-haiku-4-5", "claude-sonnet-5", "gpt-5"),
            0.0,
            self.forge_ready,
            1.59,
        )

    def failure_evidence(self) -> tuple[str, int]:
        return "AssertionError: [total] expected 10 got 12", 2

    def read_evidence(self, path: str) -> str:
        if path not in self.evidence_files:
            raise FileNotFoundError(2, "No such file or directory", path)
        return self.evidence_files[path]

    def preview_mandate(
        self, request: MandateRequest, signatures: int, options: MandateOptions
    ) -> MandatePreview:
        self.requests.append(request)
        engine = options.engine or "claude"
        return MandatePreview(
            f"=== REQUEST ===\nTYPE: {request.type}\nWHAT: {request.what}",
            f'{engine} -p "<prompt>" --output-format stream-json',
            engine,
            "normal",
            0.72,
        )

    def run_mandate(
        self,
        request: MandateRequest,
        signatures: int,
        options: MandateOptions,
        observer: Callable[[EngineEvent], None],
        progress: Callable[[ProgressEvent], None],
    ) -> MandateReport:
        self.requests.append(request)
        progress(Note(Status.INFO, "pounce started"))
        if self.block:
            observer(self.events[0])
            observer(self.events[1])
            self.released.wait(10)
            return mandate_report(ok=False)
        for event in self.events:
            observer(event)
        return mandate_report()

    def stop_mandate(self) -> bool:
        self.stops += 1
        self.released.set()
        return True

    forge_ready: bool = True
    results: dict[str, ResultView] = field(
        default_factory=lambda: {"01JMANDATE0000000000000RUN1": sample_result()}
    )
    saved_results: list[str] = field(default_factory=list)
    exported_results: list[str] = field(default_factory=list)

    def result_view(self, run_id: str) -> ResultView | None:
        return self.results.get(run_id)

    def save_result(self, run_id: str) -> str:
        self.saved_results.append(run_id)
        return "docs/investigations/2026-09-24-the-landing-page.md"

    def export_result(self, run_id: str) -> str:
        self.exported_results.append(run_id)
        return f".cuanta/runs/{run_id}/run-report.md"

    def result_file(self, run_id: str, path: str) -> RunFile:
        return RunFile(path, "export const hero = 1;\n", "export const hero = 2;\n")

    empty_ledger: bool = False
    exports: list[tuple[str, str, str]] = field(default_factory=list)
    raw_exports: list[bool] = field(default_factory=list)
    imports: int = 0
    reindexed: int = 0

    def recent_runs(self) -> tuple[Run, ...]:
        return fixture_ledger(self.empty_ledger).runs()

    def spectrum(self, run_id: str) -> SpectrumResult:
        ledger = fixture_ledger(self.empty_ledger)
        selection = (
            Selection(since=ALL_SESSIONS) if run_id == ALL_IMPORTED else Selection(run=run_id)
        )
        return SpectrumQuery(ledger, load_prices()).run(selection)

    def import_sessions(self) -> dict[str, int]:
        self.imports += 1
        return {"claude": 12, "codex": 3}

    def reindex_graph(self) -> tuple[bool, str]:
        self.reindexed += 1
        return True, "graph.json updated"

    def export_ledger(self, fmt: str, table: str, path: str, include_raw: bool) -> tuple[str, int]:
        self.exports.append((fmt, table, path))
        self.raw_exports.append(include_raw)
        return f"/work/shop/{path}", 2048

    init_calls: list[tuple[InitOptions, bool, float | None]] = field(default_factory=list)
    new_files: dict[str, tuple[str, str]] = field(
        default_factory=lambda: {
            ".claude/agents/tester.new.md": (
                "# tester\nrun pytest\nkeep fixtures small\n",
                "# tester\nrun pytest -q\nkeep fixtures small\nreport hairballs\n",
            )
        }
    )
    resolved: list[tuple[str, bool]] = field(default_factory=list)
    telemetry_on: set[str] = field(default_factory=set)
    listener_running: bool = False
    listener_mode: str = "auto"
    listener_calls: list[str] = field(default_factory=list)
    catalog: tuple[ModelEntry, ...] = field(default_factory=lambda: CATALOG)
    tier_changes: list[tuple[str, str]] = field(default_factory=list)
    probes: list[tuple[str, bool]] = field(default_factory=list)
    policy: RoutingPolicy = field(default_factory=RoutingPolicy)
    routing_saved: dict[str, object] = field(default_factory=dict)
    stats: tuple[RoleStats, ...] = field(default_factory=lambda: ROLE_STATS)
    layout: str = "guided"
    last: str = ""
    stories: dict[str, str] = field(default_factory=dict)
    understood: list[str] = field(default_factory=list)
    team_options: list[MandateOptions] = field(default_factory=list)
    similar: tuple[Run, ...] = field(default_factory=lambda: SIMILAR_RUNS)
    onboarded: bool = True
    checks: list[str] = field(default_factory=list)
    improvements: list[bool] = field(default_factory=list)
    launched_options: list[MandateOptions] = field(default_factory=list)
    backend: str = "heuristic"
    consented: set[str] = field(default_factory=set)
    saved: dict[str, object] = field(default_factory=dict)
    gate_open: bool = True
    loop_runs: int = 0

    def telemetry_plan(self, engine: str) -> tuple[WiringPlan, ...]:
        return (
            WiringPlan(
                "claude",
                "/work/shop/.claude/settings.local.json",
                "/work/shop/.cuanta/backups/claude-settings.local.json.bak",
                True,
            ),
        )

    def run_init(
        self,
        options: InitOptions,
        consent: bool,
        budget_usd: float | None,
        progress: Callable[[ProgressEvent], None],
    ) -> InitReport:
        self.init_calls.append((options, consent, budget_usd))
        context = InitContext(detection=DETECTION, dry_run=options.dry_run)
        stages = []
        for key in STAGES:
            progress(started(key, msg(f"stage.{key}")))
            status = Status.SKIP if key == "telemetry" and not consent else Status.OK
            result = stage(
                status, "stage.planned" if options.dry_run else "stage.run", run=key.upper()
            )
            progress(finished(key, status, result.message or msg("stage.planned")))
            stages.append((key, result))
        progress(note(Status.INFO, msg("forge.phase", phase="4")))
        if options.dry_run:
            context.planned.extend(
                [
                    msg("plan.write", path="CLAUDE.md"),
                    msg("plan.write", path=".claude/agents/tester.md"),
                ]
            )
        else:
            context.registration_message = msg("registration.ok")
            context.verify_lines.extend(
                [
                    Finding(Status.OK, msg("verify.agents_ok")),
                    Finding(Status.OK, msg("verify.phases_ok")),
                ]
            )
            context.new_files.extend(self.new_files)
        return InitReport(context, tuple(stages), None)

    def load_new_file(self, path: str) -> NewFilePair:
        mine, new = self.new_files[path]
        return NewFilePair(path, original_of(path), tuple(side_by_side(mine, new)))

    def resolve_new_file(self, path: str, keep_mine: bool) -> str:
        self.resolved.append((path, keep_mine))
        self.new_files.pop(path, None)
        return original_of(path)

    def telemetry_panel(self) -> TelemetryPanel:
        engines = []
        for plan in self.telemetry_plan("all"):
            state = WiringState.ON if plan.engine in self.telemetry_on else WiringState.OFF
            engines.append((WiringReport(plan.engine, state, path=plan.target), plan))
        codex = WiringPlan("codex", "/home/u/.codex/config.toml", "", False)
        engines.append((WiringReport("codex", WiringState.UNAVAILABLE, path=codex.target), codex))
        return TelemetryPanel(self.listener_running, 4318, 1234, tuple(engines))

    def set_telemetry(self, engine: str, on: bool) -> tuple[WiringReport, ...]:
        if on:
            self.telemetry_on.add(engine)
        else:
            self.telemetry_on.discard(engine)
        state = WiringState.ON if on else WiringState.OFF
        return (WiringReport(engine, state),)

    def listener_start(self) -> int:
        self.listener_calls.append("start")
        self.listener_running = True
        return 4318

    def listener_stop(self) -> bool:
        self.listener_calls.append("stop")
        self.listener_running = False
        return True

    def instinct_overview(self) -> tuple[tuple[BackendStatus, ...], tuple[Decision, ...]]:
        statuses = tuple(
            BackendStatus(
                name,
                name != "jev",
                msg("instinct.heuristic_ready")
                if name == "heuristic"
                else msg("instinct.claude_missing"),
                name != "heuristic",
                name in self.consented,
                name == self.backend,
            )
            for name in ("heuristic", "jev", "llm")
        )
        decision = Decision(
            "r1",
            "heuristic",
            "choose",
            "How large is this mandate: trivial, normal or complex?",
            "",
            "normal",
            0.7,
            3,
        )
        return statuses, (decision,)

    def use_backend(self, name: str, consent: bool) -> None:
        self.backend = name
        if consent:
            self.consented.add(name)

    def probe_instinct(self) -> list[ProbeRow]:
        return [
            ProbeRow(
                "choose",
                "How big is this change: trivial, normal or complex?",
                msg("probe.choice", option=option_message("normal"), p="0.70"),
                3,
                0.0,
                self.backend,
            )
        ]

    def settings(self) -> Config:
        return Config(
            port=4318,
            budget_usd=5.0,
            exclusions=("dist",),
            background=self.background,
            terminal_tip_dismissed=self.tip_dismissed,
            listener_mode=self.listener_mode,
            mandate_layout=self.layout,
            onboarded=self.onboarded,
        )

    def save_setting(self, dotted: str, value: object) -> None:
        self.saved[dotted] = value

    def models_view(self, refresh: bool) -> CatalogView:
        self.calls.append("models_refresh" if refresh else "models")
        return CatalogView(
            self.catalog, "2026-09-23T12:00:00Z", 1, "2026-09-23", ("claude", "codex"), True
        )

    def set_model_tier(self, model: str, tier: str) -> ModelEntry:
        self.tier_changes.append((model, tier))
        found = next(entry for entry in self.catalog if entry.key == model)
        updated = replace(found, tier=Tier(tier), tier_source=TierSource.OVERRIDE)
        self.catalog = tuple(updated if entry.key == model else entry for entry in self.catalog)
        return updated

    def probe_model(self, model: str, spend: bool) -> ProbeOutcome:
        self.probes.append((model, spend))
        entry = next(item for item in self.catalog if item.key == model)
        if not spend:
            return ProbeOutcome(entry, 0.00026, None, None)
        return ProbeOutcome(entry, 0.00026, True, 0.00031)

    def routing_policy(self) -> RoutingPolicy:
        return self.policy

    def save_routing(self, values: Mapping[str, object]) -> None:
        self.routing_saved = dict(values)

    def routing_stats(self) -> tuple[RoleStats, ...]:
        return self.stats

    def latest_bench(self) -> BenchResult | None:
        return self.bench

    def jev_card(self, test: bool) -> JevCard:
        card = JevCard(
            True, "https://openrouter.ai/api/v1/systemone", "jev-latest", None, 0.0123, 42
        )
        if not test:
            return card
        status = msg(
            "instinct.connected", model="typesafe/jev-1.13", provider="TypeSafe", latency=212
        )
        return replace(card, model="typesafe/jev-1.13", latency_ms=212, status=status, ok=True)

    def assistant_check(self, request: MandateRequest) -> Clarity:
        self.checks.append(request.what)
        return Clarity(heuristic_clarity(request), chips_from(heuristic_gaps(request)), "heuristic")

    def assistant_preview(self, request: MandateRequest) -> str:
        return json.dumps(sent_payload(request, remote=True), indent=2)

    def suggestions(self, request: MandateRequest) -> Suggestions:
        return Suggestions(
            ("src/shop/cart.py", "src/shop/tax.py"),
            ("tests/test_cart.py",),
            ("The public API of Cart", "No comments / no docstrings"),
            EXAMPLES.get(request.type),
        )

    def improve(self, request: MandateRequest, spend: bool) -> Improvement:
        self.improvements.append(spend)
        if not spend:
            return Improvement(0.0004, "claude:haiku")
        proposal = replace(request, what=f"{request.what} (clear)", tests="the cart test passes")
        return Improvement(
            0.0004, "claude:haiku", proposal, changes(request, proposal), 0.0005, True
        )

    def team_plan(
        self, request: MandateRequest, options: MandateOptions
    ) -> tuple[RoutePlan, Estimate]:
        self.team_options.append(options)
        policy = RoutingPolicy(engines=(options.engine or "claude",))
        requests = default_requests(policy, roles_that_run(request.type, options.simple))
        routes = plan_route(policy, self.catalog, requests)
        plan = RoutePlan(policy, None, None, (), routes, "heuristic")
        cap = resolve_budget(options, request.type, 0.0)
        return plan, estimate(plan, self.similar, load_prices(), request.type, options.depth, cap)

    def understand(self, story: str) -> Understanding:
        self.understood.append(story)
        decisions = DecisionMaker(HeuristicInstinct(), MemoryLedger(), lambda: FIXED_TIME)
        service = IntakeService(decisions, 0.6, lambda facts, request: PLACES)
        return service.understand(story)

    def last_story(self) -> str:
        return self.last

    def drafts(self, current: str) -> tuple[Draft, ...]:
        return tuple(
            Draft(draft_id, story, f"{FIXED_TIME}{index:03d}")
            for index, (draft_id, story) in enumerate(self.stories.items())
            if draft_id != current
        )[::-1]

    def autosave(self, draft_id: str, story: str) -> None:
        if story.strip():
            self.stories[draft_id] = story
        else:
            self.stories.pop(draft_id, None)

    def launched(self, draft_id: str, story: str) -> None:
        self.stories.pop(draft_id, None)
        self.last = story

    def load_draft(self, draft_id: str) -> str:
        return self.stories.get(draft_id, "")

    def instinct_preview(self) -> str:
        return '{"kind": "scope", "type": "bug", "what": "fix the login timeout"}'

    def loop_state(self) -> LoopState:
        if self.gate_open:
            return LoopState(LoopGate(True, ""), "strong", 3, 5.0)
        missing = "VERIFY_TIER=moderate (pytest); the loop needs strong"
        return LoopState(LoopGate(False, missing), "moderate", 3, 5.0)

    def run_loop(
        self, max_iterations: int, budget_usd: float, progress: Callable[[ProgressEvent], None]
    ) -> LoopReport:
        self.loop_runs += 1
        progress(StepStarted("test-0", "test"))
        progress(StepFinished("test-0", Status.OK, "green"))
        return LoopReport("loop1", (), StopReason.GREEN, 0.0, "green")

    background: str = "solid"
    tip_dismissed: bool = False

    def terminal_report(self) -> TerminalReport:
        return TerminalReport(TerminalKind.MODERN, msg("terminal.no_evidence"))
