from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from cuanta.adapters.storage.capsule_store import FileCapsuleStore
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.application.cross_engine import (
    CROSS_ORDER,
    CompletionState,
    CrossEnginePipeline,
    CrossReport,
    NewFileGuard,
    cross_metrics,
)
from cuanta.application.engine_run import EngineLauncher
from cuanta.application.routing import RoutePlan
from cuanta.application.verification import Verifier
from cuanta.cli.commands.mandate import cross_payload
from cuanta.domain.change_plan import ChangePlan, EditTarget
from cuanta.domain.engine import EngineEvent, EngineOutcome, EngineRequest, RunResult
from cuanta.domain.governor_report import HOOKS
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import Message, english, msg
from cuanta.domain.models import ModelEntry, Tier
from cuanta.domain.progress import ProgressEvent
from cuanta.domain.role_budgets import (
    REPAIR_FRACTION,
    OvershootMargins,
    RepairBudget,
    allocate_budget,
    floor_fraction,
    role_split,
)
from cuanta.domain.role_handoff import VerifyResult
from cuanta.domain.routing import ROLES, Role, RoleRoute, RoutingPolicy
from tests.fakes import FakeRunner, FakeStream

REQUEST = MandateRequest(type="feature", what="add canonical", why="seo", out_of_scope="secrets")
FIX = MandateRequest(type="bug", what="fix canonical", why="seo", out_of_scope="secrets")


@dataclass
class Act:
    text: str = '{"summary": "done", "status": "done"}'
    cost: float = 0.1
    writes: Mapping[str, str] = field(default_factory=dict)
    subtype: str = "success"
    whole_cap: bool = False


class Recorder:
    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    def publish(self, event: ProgressEvent) -> None:
        self.events.append(event)

    def texts(self) -> list[str]:
        return [
            english(message)
            for event in self.events
            if (message := getattr(event, "message", None)) is not None
        ]


class Actor:
    def __init__(self, name: str, root: Path, script: dict[str, list[Act]]) -> None:
        self._name = name
        self._root = root
        self._script = script
        self.requests: list[EngineRequest] = []

    @property
    def name(self) -> str:
        return self._name

    def available(self) -> bool:
        return True

    def version(self) -> str:
        return "x"

    def missing_flags(self) -> tuple[str, ...]:
        return ()

    def cancel(self) -> None:
        return None

    def command(self, request: EngineRequest) -> list[str]:
        return [self._name]

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        self.requests.append(request)
        role = request.prompt.split("You are the ", 1)[1].split(" ", 1)[0]
        queue = self._script.get(role, [])
        act = queue.pop(0) if queue else Act()
        for path, content in act.writes.items():
            target = self._root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        ok = act.subtype == "success"
        cost = request.max_budget_usd if act.whole_cap else act.cost
        result = RunResult(ok, act.subtype, cost, 1, "s", (), act.text)
        on_event(result)
        return EngineOutcome(0 if ok else 1, result, 0)


def routes(engines: Mapping[Role, str]) -> RoutePlan:
    found = tuple(
        RoleRoute(
            role,
            Tier.STANDARD,
            Tier.STANDARD if role in engines else None,
            ModelEntry(engines[role], f"model-{role.value}", "m", "p") if role in engines else None,
            msg("route.policy", tier="standard"),
        )
        for role in ROLES
    )
    return RoutePlan(RoutingPolicy(), None, None, (), found, "heuristic")


MIX = {Role.ANALYST: "claude", Role.SENIOR: "codex", Role.TESTER: "claude", Role.DOCS: "claude"}


def snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file() and "capsules" not in path.parts and ".cuanta" not in path.parts
    }


def lines(root: Path) -> Callable[[str], Sequence[str] | None]:
    def read(path: str) -> Sequence[str] | None:
        target = root / path
        return target.read_text(encoding="utf-8").splitlines() if target.is_file() else None

    return read


@dataclass
class Harness:
    root: Path
    script: dict[str, list[Act]]
    budget: float = 2.0
    shares: Mapping[Role, float] | None = None
    verify_results: list[tuple[VerifyResult, ...]] = field(default_factory=list)
    plan: ChangePlan = field(default_factory=lambda: ChangePlan(verify=("npm run build",)))
    reads: dict[str, tuple[tuple[str, int, int], ...]] = field(default_factory=dict)
    margin: float = 0.1
    index_engines: frozenset[str] = frozenset()
    guard: NewFileGuard | None = None
    checkpoint: Callable[[], Message | None] | None = None
    actors: dict[str, Actor] = field(default_factory=dict)
    ledger: MemoryLedger = field(default_factory=MemoryLedger)
    saved: dict[str, Mapping[str, object]] = field(default_factory=dict)
    reserve: float = 0.0
    repairs: list[bool] = field(default_factory=list)
    discipline: Callable[[str], bool] | None = None

    def allocate(
        self, plan: RoutePlan, kind: str, depth: str, cap: float, repair: bool
    ) -> RepairBudget:
        self.repairs.append(repair)
        return RepairBudget(self.shares or {}, self.reserve, False)

    def verifier(
        self, commands: Sequence[str], stopped: Callable[[], bool]
    ) -> tuple[VerifyResult, ...]:
        if self.verify_results:
            return self.verify_results.pop(0)
        return tuple(VerifyResult(command, 0, 1.0) for command in commands)

    def build(self) -> CrossEnginePipeline:
        counter = iter(range(100))
        work = self.root / "work"
        work.mkdir(exist_ok=True)

        def launcher(name: str) -> EngineLauncher:
            actor = self.actors.setdefault(name, Actor(name, work, self.script))
            return EngineLauncher(
                actor,
                self.ledger,
                FixedClock(),
                lambda: f"RUN{next(counter)}",
                lambda size: b"\x01" * size,
                "shop",
                4318,
                None,
            )

        def save(run_id: str, metrics: Mapping[str, object]) -> None:
            self.saved[run_id] = metrics

        return CrossEnginePipeline(
            launcher,
            tuple,
            FileCapsuleStore(self.root / "capsules"),
            str(work),
            self.budget,
            allocator=self.allocate if self.shares is not None else None,
            change_plan=lambda request: self.plan,
            snapshot=lambda: snapshot(work),
            save_metrics=save,
            verifier=self.verifier,
            lines_of=lines(work),
            reads_of=lambda run_id: self.reads.get(run_id, ()),
            margin=lambda: OvershootMargins(
                {f"model-{role.value}": (self.margin,) * 3 for role in ROLES}
            ),
            index_tools=lambda engine: engine in self.index_engines,
            new_files=self.guard,
            checkpoint=self.checkpoint,
            read_discipline=self.discipline,
        )

    def run(
        self, engines: Mapping[Role, str] = MIX, request: MandateRequest = REQUEST
    ) -> tuple[CrossReport, Recorder]:
        recorder = Recorder()
        return self.build().run(request, routes(engines), recorder), recorder

    def prompts(self) -> list[str]:
        found = [request for actor in self.actors.values() for request in actor.requests]
        return [request.prompt for request in found]

    def prompt_of(self, role: str) -> list[str]:
        return [prompt for prompt in self.prompts() if f"You are the {role} " in prompt]


def analyst_json() -> str:
    return json.dumps(
        {
            "summary": "layout needs a canonical",
            "decisions": ["edit layout only"],
            "facts": [{"path": "src/layout.ts", "start": 1, "end": 2, "claim": "metadata"}],
            "plan": {"edit": ["src/layout.ts"], "read": [], "verify": ["rm -rf /"]},
            "status": "done",
        }
    )


def seed(root: Path) -> None:
    work = root / "work"
    (work / "src").mkdir(parents=True, exist_ok=True)
    (work / "src" / "layout.ts").write_text("export const a = 1\nexport const b = 2\n", "utf-8")


def test_roles_receive_the_merged_chain_with_anchors_and_stale_marks(tmp_path: Path) -> None:
    seed(tmp_path)
    script = {
        "analyst": [Act(analyst_json(), 0.1)],
        "senior": [Act('{"summary": "edited", "status": "done"}', 0.2, {"src/layout.ts": "x\n"})],
    }
    harness = Harness(tmp_path, script, index_engines=frozenset({"claude"}))
    report, _ = harness.run()
    assert report.state is CompletionState.COMPLETE and report.ok
    senior = harness.prompt_of("senior")[0]
    assert "- analyst [claude/model-analyst] done: layout needs a canonical" in senior
    assert "- src/layout.ts:1-2 metadata" in senior
    assert "READ ONLY WHAT IS NEEDED" in senior and "Cuanta page" not in senior
    tester = harness.prompt_of("tester")[0]
    assert "Stale facts" in tester and "- src/layout.ts:1-2 metadata" in tester
    assert "- senior [codex/model-senior] done: edited" in tester
    assert "Cuanta page (path and lines as start:end)" in tester
    assert "`npm run build`" in tester and "Do not run builds yourself." in tester
    assert "rm -rf" not in " ".join(
        result.command for item in report.verifications for result in item.results
    )
    assert [step.index_tools for step in report.steps] == [True, False, True, True]
    assert report.steps[1].changed_files == ("src/layout.ts",)
    assert report.handoffs[1].files_changed == ("src/layout.ts",)


def test_a_budget_stop_is_salvaged_and_the_next_roles_still_run(tmp_path: Path) -> None:
    seed(tmp_path)
    script = {"analyst": [Act("Reading layout", 0.5)]}
    harness = Harness(
        tmp_path,
        script,
        budget=1.0,
        shares={Role.ANALYST: 0.28, Role.SENIOR: 0.4, Role.TESTER: 0.2, Role.DOCS: 0.12},
        reads={"RUN0": (("src/layout.ts", 1, 0),)},
    )
    report, recorder = harness.run()
    assert [step.role for step in report.steps] == [
        Role.ANALYST,
        Role.SENIOR,
        Role.TESTER,
        Role.DOCS,
    ]
    assert report.steps[0].salvaged and not report.steps[0].ok
    assert report.handoffs[0].reason == "error_max_budget_usd"
    assert report.handoffs[0].facts[0].anchor == "src/layout.ts:1-2"
    assert "analyst stopped at its budget share" in " ".join(recorder.texts())
    assert (
        "- analyst [claude/model-analyst] partial (error_max_budget_usd)"
        in harness.prompt_of("senior")[0]
    )
    assert report.state is CompletionState.COMPLETE and report.ok
    assert report.steps[1].budget_usd == pytest.approx(0.4)


def test_a_stop_without_room_for_the_next_floors_is_partial(tmp_path: Path) -> None:
    seed(tmp_path)
    harness = Harness(
        tmp_path,
        {"analyst": [Act("Reading", 0.95)]},
        budget=1.0,
        shares={Role.ANALYST: 0.3, Role.SENIOR: 0.4, Role.TESTER: 0.3},
    )
    report, _ = harness.run({Role.ANALYST: "claude", Role.SENIOR: "codex", Role.TESTER: "claude"})
    assert report.state is CompletionState.PARTIAL and not report.ok
    assert report.stopped is not None and report.stopped.key == "cross.partial_budget"
    assert len(report.steps) == 1


def test_docs_and_a_verified_tester_are_skipped_when_below_their_floor(tmp_path: Path) -> None:
    seed(tmp_path)
    script = {"senior": [Act(cost=0.87, writes={"src/layout.ts": "y\n"})]}
    harness = Harness(
        tmp_path,
        script,
        budget=1.0,
        shares={Role.ANALYST: 0.2, Role.SENIOR: 0.7, Role.TESTER: 0.05, Role.DOCS: 0.05},
    )
    report, recorder = harness.run()
    assert [step.role for step in report.steps] == [Role.ANALYST, Role.SENIOR]
    assert [item.role for item in report.skipped] == [Role.TESTER, Role.DOCS]
    assert report.state is CompletionState.COMPLETE_SKIPPED and report.ok
    text = " ".join(recorder.texts())
    assert "tester skipped: verification passed" in text
    assert "docs skipped" in text


def test_a_failed_check_gets_one_repair_then_passes(tmp_path: Path) -> None:
    seed(tmp_path)
    failing = (VerifyResult("npm run build", 1, 2.0, ("src/layout.ts:1 Type error",)),)
    script = {
        "senior": [
            Act(cost=0.1, writes={"src/layout.ts": "broken\n"}),
            Act(cost=0.05, writes={"src/layout.ts": "fixed\n"}),
        ]
    }
    harness = Harness(tmp_path, script, verify_results=[failing])
    report, recorder = harness.run()
    senior = [step for step in report.steps if step.role is Role.SENIOR]
    assert [step.repair for step in senior] == [False, True]
    assert [(item.role, item.attempt, item.passed) for item in report.verifications] == [
        (Role.SENIOR, 1, False),
        (Role.SENIOR, 2, True),
    ]
    repair = harness.prompt_of("senior")[1]
    assert "=== REPAIR TURN ===" in repair and "src/layout.ts:1 Type error" in repair
    assert "senior gets one repair turn" in " ".join(recorder.texts())
    assert report.state is CompletionState.COMPLETE
    assert report.spent_usd == pytest.approx(0.45)


def test_a_disciplined_senior_keeps_its_read_discipline_through_a_repair_round(
    tmp_path: Path,
) -> None:
    seed(tmp_path)
    failing = (VerifyResult("npm run build", 1, 2.0, ("src/layout.ts:1 Type error",)),)
    script = {
        "senior": [
            Act(cost=0.1, writes={"src/layout.ts": "broken\n"}),
            Act(cost=0.05, writes={"src/layout.ts": "fixed\n"}),
        ]
    }
    team = {Role.ANALYST: "claude", Role.SENIOR: "claude", Role.TESTER: "claude"}
    harness = Harness(
        tmp_path, script, verify_results=[failing], discipline=lambda engine: engine == "claude"
    )
    report, _ = harness.run(team)
    senior = [step for step in report.steps if step.role is Role.SENIOR]
    assert [(step.repair, step.read_discipline) for step in senior] == [
        (False, HOOKS),
        (True, HOOKS),
    ]
    modes = {"analyst": HOOKS, "senior": HOOKS, "tester": HOOKS}
    assert cross_metrics(report)["governor"] == {"reactions": [], "read_discipline": modes}
    governor = cross_payload(report)["governor"]
    assert isinstance(governor, dict) and governor["read_discipline"] == modes


def test_a_fix_without_docs_funds_its_repair_turn_from_the_docs_share(tmp_path: Path) -> None:
    seed(tmp_path)
    failing = (VerifyResult("npm run build", 1, 2.0, ("src/layout.ts:1 Type error",)),)
    team = {Role.ANALYST: "claude", Role.SENIOR: "codex", Role.TESTER: "claude"}
    as_if_docs = allocate_budget(dict.fromkeys((*team, Role.DOCS)), 1.0)
    senior_cap = 1.0 - 0.1 - as_if_docs[Role.TESTER] - as_if_docs[Role.DOCS]
    script = {
        "senior": [
            Act(cost=senior_cap, writes={"src/layout.ts": "broken\n"}),
            Act(cost=0.05, writes={"src/layout.ts": "fixed\n"}),
        ]
    }
    harness = Harness(tmp_path, script, budget=1.0, verify_results=[failing])
    report, recorder = harness.run(team, FIX)
    assert [(step.role, step.repair) for step in report.steps] == [
        (Role.ANALYST, False),
        (Role.SENIOR, False),
        (Role.SENIOR, True),
        (Role.TESTER, False),
    ]
    assert report.steps[0].budget_usd == pytest.approx(as_if_docs[Role.ANALYST])
    assert report.steps[1].budget_usd == pytest.approx(senior_cap)
    assert report.steps[2].budget_usd == pytest.approx(as_if_docs[Role.DOCS])
    text = " ".join(recorder.texts())
    assert f"${as_if_docs[Role.DOCS]:.4f} is held for one repair turn, funded from the docs" in text
    assert report.state is CompletionState.COMPLETE


def test_a_fix_with_docs_holds_a_fixed_share_of_the_cap_for_its_repair(tmp_path: Path) -> None:
    seed(tmp_path)
    failing = (VerifyResult("npm run build", 1, 2.0, ("src/layout.ts:1 Type error",)),)
    reserve = 1.0 * REPAIR_FRACTION
    shares = role_split(dict.fromkeys(MIX), 1.0, True).shares
    senior_cap = 1.0 - 0.1 - shares[Role.TESTER] - shares[Role.DOCS] - reserve
    script = {
        "senior": [
            Act(cost=senior_cap, writes={"src/layout.ts": "broken\n"}),
            Act(cost=0.05, writes={"src/layout.ts": "fixed\n"}),
        ]
    }
    harness = Harness(tmp_path, script, budget=1.0, verify_results=[failing])
    report, recorder = harness.run(MIX, FIX)
    assert report.steps[0].budget_usd == pytest.approx(shares[Role.ANALYST])
    repair = [step for step in report.steps if step.repair]
    assert [step.budget_usd for step in repair] == pytest.approx([reserve])
    assert f"${reserve:.4f} is held for one repair turn" in " ".join(recorder.texts())
    assert [step.role for step in report.steps if not step.repair] == list(CROSS_ORDER)


def test_a_fix_without_checks_holds_no_repair_reserve_and_splits_like_a_feature(
    tmp_path: Path,
) -> None:
    seed(tmp_path)
    team = {Role.ANALYST: "claude", Role.SENIOR: "codex", Role.TESTER: "claude"}
    feature = allocate_budget(dict.fromkeys(team), 1.0)
    script = {"analyst": [Act(cost=0.2)]}
    harness = Harness(tmp_path, script, budget=1.0, plan=ChangePlan())
    report, recorder = harness.run(team, FIX)
    assert [step.budget_usd for step in report.steps[:2]] == pytest.approx(
        [feature[Role.ANALYST], 1.0 - 0.2 - feature[Role.TESTER]]
    )
    assert not report.verifications
    assert "held for one repair turn" not in " ".join(recorder.texts())
    pinned_root = tmp_path / "pinned"
    seed(pinned_root)
    shares = {Role.ANALYST: 0.26, Role.SENIOR: 0.34, Role.TESTER: 0.22}
    pinned = Harness(
        pinned_root,
        {"analyst": [Act(cost=0.2)]},
        budget=1.0,
        shares=shares,
        reserve=0.18,
        plan=ChangePlan(),
    )
    held, notes = pinned.run(team, FIX)
    assert pinned.repairs == [False]
    assert held.steps[0].budget_usd == pytest.approx(1.0 - 0.34 - 0.22)
    assert "held for one repair turn" not in " ".join(notes.texts())


def test_passing_checks_release_the_repair_reserve_for_the_tester(tmp_path: Path) -> None:
    seed(tmp_path)
    split = role_split(dict.fromkeys(MIX), 1.0, True)
    analyst, senior = split.shares[Role.ANALYST], split.shares[Role.SENIOR]
    script = {
        "analyst": [Act(cost=analyst)],
        "senior": [Act(cost=senior + 0.12, writes={"src/layout.ts": "fixed\n"})],
    }
    harness = Harness(tmp_path, script, budget=1.0)
    report, recorder = harness.run(MIX, FIX)
    assert [(item.role, item.passed) for item in report.verifications] == [(Role.SENIOR, True)]
    assert [step.role for step in report.steps] == list(CROSS_ORDER)
    assert not report.skipped
    left = 1.0 - analyst - senior - 0.12
    assert report.steps[1].overrun_usd == pytest.approx(0.12)
    assert report.steps[2].budget_usd == pytest.approx(left - split.shares[Role.DOCS])
    assert "tester skipped" not in " ".join(recorder.texts())
    assert report.state is CompletionState.COMPLETE


def test_the_repair_reserve_gives_way_before_an_optional_role_is_skipped(tmp_path: Path) -> None:
    seed(tmp_path)
    split = role_split(dict.fromkeys(MIX), 1.0, True)
    analyst, senior, tester = (split.shares[role] for role in CROSS_ORDER[:3])
    script = {
        "analyst": [Act(cost=analyst)],
        "senior": [Act(cost=senior + 0.2)],
        "tester": [Act(cost=tester)],
    }
    harness = Harness(tmp_path, script, budget=1.0)
    report, recorder = harness.run(MIX, FIX)
    assert split.repair_usd > 0 and not report.verifications
    assert [step.role for step in report.steps] == list(CROSS_ORDER)
    assert not report.skipped
    left = 1.0 - analyst - (senior + 0.2) - tester
    assert left < split.repair_usd
    assert report.steps[3].budget_usd == pytest.approx(left)
    assert "docs skipped" not in " ".join(recorder.texts())


def test_a_feature_keeps_the_writer_left_over_as_its_only_repair_money(tmp_path: Path) -> None:
    seed(tmp_path)
    failing = (VerifyResult("npm run build", 1, 2.0, ("src/layout.ts:1 Type error",)),)
    team = {Role.ANALYST: "claude", Role.SENIOR: "codex", Role.TESTER: "claude"}
    shares = allocate_budget(dict.fromkeys(team), 1.0)
    senior_cap = 1.0 - 0.1 - shares[Role.TESTER]
    script = {"senior": [Act(cost=senior_cap, writes={"src/layout.ts": "broken\n"})]}
    harness = Harness(tmp_path, script, budget=1.0, verify_results=[failing, failing])
    report, recorder = harness.run(team)
    assert not any(step.repair for step in report.steps)
    assert "held for one repair turn" not in " ".join(recorder.texts())


@pytest.mark.parametrize(("engine", "repaired"), [("codex", True), ("claude", False)])
def test_only_a_writer_carried_past_its_share_gets_the_held_repair_turn(
    tmp_path: Path, engine: str, repaired: bool
) -> None:
    seed(tmp_path)
    failing = (VerifyResult("npm run build", 1, 2.0, ("src/layout.ts:1 Type error",)),)
    split = role_split(dict.fromkeys(MIX), 1.0, True)
    senior_cap = 1.0 - 0.1 - split.shares[Role.TESTER] - split.shares[Role.DOCS] - split.repair_usd
    script = {
        "senior": [
            Act(cost=senior_cap + 0.03, writes={"src/layout.ts": "broken\n"}),
            Act(cost=0.05, writes={"src/layout.ts": "fixed\n"}),
        ]
    }
    harness = Harness(tmp_path, script, budget=1.0, verify_results=[failing])
    report, _ = harness.run({**MIX, Role.SENIOR: engine}, FIX)
    writer = report.steps[1]
    assert writer.role is Role.SENIOR and writer.budget_usd == pytest.approx(senior_cap)
    assert writer.ok is repaired and writer.salvaged is not repaired
    repair = [step for step in report.steps if step.repair]
    assert len(harness.prompt_of("senior")) == (2 if repaired else 1)
    if repaired:
        assert [step.budget_usd for step in repair] == pytest.approx([split.repair_usd - 0.03])
        assert writer.overrun_usd == pytest.approx(0.03)
        assert [(item.attempt, item.passed) for item in report.verifications] == [
            (1, False),
            (2, True),
        ]
        assert report.state is CompletionState.COMPLETE
    else:
        assert repair == []
        assert [(item.attempt, item.passed) for item in report.verifications] == [(1, False)]


def test_skewed_fix_shares_keep_every_floor_so_docs_still_runs_after_a_repair(
    tmp_path: Path,
) -> None:
    seed(tmp_path)
    failing = (VerifyResult("npm run build", 1, 2.0, ("src/layout.ts:1 Type error",)),)
    skewed: dict[Role, float | None] = {
        Role.ANALYST: 0.1,
        Role.SENIOR: 3.0,
        Role.TESTER: 0.1,
        Role.DOCS: 0.02,
    }
    split = role_split(skewed, 1.0, True)
    script = {
        "analyst": [Act(whole_cap=True)],
        "senior": [
            Act(whole_cap=True, writes={"src/layout.ts": "broken\n"}),
            Act(whole_cap=True, writes={"src/layout.ts": "fixed\n"}),
        ],
        "tester": [Act(whole_cap=True)],
        "docs": [Act(whole_cap=True)],
    }
    harness = Harness(
        tmp_path,
        script,
        budget=1.0,
        shares=split.shares,
        reserve=split.repair_usd,
        verify_results=[failing],
    )
    report, recorder = harness.run(dict.fromkeys(CROSS_ORDER, "codex"), FIX)
    assert [(step.role, step.repair) for step in report.steps] == [
        (Role.ANALYST, False),
        (Role.SENIOR, False),
        (Role.SENIOR, True),
        (Role.TESTER, False),
        (Role.DOCS, False),
    ]
    assert not report.skipped and "docs skipped" not in " ".join(recorder.texts())
    for step in report.steps:
        if not step.repair:
            assert step.budget_usd >= floor_fraction(step.role) - 1e-9
    assert report.state is CompletionState.COMPLETE


def test_a_second_failure_passes_the_results_to_the_tester(tmp_path: Path) -> None:
    seed(tmp_path)
    failing = (VerifyResult("npm run build", 1, 2.0, ("src/layout.ts:1 still broken",)),)
    script = {
        "senior": [Act(writes={"src/layout.ts": "a\n"}), Act(writes={"src/layout.ts": "b\n"})],
        "tester": [Act(writes={"tests/layout.test.ts": "t\n"})],
    }
    harness = Harness(tmp_path, script, verify_results=[failing, failing, failing, failing])
    report, recorder = harness.run()
    tester = harness.prompt_of("tester")[0]
    assert "Verification failures:" in tester and "src/layout.ts:1 still broken" in tester
    assert "the tester receives the results" in " ".join(recorder.texts())
    assert report.state is CompletionState.PARTIAL
    assert report.stopped is not None
    assert (
        english(report.stopped) == "the checks still fail after tester; the change is not verified"
    )


class Building(FakeStream):
    def __init__(self, pressed: Callable[[], object]) -> None:
        super().__init__(["building"], 1)
        self.pressed = pressed
        self.released = threading.Event()

    def lines(self) -> Iterator[str]:
        yield from self.output
        self.pressed()
        self.released.wait(10)

    def close(self) -> None:
        self.closed = True
        self.released.set()


@dataclass
class Checked(Harness):
    checks: Verifier | None = None

    def verifier(
        self, commands: Sequence[str], stopped: Callable[[], bool]
    ) -> tuple[VerifyResult, ...]:
        assert self.checks is not None
        return self.checks.run(commands, stopped)


@pytest.mark.parametrize("attempt", [1, 2])
def test_a_stop_during_the_checks_ends_the_run_and_no_later_check_starts(
    tmp_path: Path, attempt: int
) -> None:
    seed(tmp_path)
    harness = Checked(
        tmp_path,
        {"senior": [Act(writes={"src/layout.ts": f"v{n}\n"}) for n in range(attempt)]},
        plan=ChangePlan(verify=("npm run build", "npm run lint")),
    )
    pipeline = harness.build()
    building = Building(pipeline.stop)
    queued: list[FakeStream] = [FakeStream(["src/layout.ts:1 Type error"], 1)] * (attempt - 1)
    queued.append(building)
    runner = FakeRunner(queued={"npm run build": queued})
    harness.checks = Verifier(runner, tmp_path, windows=False)
    recorder = Recorder()
    started = time.monotonic()
    report = pipeline.run(REQUEST, routes(MIX), recorder)
    assert time.monotonic() - started < 5.0
    assert building.closed
    assert runner.calls[-1] == ("npm", "run", "build")
    assert runner.calls.count(("npm", "run", "lint")) == attempt - 1
    assert [(item.attempt, item.passed) for item in report.verifications] == [
        (number, False) for number in range(1, attempt + 1)
    ]
    stopped = report.verifications[-1].results
    assert [(result.command, result.exit_code) for result in stopped] == [("npm run build", None)]
    assert stopped[0].errors == ("stopped before it finished",)
    assert [step.role for step in report.steps] == [Role.ANALYST, *[Role.SENIOR] * attempt]
    assert harness.prompt_of("tester") == []
    assert report.state is CompletionState.PARTIAL and not report.ok
    assert report.stopped == msg("cross.stopped")
    assert "the tester receives the results" not in " ".join(recorder.texts())


def test_a_protected_change_stops_before_the_next_role_and_names_it(tmp_path: Path) -> None:
    seed(tmp_path)
    script = {"senior": [Act(writes={"secrets/key.txt": "leak\n", "src/layout.ts": "z\n"})]}
    harness = Harness(tmp_path, script, plan=ChangePlan(guard=("secrets/**",)))
    report, _ = harness.run()
    assert [step.role for step in report.steps] == [Role.ANALYST, Role.SENIOR]
    assert report.state is CompletionState.FAILED and report.guard_role == "senior"
    assert report.stopped is not None
    assert english(report.stopped).startswith("senior changed protected files (secrets/key.txt)")
    assert harness.prompt_of("tester") == []


def test_codex_overruns_are_charged_to_the_remainder_and_shown(tmp_path: Path) -> None:
    seed(tmp_path)
    script = {"senior": [Act(cost=1.3, writes={"src/layout.ts": "q\n"})]}
    harness = Harness(
        tmp_path,
        script,
        budget=2.0,
        shares={Role.ANALYST: 0.4, Role.SENIOR: 0.6, Role.TESTER: 0.6, Role.DOCS: 0.4},
    )
    report, recorder = harness.run()
    senior = report.steps[1]
    assert senior.ok and senior.overrun_usd == pytest.approx(0.4)
    assert "the $0.4000 overrun comes out of the remaining budget" in " ".join(recorder.texts())
    assert report.steps[2].budget_usd == pytest.approx(0.2)


def test_claude_native_caps_sit_below_the_share_by_the_model_overshoot(tmp_path: Path) -> None:
    seed(tmp_path)
    harness = Harness(
        tmp_path,
        {},
        budget=1.0,
        margin=0.2,
        shares={Role.ANALYST: 0.5, Role.SENIOR: 0.5},
    )
    report, recorder = harness.run({Role.ANALYST: "claude", Role.SENIOR: "codex"})
    assert [step.native_cap_usd for step in report.steps] == pytest.approx([0.3, 0.9])
    requests = [request.max_budget_usd for request in harness.actors["claude"].requests]
    assert requests == pytest.approx([0.3])
    assert "analyst native cap $0.3000, $0.2000 below its $0.5000 share" in recorder.texts()


def test_a_large_overshoot_never_takes_more_than_half_a_claude_share(tmp_path: Path) -> None:
    seed(tmp_path)
    harness = Harness(
        tmp_path,
        {},
        budget=1.0,
        margin=0.4,
        shares={Role.ANALYST: 0.5, Role.SENIOR: 0.5},
    )
    report, _ = harness.run({Role.ANALYST: "claude", Role.SENIOR: "codex"})
    assert report.steps[0].native_cap_usd == pytest.approx(0.25)


@dataclass
class FakeGuard:
    root: Path
    created: list[str] = field(default_factory=list)
    settled: list[tuple[str, ...]] = field(default_factory=list)
    hidden: tuple[str, ...] = ()

    def prepare(self, plan: ChangePlan | None) -> tuple[str, ...]:
        paths = tuple(item.path for item in (plan.edit if plan else ()))
        for path in paths:
            (self.root / path).write_text("", encoding="utf-8")
        self.created.extend(paths)
        return paths

    def settle(self, created: Sequence[str]) -> tuple[str, ...]:
        self.settled.append(tuple(created))
        return ()

    def unreadable(self) -> tuple[str, ...]:
        return self.hidden if self.settled else ()


def test_codex_writers_get_planned_files_first_and_unreadable_ones_are_reported(
    tmp_path: Path,
) -> None:
    seed(tmp_path)
    guard = FakeGuard(tmp_path / "work", hidden=("src/ghost.ts",))
    plan = ChangePlan(edit=(EditTarget("src/new.ts", 0.8, "explicit new file path"),))
    harness = Harness(tmp_path, {"senior": [Act(writes={"src/new.ts": "n\n"})]}, plan=plan)
    harness.guard = guard
    report, recorder = harness.run()
    assert guard.created == ["src/new.ts"] and guard.settled == [("src/new.ts",)]
    senior = report.steps[1]
    assert senior.unreadable_files == ("src/ghost.ts",)
    assert "src/ghost.ts" in report.handoffs[1].files_changed
    text = " ".join(recorder.texts())
    assert "created 1 planned new files before the Codex writer" in text
    assert "Codex created files cuanta cannot read: src/ghost.ts" in text


def test_metrics_record_handoff_tokens_rereads_and_completion(tmp_path: Path) -> None:
    seed(tmp_path)
    (tmp_path / "work" / "src" / "other.ts").write_text("o\n", encoding="utf-8")
    script = {"analyst": [Act(analyst_json(), 0.1)]}
    reads = {
        "RUN0": (("src/layout.ts", 1, 2), ("src/other.ts", 1, 1)),
        "RUN1": (("SRC/Layout.ts", 1, 2),),
    }
    harness = Harness(tmp_path, script, reads=reads)
    report, _ = harness.run({Role.ANALYST: "claude", Role.SENIOR: "claude"})
    assert report.steps[0].read_files == ("src/layout.ts", "src/other.ts")
    assert report.steps[1].covered_files == ("src/layout.ts",)
    assert report.steps[1].reread_files == ("SRC/Layout.ts",)
    assert report.steps[0].handoff_tokens == 0 and report.steps[1].handoff_tokens > 0
    metrics = harness.saved["RUN0"]
    assert metrics["completion"] == "complete"
    assert metrics["rereads"] == {
        "analyst": {"covered": 0, "read": 2, "reread": 0},
        "senior": {"covered": 1, "read": 1, "reread": 1},
    }
    assert cross_metrics(report)["skipped_roles"] == []


def test_an_analyst_edit_stops_the_pipeline_before_the_senior(tmp_path: Path) -> None:
    seed(tmp_path)
    harness = Harness(
        tmp_path,
        {"analyst": [Act(writes={"src/layout.ts": "analyst edit\n"})]},
        plan=ChangePlan(guard=("secrets/**",)),
    )
    report, _ = harness.run()
    assert [step.role for step in report.steps] == [Role.ANALYST]
    assert report.guard_role == "analyst" and report.state is CompletionState.FAILED
    assert harness.prompt_of("senior") == []


def test_a_protected_change_in_the_repair_turn_stops_the_pipeline(tmp_path: Path) -> None:
    seed(tmp_path)
    failing = (VerifyResult("npm run build", 1, 1.0, ("src/layout.ts:1 broken",)),)
    script = {
        "senior": [
            Act(writes={"src/layout.ts": "broken\n"}),
            Act(writes={"secrets/token.txt": "leak\n"}),
        ]
    }
    harness = Harness(
        tmp_path,
        script,
        verify_results=[failing],
        plan=ChangePlan(guard=("secrets/**",), verify=("npm run build",)),
    )
    report, _ = harness.run()
    assert [(step.role, step.repair) for step in report.steps] == [
        (Role.ANALYST, False),
        (Role.SENIOR, False),
        (Role.SENIOR, True),
    ]
    assert report.guard_role == "senior" and report.state is CompletionState.FAILED
    assert harness.prompt_of("tester") == []


def test_sandbox_guards_run_after_verification_too(tmp_path: Path) -> None:
    seed(tmp_path)
    harness = Harness(tmp_path, {"senior": [Act(writes={"src/layout.ts": "v\n"})]})
    calls: list[int] = []

    def checkpoint() -> Message | None:
        calls.append(1)
        return (
            msg("sandbox.state_changed", count=1, paths="package-lock.json")
            if len(calls) == 3
            else None
        )

    harness.checkpoint = checkpoint
    report = harness.build().run(REQUEST, routes(MIX), Recorder())
    assert len(calls) == 3 and report.state is CompletionState.FAILED
    assert [item.role for item in report.verifications] == [Role.SENIOR]
    assert [step.role for step in report.steps] == [Role.ANALYST, Role.SENIOR]


def test_after_a_salvage_the_next_required_role_still_gets_its_floor(tmp_path: Path) -> None:
    seed(tmp_path)
    harness = Harness(
        tmp_path,
        {"analyst": [Act("reading", 0.65)]},
        budget=1.0,
        shares={Role.ANALYST: 0.28, Role.SENIOR: 0.4, Role.TESTER: 0.2, Role.DOCS: 0.12},
    )
    report, _ = harness.run()
    senior = report.steps[1]
    assert senior.role is Role.SENIOR and senior.budget_usd >= 0.2
    assert senior.budget_usd == pytest.approx(0.27)


def test_a_salvaged_senior_leaves_the_run_partial(tmp_path: Path) -> None:
    seed(tmp_path)
    harness = Harness(
        tmp_path,
        {"senior": [Act("half done", 0.9, {"src/layout.ts": "half\n"}, "error_max_budget_usd")]},
        budget=2.0,
    )
    report, _ = harness.run({Role.ANALYST: "claude", Role.SENIOR: "claude", Role.TESTER: "claude"})
    assert report.steps[1].salvaged
    assert report.state is CompletionState.PARTIAL and not report.ok
    assert report.stopped is not None
    assert english(report.stopped) == (
        "senior stopped at its budget share before finishing; the change may be incomplete"
    )


def test_a_blocked_writer_ends_the_run_as_partial_and_names_the_cause(tmp_path: Path) -> None:
    seed(tmp_path)
    blocked = '{"status": "blocked", "blocked_reason": "the analyst plan JSON is missing"}'
    harness = Harness(tmp_path, {"senior": [Act(blocked, 0.05)]})
    report, _ = harness.run()
    assert [step.role for step in report.steps] == [Role.ANALYST, Role.SENIOR]
    assert report.state is CompletionState.PARTIAL and not report.ok
    assert report.stopped is not None
    assert english(report.stopped) == (
        "senior reported that it is blocked: the analyst plan JSON is missing"
    )
    assert harness.prompt_of("tester") == []


def test_after_a_partial_handoff_the_next_role_is_told_to_continue(tmp_path: Path) -> None:
    seed(tmp_path)
    harness = Harness(
        tmp_path,
        {"analyst": [Act("reading", 0.65)]},
        budget=1.0,
        shares={Role.ANALYST: 0.28, Role.SENIOR: 0.4, Role.TESTER: 0.2, Role.DOCS: 0.12},
    )
    harness.run()
    senior = harness.prompt_of("senior")[0]
    assert "That does not block you" in senior
    assert "That does not block you" not in harness.prompt_of("analyst")[0]
