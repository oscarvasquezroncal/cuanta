from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from cuanta.adapters.storage.capsule_store import FileCapsuleStore
from cuanta.application import cross_engine
from cuanta.application.cross_engine import CompletionState, CrossReport, cross_metrics
from cuanta.application.engine_run import EngineLauncher, LaunchSpec
from cuanta.application.forecast import PlannedForecast
from cuanta.application.mandate import MandateReport, MandateService, report_payload
from cuanta.application.mandate_flow import (
    MandateFlow,
    MandateOptions,
    Prepared,
    has_scout,
    per_role_run,
)
from cuanta.application.route_apply import Applied
from cuanta.application.routing import RoutePlan
from cuanta.application.scout import read_budget
from cuanta.application.steering import GovernorSetup, Steering
from cuanta.domain.agents import AgentsPlan
from cuanta.domain.change_plan import ChangePlan, EditTarget
from cuanta.domain.detection import Stack
from cuanta.domain.engine import AssistantText, EngineEvent, ToolCall
from cuanta.domain.envelope import (
    SCOUT_SHAPE,
    Buckets,
    FixedPrefix,
    RoleForecast,
    StopRules,
)
from cuanta.domain.evidence_pack import (
    PACK_TOKENS,
    EvidencePack,
    check_pack,
    parse_pack,
    scope_plan,
)
from cuanta.domain.governor import role_plan
from cuanta.domain.instinct import Choice
from cuanta.domain.ledger import Run
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import msg
from cuanta.domain.models import ModelEntry, Tier
from cuanta.domain.role_budgets import role_split
from cuanta.domain.routing import SCOUT_ROLES, Provider, Role, RoleRoute, RoutingPolicy
from cuanta.domain.sandbox import SandboxLaunch
from cuanta.domain.scout import DocsChoice, DocsMode, DocsReason, ScoutMode
from cuanta.domain.scout_report import parse_scout
from cuanta.ports.engine import Engine
from cuanta.ports.progress import ProgressSink
from tests.unit.test_cross_handoffs import FIX, REQUEST, Act, Harness, Recorder, lines, seed

DOCS_REQUEST = MandateRequest(
    type="feature",
    what="Add the canonical and update the README",
    why="seo",
    out_of_scope="secrets",
)
SENIOR_DONE = '{"summary": "edited", "plan": {"edit": ["src/layout.ts"]}, "status": "done"}'


def scout_plan(engine: str) -> RoutePlan:
    found = tuple(
        RoleRoute(
            role,
            Tier.ECONOMY if role is Role.SCOUT else Tier.STANDARD,
            Tier.ECONOMY if role is Role.SCOUT else Tier.STANDARD,
            ModelEntry(engine, f"model-{role.value}", "m", "p"),
            msg("route.policy", tier="standard"),
        )
        for role in SCOUT_ROLES
    )
    return RoutePlan(RoutingPolicy(engines=(engine,)), None, None, (), found, "heuristic")


def pack_text(**extra: object) -> str:
    data: dict[str, object] = {
        "summary": "layout needs a canonical",
        "facts": [{"path": "src/layout.ts", "start": 1, "end": 2, "claim": "metadata"}],
        "snippets": [{"path": "src/layout.ts", "start": 1, "end": 1, "text": "export const a"}],
        "risks": ["the head is shared"],
        "tests": ["tests/layout.test.ts"],
        "edit_set": ["src/layout.ts"],
        "status": "done",
    }
    data.update(extra)
    return "I explored the layout at length: SCOUT-TRANSCRIPT-MARKER.\n" + json.dumps(data)


def run(
    harness: Harness, engine: str = "codex", request: MandateRequest = REQUEST
) -> tuple[CrossReport, Recorder]:
    recorder = Recorder()
    report = harness.build().run(request, scout_plan(engine), recorder)
    return report, recorder


def notes(recorder: Recorder) -> str:
    return " ".join(recorder.texts())


def test_the_gpt_team_scouts_read_only_and_the_senior_gets_only_the_pack_and_edit_set(
    tmp_path: Path,
) -> None:
    seed(tmp_path)
    script = {
        "scout": [Act(pack_text(), 0.05)],
        "senior": [Act(SENIOR_DONE, 0.2, {"src/layout.ts": "export const a = 2\n"})],
    }
    harness = Harness(tmp_path, script)
    report, recorder = run(harness)
    assert report.ok and report.state is CompletionState.COMPLETE
    assert [step.role for step in report.steps] == [
        Role.SCOUT,
        Role.SENIOR,
        Role.TESTER,
        Role.DOCS,
    ]
    scout_request = harness.actors["codex"].requests[0]
    assert scout_request.read_only and scout_request.model == "model-scout"
    assert '"edit_set"' in harness.prompt_of("scout")[0]
    senior = harness.prompt_of("senior")[0]
    assert "=== EVIDENCE PACK FROM THE SCOUT (checked by cuanta) ===" in senior
    assert "- src/layout.ts:1-2 metadata" in senior
    assert "Edit set (the files you may edit):\n- src/layout.ts" in senior
    assert "Read budget: about 3 file reads." in senior
    assert "the evidence pack lists anchored facts" in senior
    assert "the chain lists anchored facts" not in senior
    assert "SCOUT-TRANSCRIPT-MARKER" not in senior
    assert "HANDOFF CHAIN FROM EARLIER ROLES" not in senior
    tester = harness.prompt_of("tester")[0]
    assert "=== CHANGES (from cuanta's snapshots) ===" in tester
    assert "+export const a = 2" in tester and "-export const a = 1" in tester
    assert "Run `cuanta test --affected`" in tester
    assert "- tests/layout.test.ts" in tester
    assert "- scout [" not in tester
    assert "- senior [codex/model-senior] done: edited" in tester
    docs = next(prompt for prompt in harness.prompts() if "You are the docs of" in prompt)
    assert "- scout [codex/model-scout] done: layout needs a canonical" in docs
    scout = report.scout
    assert scout is not None and scout.mode is ScoutMode.LAUNCH
    assert scout.check.pack.edit == ("src/layout.ts",) and not scout.check.over_budget
    stored = [path.read_text(encoding="utf-8") for path in (tmp_path / "capsules").glob("*.log")]
    assert any(text.startswith("Summary: layout needs a canonical") for text in stored)
    assert report.handoffs[0].capsule == scout.capsule
    assert f"cuanta cat {scout.capsule}" in notes(recorder)
    metrics = harness.saved[report.steps[0].run_id]
    summary = parse_scout(metrics["scout"])
    assert (summary.mode, summary.edit_set, summary.budget) == (
        "launch",
        ("src/layout.ts",),
        PACK_TOKENS,
    )
    assert summary.leaks_known and summary.leaked == ()
    assert "docs" not in metrics


def test_an_oversized_pack_is_trimmed_before_the_senior_and_keeps_its_edit_set(
    tmp_path: Path,
) -> None:
    seed(tmp_path)
    (tmp_path / "work" / "src" / "big.ts").write_text(
        "\n".join(f"const line{index} = '{'z' * 120}'" for index in range(200)), encoding="utf-8"
    )
    snippets = [
        {
            "path": "src/big.ts",
            "start": index * 10 + 1,
            "end": index * 10 + 40,
            "text": "made up " * 400,
        }
        for index in range(12)
    ]
    edit = [
        f"src/generated/component_number_{index:03d}_with_a_long_name.tsx" for index in range(500)
    ]
    text = pack_text(snippets=snippets)
    harness = Harness(tmp_path, {"scout": [Act(text, 0.05)]})
    report, recorder = run(harness)
    scout = report.scout
    parsed = parse_pack(text)
    assert scout is not None and parsed is not None
    expected = check_pack(parsed, lines(tmp_path / "work"))
    assert expected.dropped_snippets > 0
    assert scout.check.dropped_snippets == expected.dropped_snippets
    assert scout.check.dropped_facts == 0 and not scout.check.over_budget
    assert scout.check.tokens <= PACK_TOKENS < scout.check.raw_tokens
    senior = harness.prompt_of("senior")[0]
    kept = 12 - expected.dropped_snippets
    assert senior.count("--- src/big.ts") == kept
    assert "made up" not in senior and f"const line10 = '{'z' * 120}'" in senior
    assert "Edit set (the files you may edit):\n- src/layout.ts" in senior
    assert (
        f"evidence pack trimmed to fit: {expected.dropped_snippets} snippets and 0 facts dropped"
        in notes(recorder)
    )
    over = Harness(tmp_path / "over", {"scout": [Act(pack_text(edit_set=edit), 0.05)]})
    seed(tmp_path / "over")
    (tmp_path / "over" / "work" / "src" / "big.ts").write_text("x\n" * 200, encoding="utf-8")
    flooded, flood_notes = run(over)
    found = flooded.scout
    assert found is not None and found.check.over_budget
    assert found.check.pack.edit == tuple(edit)
    assert f"- {edit[-1]}" in over.prompt_of("senior")[0]
    assert "over its 6,000-token budget; the edit set is kept" in notes(flood_notes)


def test_edits_outside_the_edit_set_are_flagged_named_or_not(tmp_path: Path) -> None:
    seed(tmp_path)
    handoff = json.dumps(
        {"summary": "edited", "plan": {"edit": ["src/layout.ts", "src/named.ts"]}, "status": "done"}
    )
    writes = {"src/layout.ts": "x\n", "src/named.ts": "n\n", "src/stray.ts": "s\n"}
    script = {"scout": [Act(pack_text())], "senior": [Act(handoff, 0.2, writes)]}
    harness = Harness(tmp_path, script)
    report, recorder = run(harness)
    scout = report.scout
    assert scout is not None and scout.senior is not None
    assert scout.senior.outside.named == ("src/named.ts",)
    assert scout.senior.outside.unnamed == ("src/stray.ts",)
    text = notes(recorder)
    assert "senior edited files outside the edit set and named them: src/named.ts" in text
    assert (
        "senior edited files outside the edit set without naming them in its handoff: "
        "src/stray.ts" in text
    )
    summary = parse_scout(cross_metrics(report)["scout"])
    assert summary.outside_unnamed == ("src/stray.ts",)
    assert summary.outside_named == ("src/named.ts",)


def test_senior_reads_outside_the_pack_count_as_exploration_leak_for_the_governor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(tmp_path)
    (tmp_path / "work" / "src" / "other.ts").write_text("o\n", encoding="utf-8")
    seen: dict[Role, ChangePlan | None] = {}

    def spy(
        setup: GovernorSetup | None,
        launcher: EngineLauncher,
        spec: LaunchSpec,
        role: Role,
        share: float,
        forecast: PlannedForecast | None,
        progress: ProgressSink,
    ) -> Steering | None:
        seen[role] = spec.change_plan
        return None

    monkeypatch.setattr(cross_engine, "codex_steering", spy)
    reads: dict[str, tuple[tuple[str, int, int], ...]] = {
        "RUN1": (("src/layout.ts", 1, 2), ("src/other.ts", 1, 1))
    }
    edited = Act(SENIOR_DONE, 0.2, {"src/layout.ts": "y\n"})
    harness = Harness(tmp_path, {"scout": [Act(pack_text())], "senior": [edited]}, reads=reads)
    report, recorder = run(harness)
    scout = report.scout
    assert scout is not None and scout.senior is not None
    assert scout.senior.leaked == ("src/other.ts",)
    assert "senior read 1 files outside the pack and the edit set: src/other.ts" in notes(recorder)
    scope = seen[Role.SENIOR]
    assert scope is not None
    assert [target.path for target in scope.edit] == ["src/layout.ts"]
    forecast = RoleForecast(
        Role.SENIOR,
        "m",
        Buckets(),
        4,
        0.1,
        0.16,
        1.0,
        StopRules(6, 4, 100),
        FixedPrefix(1_000, False),
        1.0,
    )
    planned = role_plan(forecast, Provider.CODEX, 0.5, scope)
    assert set(planned.read_paths) == {"src/layout.ts"}
    wider = scope_plan(ChangePlan(), scout.check.pack)
    assert wider.edit == (EditTarget("src/layout.ts", 1.0, "scout edit set"),)
    explorer = seen.get(Role.SCOUT)
    assert explorer is not None and explorer.read_only


@pytest.mark.parametrize(
    ("request_", "reserve_key"),
    [(REQUEST, "repair budget:"), (FIX, "fix budget:")],
)
def test_docs_off_skips_docs_and_its_share_funds_the_repair_turn(
    tmp_path: Path, request_: MandateRequest, reserve_key: str
) -> None:
    seed(tmp_path)
    edited = Act(SENIOR_DONE, 0.2, {"src/layout.ts": "z\n"})
    script = {"scout": [Act(pack_text())], "senior": [edited]}
    harness = Harness(tmp_path, script, docs_mode=DocsMode.AUTO)
    report, recorder = run(harness, request=request_)
    assert [step.role for step in report.steps] == [Role.SCOUT, Role.SENIOR, Role.TESTER]
    assert report.state is CompletionState.COMPLETE
    docs = report.docs
    assert docs is not None and (docs.on, docs.reason) == (False, DocsReason.NOT_REQUESTED)
    expected = role_split(
        dict.fromkeys((Role.SCOUT, Role.SENIOR, Role.TESTER)), 2.0, False, docs_off=True
    )
    assert expected.from_docs and expected.repair_usd > 0
    text = notes(recorder)
    assert "Docs: off, the request does not ask for docs" in text
    assert f"{reserve_key} ${expected.repair_usd:.4f} is held for one repair turn" in text
    assert cross_metrics(report)["docs"] == {"on": False, "reason": "not_requested"}


def test_docs_run_when_asked_and_stay_off_in_trials(tmp_path: Path) -> None:
    seed(tmp_path)
    asked = Harness(tmp_path, {"scout": [Act(pack_text())]}, docs_mode=DocsMode.AUTO)
    report, recorder = run(asked, request=DOCS_REQUEST)
    assert report.steps[-1].role is Role.DOCS
    assert "Docs: on, the request asks for docs" in notes(recorder)
    seed(tmp_path / "trial")
    trial = Harness(
        tmp_path / "trial",
        {"scout": [Act(pack_text())]},
        docs_mode=DocsMode.AUTO,
        sandbox=SandboxLaunch(),
    )
    report, recorder = run(trial, request=DOCS_REQUEST)
    assert Role.DOCS not in [step.role for step in report.steps]
    assert "Docs: off in trials" in notes(recorder)


def test_a_claude_scout_launch_is_read_only_and_the_launch_mode_runs_per_role(
    tmp_path: Path,
) -> None:
    seed(tmp_path)
    harness = Harness(tmp_path, {"scout": [Act(pack_text())]})
    report, _ = run(harness, engine="claude")
    assert report.ok
    scout = harness.actors["claude"].requests[0]
    assert scout.read_only
    assert scout.allowed_tools == ("Read", "Grep", "Glob")
    assert "Edit" in scout.disallowed_tools and "Write" in scout.disallowed_tools
    launch = MandateOptions(engine="claude", shape="scout", scout_mode="launch")
    assert per_role_run(launch, "feature", "claude")
    assert not per_role_run(MandateOptions(engine="claude", shape="scout"), "feature", "claude")
    assert not per_role_run(
        MandateOptions(engine="claude", shape="scout", scout_mode="native"), "bug", "claude"
    )
    assert not per_role_run(launch, "investigation", "claude")
    assert per_role_run(MandateOptions(engine="codex", shape="scout"), "feature", "claude")
    assert SCOUT_SHAPE == "scout"


def test_a_scout_that_returns_no_pack_hands_on_its_reads_and_the_planned_edit_set(
    tmp_path: Path,
) -> None:
    seed(tmp_path)
    plan = ChangePlan(edit=(EditTarget("src/layout.ts", 0.9),), verify=("npm run build",))
    reads: dict[str, tuple[tuple[str, int, int], ...]] = {"RUN0": (("src/layout.ts", 1, 2),)}
    harness = Harness(tmp_path, {"scout": [Act("I looked around.")]}, plan=plan, reads=reads)
    report, recorder = run(harness)
    scout = report.scout
    assert scout is not None
    assert scout.check.pack.source.value == "fallback"
    assert scout.check.edit_from_plan and scout.check.pack.edit == ("src/layout.ts",)
    text = notes(recorder)
    assert "the scout returned no evidence pack" in text
    assert "the scout confirmed no edit set; the senior uses cuanta's change plan" in text
    assert "- src/layout.ts:1-2 read by scout" in harness.prompt_of("senior")[0]


class WatchedService:
    def __init__(self, events: list[EngineEvent], changed: tuple[str, ...]) -> None:
        self.events = events
        self.changed = changed
        self.saved: list[MandateReport] = []

    def run(self, *args: object) -> MandateReport:
        observer = cast("Callable[[EngineEvent], None]", args[5])
        for event in self.events:
            observer(event)
        return MandateReport(
            Run("R9", "mandate", "claude"),
            True,
            self.changed,
            "green",
            {},
            None,
            (),
            0,
            Choice("small", 1.0),
            "",
        )

    def close_decisions(self, run_id: str, outcome: str) -> None:
        return None

    def save_meta(self, report: MandateReport) -> None:
        self.saved.append(report)


class ReadyEngine:
    def available(self) -> bool:
        return True

    def missing_flags(self) -> tuple[str, ...]:
        return ()


def test_the_native_session_captures_the_scout_pack_and_flags_senior_edits(
    tmp_path: Path,
) -> None:
    seed(tmp_path)
    events: list[EngineEvent] = [
        ToolCall("Agent", "T1", {"subagent_type": "scout", "prompt": "explore the layout"}),
        AssistantText("reading the layout", "T1"),
        AssistantText(pack_text(), "T1"),
        ToolCall("Agent", "T2", {"subagent_type": "python-senior", "prompt": "x" * 400}),
        ToolCall("Edit", "E1", {"file_path": str(tmp_path / "work" / "src" / "layout.ts")}, "T2"),
        ToolCall("Write", "E2", {"file_path": "C:\\repo\\src\\named.ts"}, "T2"),
        ToolCall("MultiEdit", "E3", {"file_path": "/repo/src/stray.ts"}, "T2"),
        ToolCall("Read", "E4", {"file_path": "/repo/src/read.ts"}, "T2"),
        AssistantText('{"summary": "done", "plan": {"edit": ["src/named.ts"]}}', "T2"),
        ToolCall("Agent", "T3", {"subagent_type": "tester", "prompt": "test the layout"}),
        ToolCall("Write", "E5", {"file_path": "/repo/tests/layout.test.ts"}, "T3"),
        ToolCall("Edit", "E6", {"file_path": "/repo/src/main.ts"}),
        AssistantText("main agent text"),
    ]
    changed = (
        "src/layout.ts",
        "src/named.ts",
        "src/stray.ts",
        "src/read.ts",
        "tests/layout.test.ts",
        "src/main.ts",
    )
    report, recorder, service = native_run(tmp_path, events, changed)
    assert report.scout is not None
    summary = parse_scout(report.scout, {"on": False, "reason": "not_requested"})
    assert (summary.mode, summary.edit_set) == ("native", ("src/layout.ts",))
    assert summary.capsule.startswith("cap:")
    assert summary.outside_named == ("src/named.ts",)
    assert summary.outside_unnamed == ("src/stray.ts",)
    assert summary.senior_checked and not summary.leaks_known
    assert report.scout["dispatch_tokens"] == {"scout": 5, "senior": 100, "tester": 4}
    assert report.scout["dispatched"] is True and summary.dispatched
    assert report.docs == DocsChoice(False, DocsReason.NOT_REQUESTED)
    payload = report_payload(report)
    assert payload["scout"] == report.scout
    assert payload["docs"] == {"on": False, "reason": "not_requested"}
    assert service.saved[-1] is report
    assert "Docs: off, the request does not ask for docs" in notes(recorder)


def test_a_native_run_whose_scout_never_ran_records_it(tmp_path: Path) -> None:
    seed(tmp_path)
    events: list[EngineEvent] = [
        ToolCall("Agent", "T2", {"subagent_type": "python-senior", "prompt": "do it"}),
        ToolCall("Edit", "E1", {"file_path": "/repo/src/layout.ts"}, "T2"),
    ]
    report, _, _ = native_run(tmp_path, events, ("src/layout.ts",))
    assert report.scout is not None and report.scout["dispatched"] is False
    summary = parse_scout(report.scout)
    assert not summary.dispatched and summary.source == "fallback"
    assert summary.outside_unnamed == ("src/layout.ts",)


def native_run(
    tmp_path: Path, events: list[EngineEvent], changed: tuple[str, ...]
) -> tuple[MandateReport, Recorder, WatchedService]:
    service = WatchedService(events, changed)
    flow = MandateFlow(
        cast("MandateService", service),
        lambda name: None,
        lambda engine: cast("EngineLauncher", None),
        Stack,
        lambda run_id: ({}, None),
        str(tmp_path),
        "claude",
        0.0,
        lines_of=lambda path: (tmp_path / "work" / path).read_text(encoding="utf-8").splitlines(),
        capsules=FileCapsuleStore(tmp_path / "capsules"),
    )
    docs = DocsChoice(False, DocsReason.NOT_REQUESTED)
    prepared = cast(
        "Prepared",
        SimpleNamespace(
            launcher=SimpleNamespace(engine=cast("Engine", ReadyEngine())),
            engine_name="claude",
            composed=SimpleNamespace(request=REQUEST),
            spec=SimpleNamespace(
                run_id="R9",
                change_plan=ChangePlan(edit=(EditTarget("src/other.ts", 0.5),)),
            ),
            applied=None,
            forecast=None,
            forecast_error=None,
            scout=True,
            docs=docs,
            read_hooks=False,
        ),
    )
    recorder = Recorder()
    report = flow.run(prepared, cast("ProgressSink", recorder), verdict=False)
    return report, recorder, service


def test_the_senior_read_budget_never_falls_below_its_edit_set() -> None:
    pack = EvidencePack(edit=tuple(f"src/edit_{index}.ts" for index in range(5)))
    assert read_budget(pack, 3) == 7
    assert read_budget(pack, 9) == 9
    assert read_budget(pack) == 7


def test_the_session_counts_as_scouted_only_when_its_agents_file_has_the_scout() -> None:
    plan = scout_plan("claude")
    senior = AgentsPlan({"python-senior": {"model": "opus"}}, {"python-senior": Role.SENIOR})
    scouted = AgentsPlan(
        {"scout": {"model": "haiku"}, "python-senior": {"model": "opus"}},
        {"scout": Role.SCOUT, "python-senior": Role.SENIOR},
    )
    assert not has_scout(None)
    assert not has_scout(Applied(plan, "claude"))
    assert not has_scout(Applied(plan, "claude", agents=senior))
    assert has_scout(Applied(plan, "claude", agents=scouted))
