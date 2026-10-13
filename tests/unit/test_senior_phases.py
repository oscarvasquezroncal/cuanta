from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from cuanta.adapters.storage.capsule_store import FileCapsuleStore
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.application.cross_engine import SENIOR_PHASES, CrossEnginePipeline
from cuanta.application.engine_run import EngineLauncher
from cuanta.domain.agents import parse_agent
from cuanta.domain.engine import EngineEvent, EngineOutcome, EngineRequest, RunResult
from cuanta.domain.role_handoff import HandoffSource, HandoffStatus, RoleHandoff, phases_left
from cuanta.domain.routing import ROLES, Role
from tests.unit.test_cross_engine import REQUEST
from tests.unit.test_docs_beside import hashes, team
from tests.unit.test_steering import Recorder


class PhaseEngine:
    def __init__(self, answers: list[tuple[str, bool]]) -> None:
        self._answers = answers
        self.seniors = 0

    @property
    def name(self) -> str:
        return "claude"

    def available(self) -> bool:
        return True

    def version(self) -> str:
        return "x"

    def missing_flags(self) -> tuple[str, ...]:
        return ()

    def cancel(self) -> None:
        return None

    def command(self, request: EngineRequest) -> list[str]:
        return ["claude"]

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        role = next(
            (item.value for item in ROLES if f"You are the {item.value} of" in request.prompt), ""
        )
        text = '{"status": "done"}'
        if role == "senior":
            status, writes = self._answers[min(self.seniors, len(self._answers) - 1)]
            self.seniors += 1
            if writes:
                target = Path(request.cwd) / "src" / f"phase{self.seniors}.py"
                target.write_text("x = 1\n", encoding="utf-8")
            text = f'{{"status": "{status}", "summary": "phase {self.seniors}"}}'
        result = RunResult(True, "success", 0.1, 1, "s", (), text)
        on_event(result)
        return EngineOutcome(0, result, 0)


def run_team(tmp_path: Path, engine: PhaseEngine) -> list[Role]:
    origin = tmp_path / "project"
    (origin / "src").mkdir(parents=True)
    (origin / "src" / "cart.py").write_text("def total(): return 1\n", encoding="utf-8")
    ledger = MemoryLedger()
    counter = iter(range(100))

    def launcher(name: str) -> EngineLauncher:
        return EngineLauncher(
            engine,
            ledger,
            FixedClock(),
            lambda: f"RUN{next(counter)}",
            lambda size: b"\x01" * size,
            "shop",
            4318,
            None,
        )

    senior = parse_agent("---\nname: python-senior\ndescription: d\n---\nYou are the SENIOR.\n")
    assert senior is not None
    pipeline = CrossEnginePipeline(
        launcher,
        lambda: (senior,),
        FileCapsuleStore(tmp_path / "capsules"),
        str(origin),
        0.0,
        snapshot=lambda: hashes(origin),
    )
    report = pipeline.run(REQUEST, team(), Recorder())
    return [step.role for step in report.steps]


def test_the_senior_runs_again_while_its_handoff_says_phases_remain(tmp_path: Path) -> None:
    engine = PhaseEngine([("partial", True), ("partial", True), ("done", True)])
    roles = run_team(tmp_path, engine)
    assert roles == [Role.ANALYST, Role.SENIOR, Role.SENIOR, Role.SENIOR, Role.TESTER, Role.DOCS]


def test_a_senior_that_changed_nothing_hands_over_to_the_tester(tmp_path: Path) -> None:
    engine = PhaseEngine([("partial", True), ("partial", False)])
    assert run_team(tmp_path, engine).count(Role.SENIOR) == 2


def test_the_senior_runs_at_most_the_phase_limit(tmp_path: Path) -> None:
    engine = PhaseEngine([("partial", True)])
    roles = run_team(tmp_path, engine)
    assert roles.count(Role.SENIOR) == SENIOR_PHASES and roles[-2:] == [Role.TESTER, Role.DOCS]


@pytest.mark.parametrize(
    ("handoff", "expected"),
    [
        (RoleHandoff("senior", status=HandoffStatus.PARTIAL, source=HandoffSource.JSON), False),
        (
            RoleHandoff(
                "senior",
                status=HandoffStatus.PARTIAL,
                source=HandoffSource.JSON,
                files_changed=("a.py",),
            ),
            True,
        ),
        (
            RoleHandoff(
                "senior",
                status=HandoffStatus.PARTIAL,
                source=HandoffSource.FALLBACK,
                files_changed=("a.py",),
            ),
            False,
        ),
        (
            RoleHandoff(
                "senior",
                status=HandoffStatus.DONE,
                source=HandoffSource.JSON,
                files_changed=("a.py",),
            ),
            False,
        ),
    ],
)
def test_phases_are_left_only_on_a_structured_partial_handoff_with_changes(
    handoff: RoleHandoff, expected: bool
) -> None:
    assert phases_left((RoleHandoff("analyst"), handoff), "senior") is expected
    assert phases_left((handoff, RoleHandoff("tester")), "senior") is expected
    assert phases_left((), "senior") is False
