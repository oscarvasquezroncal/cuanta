from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from cuanta.adapters.instinct.heuristic import HeuristicInstinct
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.adapters.system.config_files import set_value
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.engine_run import EngineLauncher
from cuanta.application.instinct import DecisionMaker, DecisionScope
from cuanta.application.mandate import MandateService
from cuanta.application.mandate_flow import MandateFlow, MandateOptions
from cuanta.application.progress import RecordingSink
from cuanta.application.results import ResultQuery, run_markdown
from cuanta.application.run_reports import RunReports
from cuanta.bootstrap import Container
from cuanta.domain.detection import GraphMode, Stack
from cuanta.domain.engine import EngineEvent, EngineOutcome, EngineRequest, RunResult
from cuanta.domain.errors import DomainFailure
from cuanta.domain.ledger import Decision, Run
from cuanta.domain.mandate import REPORT_LANGUAGE, SIMPLE_INVESTIGATION_BLOCK, MandateRequest
from cuanta.ports.engine import Engine
from cuanta.tui.services import ContainerServices

TEMPLATE = (
    Path(__file__).parents[2]
    / "src/cuanta/assets/forge/skills/agent-system-init/templates/MANDATE_TEMPLATE.template.md"
)
REQUEST = MandateRequest(
    type="investigation",
    what="how does the landing page load its fonts",
    why="the page flashes on load",
    out_of_scope="the page content",
)
LONG_REPORT = (
    "## SUMMARY\n" + ("finding at src/app/page.tsx:12\n" * 3000) + "## NEXT\n- fix fonts\n"
)


def test_result_query_loads_recorded_fallback_reason(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run(id="R", kind="mandate", engine="claude"))
    ledger.add_decision(
        Decision(
            run_id="R",
            backend="heuristic",
            primitive="choose",
            question="q",
            options='["a"]',
            answer="a",
            confidence=1.0,
            latency_ms=1,
            fallback_error="jev answered HTTP 503",
            fallback_from="jev",
        )
    )
    view = ResultQuery(LocalWorkspace(tmp_path), ledger, date.today).load("R")
    assert view is not None
    assert (view.fallback_from, view.fallback_error) == ("jev", "jev answered HTTP 503")


def test_tui_intake_uses_a_linkable_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    set_value(tmp_path / ".cuanta" / "config.toml", "instinct.backend", "jev")
    set_value(tmp_path / ".cuanta" / "config.toml", "instinct.consent", ["jev"])
    understood = ContainerServices(tmp_path).understand("Investigate how the landing works")
    assert understood.intake_scope.startswith("intake:")
    assert understood.fallback_from == "jev"
    container = Container.for_project(tmp_path)
    ledger = container.ledger()
    try:
        stored = ledger.decisions()
    finally:
        container.close()
    assert stored
    assert all(item.request_hash == understood.intake_scope and item.preview for item in stored)


def test_edited_confirm_links_intake_fallback_to_result(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    token = "intake:token"
    ledger.add_decision(
        Decision(
            run_id="",
            backend="heuristic",
            primitive="choose",
            question="q",
            options='["a"]',
            answer="a",
            confidence=1.0,
            latency_ms=1,
            preview=True,
            request_hash=token,
            fallback_error="jev answered HTTP 503",
            fallback_from="jev",
        )
    )
    engine = ReportingEngine("## SUMMARY\nok\n")
    flow = build(tmp_path, False, ledger, engine)
    edited = MandateRequest(
        type="investigation",
        what="edited after intake",
        why="How does the landing load fonts?",
        out_of_scope="no changes",
    )
    prepared = flow.prepare(edited, 0, MandateOptions(simple=True, intake_scope=token))
    flow.run(prepared, RecordingSink(), verdict=False)
    view = ResultQuery(LocalWorkspace(tmp_path), ledger, date.today).load(prepared.spec.run_id)
    assert view is not None
    assert (view.fallback_from, view.fallback_error) == ("jev", "jev answered HTTP 503")


class ReportingEngine:
    def __init__(self, text: str, result: RunResult | None = None) -> None:
        self.text = text
        self.result = result
        self.prompts: list[str] = []

    @property
    def name(self) -> str:
        return "claude"

    def available(self) -> bool:
        return True

    def version(self) -> str:
        return "9.9.9"

    def missing_flags(self) -> tuple[str, ...]:
        return ()

    def cancel(self) -> None:
        return None

    def command(self, request: EngineRequest) -> list[str]:
        return ["claude", "-p"]

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        self.prompts.append(request.prompt)
        result = self.result or RunResult(True, "success", 0.14, 1, "s", (), self.text)
        on_event(result)
        return EngineOutcome(0, result, 0)


TAIL = "Prefer graph queries and search over reading whole files."


def build(
    tmp_path: Path,
    agents: bool,
    ledger: MemoryLedger,
    engine: ReportingEngine,
    graph_mode: GraphMode = GraphMode.CLI,
    default_max_turns: int = 0,
) -> MandateFlow:
    workspace = LocalWorkspace(tmp_path)
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "MANDATE_TEMPLATE.md").write_text(
        TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    clock = FixedClock()
    scope = DecisionScope()
    decisions = DecisionMaker(HeuristicInstinct(), ledger, clock.now_iso, None, scope)
    service = MandateService(workspace, ledger, decisions, clock.now_iso)
    counter = itertools.count(1)

    def new_run_id() -> str:
        return f"01RUN{next(counter):021d}"

    def launcher(chosen: Engine) -> EngineLauncher:
        return EngineLauncher(
            chosen,
            ledger,
            clock,
            new_run_id,
            lambda size: b"\x01" * size,
            "landing",
            4318,
            None,
            reports=RunReports(workspace),
        )

    return MandateFlow(
        service=service,
        engines=lambda name: engine,
        launchers=launcher,
        stack=Stack,
        summarize=lambda run_id: ({"main": 53_107}, None),
        cwd=str(tmp_path),
        default_engine="claude",
        default_budget=1.0,
        default_max_turns=default_max_turns,
        has_agents=lambda: agents,
        scope=scope,
        new_run_id=new_run_id,
        graph_mode=lambda: graph_mode,
    )


def test_broken_graph_removes_queries_and_permission(tmp_path: Path) -> None:
    flow = build(
        tmp_path,
        True,
        MemoryLedger(),
        ReportingEngine("## SUMMARY\nok\n"),
        GraphMode.BROKEN,
    )
    prepared = flow.prepare(REQUEST, 0, MandateOptions(depth="quick"))
    assert "graphify" not in prepared.composed.prompt.lower()
    assert "graph" not in prepared.spec.append_system_prompt.lower()
    assert "Bash(graphify *)" not in prepared.spec.allowed_tools
    assert prepared.spec.tools == ("Read", "Grep", "Glob")
    assert "Prefer search over reading whole files" in prepared.spec.append_system_prompt
    assert REPORT_LANGUAGE in prepared.composed.prompt


def test_investigations_ask_for_the_report_in_the_request_language(tmp_path: Path) -> None:
    flow = build(tmp_path, True, MemoryLedger(), ReportingEngine("## SUMMARY\nok\n"))
    for options in (
        MandateOptions(),
        MandateOptions(shape="pipeline"),
        MandateOptions(simple=True),
    ):
        prepared = flow.prepare(REQUEST, 0, options)
        assert REPORT_LANGUAGE in prepared.composed.prompt
        assert "same language" not in prepared.spec.append_system_prompt


def test_default_and_pipeline_results_record_their_shape(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    flow = build(tmp_path, True, ledger, ReportingEngine("## SUMMARY\nok\n"))
    query = ResultQuery(LocalWorkspace(tmp_path), ledger, date.today)
    for options, expected in (
        (MandateOptions(), "single"),
        (MandateOptions(shape="pipeline"), "pipeline"),
    ):
        report = flow.run(flow.prepare(REQUEST, 0, options), RecordingSink(), verdict=False)
        meta_path = tmp_path / RunReports(LocalWorkspace(tmp_path)).meta_path(report.run.id)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        view = query.load(report.run.id)
        assert meta["shape"] == expected
        assert view is not None
        assert view.single is (expected == "single")
        assert view.simple is False
        assert expected.replace("single", "single context") in run_markdown(view)


def test_legacy_mandate_shape_uses_investigation_handoffs(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    reports = RunReports(LocalWorkspace(tmp_path))
    ledger.add_run(Run("R", "mandate", "claude"))
    ledger.add_run(Run("P", "mandate", "claude"))
    ledger.add_run(Run("S", "mandate", "claude"))
    ledger.add_run(Run("F", "forge", "claude"))
    reports.save_meta("R", {"task_type": "investigation", "handoffs": []})
    reports.save_meta("P", {"task_type": "investigation", "handoffs": ["architecture-analyst"]})
    reports.save_meta("S", {"task_type": "investigation", "simple": True})
    reports.save_meta("F", {"task_type": "investigation", "handoffs": []})
    query = ResultQuery(LocalWorkspace(tmp_path), ledger, date.today)
    assert (unknown := query.load("R")) is not None
    assert not unknown.shape_known
    assert "unknown shape" in run_markdown(unknown)
    assert (pipeline := query.load("P")) is not None
    assert pipeline.shape_known and not pipeline.single
    assert (simple := query.load("S")) is not None
    assert simple.shape_known and simple.single
    assert (forge := query.load("F")) is not None and not forge.shape_known


def test_raw_report_is_stored_and_displayed_without_its_preamble(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    raw = "I have enough evidence.\n\n## SUMMARY\nfonts load twice\n\n## NEXT STEP\n- fix fonts\n"
    flow = build(tmp_path, False, ledger, ReportingEngine(raw))
    report = flow.run(
        flow.prepare(REQUEST, 0, MandateOptions(simple=True)), RecordingSink(), verdict=False
    )
    assert (tmp_path / report.report_path).read_text(encoding="utf-8") == raw
    assert report.text.startswith("## SUMMARY")
    query = ResultQuery(LocalWorkspace(tmp_path), ledger, date.today)
    view = query.load(report.run.id)
    assert view is not None
    assert view.text.startswith("## SUMMARY")
    assert "preamble" not in [section.key for section in view.sections]
    assert (
        (tmp_path / query.save_to_docs(view)).read_text(encoding="utf-8").startswith("## SUMMARY")
    )
    assert "I have enough evidence" not in (tmp_path / query.export_markdown(view)).read_text(
        encoding="utf-8"
    )


def test_broken_graph_removes_template_commands_for_feature(tmp_path: Path) -> None:
    flow = build(
        tmp_path,
        True,
        MemoryLedger(),
        ReportingEngine("ok"),
        GraphMode.BROKEN,
    )
    request = MandateRequest(
        type="feature", what="add export", tests="export is covered", out_of_scope="settings"
    )
    prepared = flow.prepare(request, 0, MandateOptions())
    assert "graphify" not in prepared.composed.prompt.lower()
    assert "Ignore older graph instructions" in prepared.composed.prompt
    assert "Bash(graphify *)" not in prepared.spec.allowed_tools


def test_the_pipeline_template_never_runs_without_forge_agents(tmp_path: Path) -> None:
    flow = build(tmp_path, False, MemoryLedger(), ReportingEngine("x"))
    with pytest.raises(DomainFailure, match="no Forge agents") as caught:
        flow.prepare(REQUEST, 0, MandateOptions())
    assert "simple mode" in caught.value.hint


def test_simple_mode_runs_one_agent_and_keeps_the_whole_report(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    engine = ReportingEngine(LONG_REPORT)
    flow = build(tmp_path, False, ledger, engine)
    prepared = flow.prepare(REQUEST, 0, MandateOptions(simple=True))
    assert prepared.composed.simple
    assert prepared.composed.prompt.startswith(SIMPLE_INVESTIGATION_BLOCK.split("\n", 1)[0])
    assert "architecture-analyst" not in prepared.composed.prompt
    assert "Write" not in prepared.spec.allowed_tools
    assert "Agent" not in prepared.spec.allowed_tools
    assert {"Write", "Edit"} <= set(prepared.spec.disallowed_tools)
    report = flow.run(prepared, RecordingSink(), verdict=False)
    assert report.simple
    stored = (tmp_path / report.report_path).read_text(encoding="utf-8")
    assert stored.rstrip("\n") == LONG_REPORT.rstrip("\n")
    assert len(stored) > 90_000
    view = ResultQuery(LocalWorkspace(tmp_path), ledger, lambda: date(2026, 9, 24)).load(
        report.run.id
    )
    assert view is not None
    assert view.simple
    assert view.task_type == "investigation"
    assert view.follow_up is not None and view.follow_up.what == "fix fonts"
    saved = ResultQuery(LocalWorkspace(tmp_path), ledger, lambda: date(2026, 9, 24)).save_to_docs(
        view
    )
    assert saved == "docs/investigations/2026-09-24-finding-at-src-app-page-tsx-12.md"
    assert (tmp_path / saved).read_text(encoding="utf-8").startswith("## SUMMARY")


def test_decisions_are_linked_to_the_launched_run(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    engine = ReportingEngine("## SUMMARY\nok\n## NEXT\n- none\n")
    flow = build(tmp_path, True, ledger, engine)
    flow.prepare(REQUEST, 0, MandateOptions(), preview=True)
    flow.prepare(REQUEST, 0, MandateOptions(), preview=True)
    previews = ledger.decisions()
    assert len(previews) == 1
    assert previews[0].preview
    assert previews[0].run_id == ""
    prepared = flow.prepare(REQUEST, 0, MandateOptions())
    run_id = prepared.spec.run_id
    assert run_id
    report = flow.run(prepared, RecordingSink(), verdict=False)
    assert report.run.id == run_id
    linked = ledger.decisions(run_id=run_id)
    assert len(linked) == 2
    assert {decision.preview for decision in linked} == {True, False}
    assert all(decision.outcome for decision in linked)
    assert all(decision.run_id == run_id for decision in ledger.decisions())


def test_a_pipeline_investigation_is_read_only_and_uses_the_analyst(tmp_path: Path) -> None:
    flow = build(tmp_path, True, MemoryLedger(), ReportingEngine("## SUMMARY\nok\n"))
    prepared = flow.prepare(REQUEST, 0, MandateOptions(shape="pipeline"))
    prompt = prepared.composed.prompt
    assert prompt.startswith("# MANDATE — investigation (read-only)")
    assert "architecture-analyst agent ONCE" in prompt
    assert "## FINDINGS" in prompt
    assert set(prepared.spec.allowed_tools) == {
        "Read",
        "Grep",
        "Glob",
        "Bash(graphify *)",
        "Agent",
        "Task",
    }
    assert "Write" in prepared.spec.disallowed_tools
    assert prepared.spec.tools is None
    assert prepared.spec.append_system_prompt == ""


def test_an_investigation_runs_in_one_context_by_default(tmp_path: Path) -> None:
    flow = build(tmp_path, True, MemoryLedger(), ReportingEngine("## SUMMARY\nok\n"))
    prepared = flow.prepare(REQUEST, 0, MandateOptions(depth="quick"))
    prompt = prepared.composed.prompt
    spec = prepared.spec
    assert prompt.startswith("# MANDATE — investigation (read-only, one context)")
    assert "ONCE" not in prompt
    assert "READ BUDGET" not in prompt
    assert set(spec.allowed_tools) == {"Read", "Grep", "Glob", "Bash(graphify *)"}
    assert spec.tools == ("Read", "Grep", "Glob", "Bash")
    assert {"Agent", "Task", "Write", "Edit"} <= set(spec.disallowed_tools)
    assert spec.agents_file == ""
    assert spec.effort == "low"
    assert spec.append_system_prompt.startswith("You are the codebase analyst")
    assert "not in JSON" in spec.append_system_prompt
    assert spec.append_system_prompt.endswith("open at most 8 files. " + TAIL)
    assert flow.prepare(REQUEST, 0, MandateOptions()).spec.effort == "medium"


def test_depth_sets_the_cap_the_read_budget_and_the_run_labels(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    flow = build(tmp_path, True, ledger, ReportingEngine("## SUMMARY\nok\n"))
    prepared = flow.prepare(REQUEST, 0, MandateOptions(depth="quick"))
    assert prepared.spec.max_budget_usd == 0.25
    assert prepared.spec.max_turns == 20
    assert prepared.spec.task_type == "investigation"
    assert prepared.spec.depth == "quick"
    assert "READ BUDGET (quick): open at most 8 files" in prepared.spec.append_system_prompt
    piped = flow.prepare(REQUEST, 0, MandateOptions(depth="quick", shape="pipeline"))
    assert "READ BUDGET (quick): open at most 8 files" in piped.composed.prompt
    capped = flow.prepare(REQUEST, 0, MandateOptions(depth="quick", budget_usd=0.1))
    assert capped.spec.max_budget_usd == 0.1
    unlimited = flow.prepare(REQUEST, 0, MandateOptions(depth="deep", no_cap=True))
    assert unlimited.spec.max_budget_usd == 0.0
    assert unlimited.spec.max_turns == 80
    flow.run(flow.prepare(REQUEST, 0, MandateOptions(depth="deep")), RecordingSink())
    run = ledger.runs(kind="mandate")[0]
    assert (run.task_type, run.depth) == ("investigation", "deep")


def test_turn_limit_precedence_and_ledger_end_reason(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    flow = build(tmp_path, True, ledger, ReportingEngine("## SUMMARY\nok\n"))
    quick = flow.prepare(REQUEST, 0, MandateOptions(depth="quick"))
    assert quick.spec.max_turns == 20
    assert flow.prepare(REQUEST, 0, MandateOptions(depth="quick", max_turns=5)).spec.max_turns == 5
    assert flow.prepare(REQUEST, 0, MandateOptions(engine="codex")).spec.max_turns == 0
    bug = MandateRequest(
        type="bug", what="fix add", why="wrong sum", tests="add test", out_of_scope="fonts"
    )
    ordinary = flow.prepare(bug, 0, MandateOptions())
    assert ordinary.spec.max_turns == 40
    assert ordinary.spec.effort == ""
    assert ordinary.spec.max_budget_usd == 1.0
    configured = build(
        tmp_path, True, ledger, ReportingEngine("## SUMMARY\nok\n"), default_max_turns=30
    )
    assert configured.prepare(REQUEST, 0, MandateOptions(depth="quick")).spec.max_turns == 30
    successful = flow.run(quick, RecordingSink(), verdict=False)
    assert (successful.run.max_turns, successful.run.turns, successful.run.end_reason) == (
        20,
        1,
        "success",
    )

    limited = ReportingEngine(
        "## SUMMARY\ncut\n## NEXT\n- retry\n",
        RunResult(False, "error_max_turns", 0.14, 80, "s", (), "## SUMMARY\ncut\n"),
    )
    limited_ledger = MemoryLedger()
    limited_flow = build(tmp_path, True, limited_ledger, limited)
    stopped = limited_flow.run(
        limited_flow.prepare(REQUEST, 0, MandateOptions(depth="deep")),
        RecordingSink(),
        verdict=False,
    )
    assert (stopped.run.max_turns, stopped.run.turns, stopped.run.end_reason) == (
        80,
        80,
        "error_max_turns",
    )
    assert stopped.run.status == "failed"
    view = ResultQuery(LocalWorkspace(tmp_path), limited_ledger, date.today).load(stopped.run.id)
    assert view is not None
    assert "Turns: 80/80 · cut by turn limit" in run_markdown(view)
