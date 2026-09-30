from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.engine_run import EngineLauncher, LaunchSpec
from cuanta.application.implementer import ImplementationSession, VerificationBaselines
from cuanta.application.mandate_flow import MandateOptions, per_role_run
from cuanta.application.verification import Verifier
from cuanta.domain.config import Config, layer_from_table, merge
from cuanta.domain.engine import EngineEvent, EngineOutcome, EngineRequest, RunResult
from cuanta.domain.errors import DomainFailure
from cuanta.domain.implementation import (
    VerificationDelta,
    baseline_key,
    implementation_fingerprint,
    implementation_profile,
    planned_steps,
    project_rules,
    repair_feedback,
    unavailable_checks,
    verification_delta,
)
from cuanta.domain.role_handoff import VerifyResult
from cuanta.ports.system import Completed
from tests.fakes import FakeRunner, FakeStream
from tests.unit.test_engine_profiles import REQUEST, flow

COMMANDS = ("npx tsc --noEmit", "npm run lint")
BASE_ERROR = "src/old.ts:2 error TS2322: old mismatch"
NEW_ERROR = "src/new.ts:8 error TS2322: new mismatch"


def checks(*errors: str) -> tuple[VerifyResult, ...]:
    return (
        VerifyResult(COMMANDS[0], 1 if errors else 0, errors=errors),
        VerifyResult(COMMANDS[1], 0),
    )


class CheckSequence:
    def __init__(self, *results: tuple[VerifyResult, ...]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self, commands: Sequence[str], stopped: Callable[[], bool]
    ) -> tuple[VerifyResult, ...]:
        self.calls.append(tuple(commands))
        return () if stopped() else self.results.pop(0)


def result(cost: float | None = 0.2, text: str = "done", turns: int = 1) -> RunResult:
    return RunResult(True, "success", cost, turns, "SESSION", text=text)


def record_turn(sent: list[str], text: str) -> bool:
    sent.append(text)
    return True


def test_baseline_delta_keeps_anchors_and_duplicate_new_errors() -> None:
    baseline = checks(BASE_ERROR)
    current = checks("src/old.ts:20 error TS2322: old mismatch", NEW_ERROR)
    delta = verification_delta(baseline, current, COMMANDS)
    assert delta.preexisting == (f"{COMMANDS[0]}: src/old.ts:20 error TS2322: old mismatch",)
    assert delta.introduced == (f"{COMMANDS[0]}: {NEW_ERROR}",)
    assert not delta.passed
    duplicates = verification_delta(
        baseline, checks(BASE_ERROR, BASE_ERROR.replace(":2", ":3")), COMMANDS
    )
    assert len(duplicates.preexisting) == len(duplicates.introduced) == 1
    assert verification_delta(baseline, checks(BASE_ERROR), COMMANDS).passed


@pytest.mark.parametrize(
    "current",
    [
        (),
        (VerifyResult(COMMANDS[0], None),),
        (VerifyResult(COMMANDS[0], 1),),
        (VerifyResult(COMMANDS[0], 0, timed_out=True),),
    ],
)
def test_missing_unavailable_and_timeout_checks_never_pass(
    current: tuple[VerifyResult, ...],
) -> None:
    assert not verification_delta(current, current, COMMANDS).passed


def test_baselines_persist_reuse_and_invalidate_on_source_and_lock_state(tmp_path: Path) -> None:
    workspace = LocalWorkspace(tmp_path)
    baseline = VerificationBaselines(workspace)
    verify = CheckSequence(checks(BASE_ERROR), checks(), checks())
    source = {"src/a.ts": "hash", "package-lock.json": "lock1"}
    assert baseline.get(source, COMMANDS, verify, lambda: False) == checks(BASE_ERROR)
    restored = VerificationBaselines(workspace)
    assert restored.get(source, COMMANDS, verify, lambda: False) == checks(BASE_ERROR)
    restored.get({**source, "package-lock.json": "lock2"}, COMMANDS, verify, lambda: False)
    restored.get({**source, "src/a.ts": "new"}, COMMANDS, verify, lambda: False)
    assert len(verify.calls) == 3
    assert baseline_key(source, COMMANDS, "env1") != baseline_key(source, COMMANDS, "env2")


def test_invalid_and_incomplete_baselines_are_never_reused(tmp_path: Path) -> None:
    workspace = LocalWorkspace(tmp_path)
    store = VerificationBaselines(workspace)
    path = f".cuanta/baselines/{baseline_key({}, COMMANDS)}.json"
    verify = CheckSequence((), checks())
    workspace.write_text(path, "corrupt")
    assert store.get({}, COMMANDS, verify, lambda: False) == ()
    assert store.get({}, COMMANDS, verify, lambda: False) == checks()
    assert len(verify.calls) == 2


def test_repair_keeps_context_and_sends_only_introduced_errors() -> None:
    verify = CheckSequence(checks(BASE_ERROR, NEW_ERROR), checks(BASE_ERROR))
    session = ImplementationSession(COMMANDS, checks(BASE_ERROR), verify, lambda: 0.0)
    turns: list[str] = []

    def send(text: str) -> bool:
        turns.append(text)
        return True

    session.on_result(result(), send)
    session.on_result(result(0.3), send)
    assert len(turns) == 1 and NEW_ERROR in turns[0] and BASE_ERROR not in turns[0]
    assert session.report.passed and session.report.repairs == 1
    assert session.report.steps[0].state == "green"
    assert session.report.payload()["checks"]
    outcome = EngineOutcome(0, result(0.3), 2)
    assert session.settle(outcome) is outcome


@pytest.mark.parametrize(
    ("cost", "cap", "turns", "maximum", "time", "reason"),
    [
        (None, 1.0, 1, 0, 0.0, "cost_unknown"),
        (0.97, 1.0, 1, 0, 0.0, "cost_limit"),
        (0.2, 1.0, 5, 5, 0.0, "turn_limit"),
        (0.2, 1.0, 1, 0, 901.0, "time_limit"),
    ],
)
def test_repair_rails_stop_followups(
    cost: float | None, cap: float, turns: int, maximum: int, time: float, reason: str
) -> None:
    now = [0.0]
    session = ImplementationSession(
        COMMANDS, checks(), CheckSequence(checks(NEW_ERROR)), lambda: now[0], cap, max_turns=maximum
    )
    now[0] = time
    sent: list[str] = []
    session.on_result(result(cost, turns=turns), lambda text: record_turn(sent, text))
    assert not sent and session.report.reason == reason
    settled = session.settle(EngineOutcome(0, result(cost), 1))
    assert not settled.ok and settled.result is not None
    assert reason in settled.result.text


def test_three_repair_rounds_then_stop_with_remaining_errors() -> None:
    verify = CheckSequence(*(checks(NEW_ERROR) for _ in range(4)))
    session = ImplementationSession(COMMANDS, checks(), verify, lambda: 0.0, max_repairs=10)
    sent: list[str] = []
    for _ in range(4):
        session.on_result(result(), lambda text: record_turn(sent, text))
    assert len(sent) == session.report.repairs == 3
    assert session.report.reason == "repair_limit"
    final = session.settle(EngineOutcome(0, result(), 1))
    assert final.result is not None and NEW_ERROR in final.result.text


def test_ordered_steps_stop_at_first_red_step() -> None:
    verify = CheckSequence(checks(), checks(NEW_ERROR))
    session = ImplementationSession(
        COMMANDS, checks(), verify, lambda: 0.0, max_repairs=0, stepped=True
    )
    sent: list[str] = []

    def send(text: str) -> bool:
        sent.append(text)
        return True

    session.on_result(result(text='{"implementation_steps":["one","two","three"]}'), send)
    session.on_result(result(), send)
    session.on_result(result(), send)
    assert len(sent) == 2 and "step 2/3" in sent[-1]
    assert [step.state for step in session.report.steps] == ["green", "failed", "pending"]
    assert not session.report.passed


def test_all_steps_green_and_malformed_plans_fail_without_writing_turn() -> None:
    session = ImplementationSession(
        COMMANDS, checks(), CheckSequence(checks(), checks()), lambda: 0.0, stepped=True
    )
    session.on_result(
        result(text='{"implementation_steps":[{"title":"one"},"two"]}'), lambda _: True
    )
    session.on_result(result(), lambda _: True)
    session.on_result(result(), lambda _: True)
    assert session.report.passed and all(step.state == "green" for step in session.report.steps)
    invalid = ImplementationSession(COMMANDS, checks(), CheckSequence(), lambda: 0.0, stepped=True)
    invalid.on_result(result(text="I plan to do this"), lambda _: pytest.fail("no followup"))
    assert invalid.report.reason == "invalid_step_plan"
    for text in ("{}", '{"implementation_steps":[]}', '{"implementation_steps":[{}]}'):
        assert planned_steps(text) == ()


def test_closed_session_and_engine_failures_remain_failures() -> None:
    session = ImplementationSession(
        COMMANDS, checks(), CheckSequence(checks(NEW_ERROR)), lambda: 0.0
    )
    session.on_result(result(), lambda _: False)
    assert session.report.reason == "session_closed"
    failed = ImplementationSession(COMMANDS, checks(), CheckSequence(), lambda: 0.0)
    failed.on_result(replace(result(), ok=False, subtype="error_max_turns"), lambda _: True)
    assert failed.report.reason == "turn_limit"
    assert failed.report.engine_subtype == "error_max_turns"
    missing = EngineOutcome(1, None, 0)
    assert failed.settle(missing) is missing


def test_wall_timer_cancels_a_silent_engine() -> None:
    halted = threading.Event()
    session = ImplementationSession(
        COMMANDS, checks(), CheckSequence(), lambda: 0.0, timeout_s=0.01
    )
    with session.running(halted.set):
        assert halted.wait(2)
    assert session.stopped() and session.report.reason == "time_limit"


class RepairStream(FakeStream):
    def lines(self) -> Iterator[str]:
        for index, line in enumerate(self.output):
            if index:
                assert not self.ended and len(self.sent) == 1
                assert NEW_ERROR in self.sent[0]
            yield line


def test_native_stream_result_verifies_and_repairs_before_closing_one_process(
    tmp_path: Path,
) -> None:
    stream = RepairStream(
        [
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": text,
                    "total_cost_usd": cost,
                    "num_turns": turns,
                    "session_id": "SESSION",
                }
            )
            for text, cost, turns in (("first", 0.2, 1), ("fixed", 0.3, 2))
        ]
    )
    runner = FakeRunner(
        streams={"claude": stream}, responses={"claude --help": Completed(0, "--input-format", "")}
    )
    engine = ClaudeCodeEngine(runner)
    ledger = MemoryLedger()
    session = ImplementationSession(
        COMMANDS, checks(), CheckSequence(checks(NEW_ERROR), checks()), lambda: 0.0
    )
    launcher = EngineLauncher(
        engine,
        ledger,
        FixedClock(),
        lambda: "R",
        lambda size: b"x" * size,
        "project",
        4318,
        None,
        implementer=lambda _: session,
    )
    events: list[EngineEvent] = []
    launch = launcher.launch(
        LaunchSpec("mandate", "fix", str(tmp_path), (), model="sonnet"), events.append
    )
    assert launch.outcome.ok and launch.outcome.result is not None
    assert launch.outcome.result.text == "fixed" and launch.run.cost_usd == 0.3
    assert launch.outcome.late_results == 0
    assert len([call for call in runner.calls if "-p" in call]) == 1
    assert stream.ended and stream.closed and runner.keeps == [True]
    assert launch.implementation is not None and launch.implementation.passed
    assert [event for event in events if isinstance(event, RunResult)] == [launch.outcome.result]


def test_native_planning_write_is_rejected_before_any_step_is_authorized(tmp_path: Path) -> None:
    from cuanta.bootstrap import Container

    source = tmp_path / "feature.ts"
    source.write_text("before", encoding="utf-8")

    class PlanningWriteStream(FakeStream):
        def lines(self) -> Iterator[str]:
            source.write_text("unauthorized edit", encoding="utf-8")
            yield from self.output

    stream = PlanningWriteStream(
        [
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": '{"implementation_steps":["write feature"]}',
                    "total_cost_usd": 0.2,
                    "num_turns": 1,
                }
            )
        ]
    )
    runner = FakeRunner(
        streams={"claude": stream}, responses={"claude --help": Completed(0, "--input-format", "")}
    )
    container = Container(tmp_path, Config(), runner=runner, home=tmp_path / "home")
    try:
        session = ImplementationSession(
            COMMANDS,
            checks(),
            CheckSequence(),
            lambda: 0.0,
            stepped=True,
            snapshot=container.project_snapshot,
        )
        launcher = EngineLauncher(
            ClaudeCodeEngine(runner),
            MemoryLedger(),
            FixedClock(),
            lambda: "R",
            lambda size: b"x" * size,
            "project",
            4318,
            None,
            implementer=lambda _: session,
        )
        launch = launcher.launch(
            LaunchSpec("mandate", "feature", str(tmp_path), ()), lambda _: None
        )
        assert not launch.outcome.ok and launch.outcome.result is not None
        assert launch.outcome.result.terminal_reason == "planning_changes"
        assert stream.sent == [] and stream.ended and stream.closed
        assert session.report.steps == () and session.report.repairs == 0
        assert session.report.payload()["repair_rounds_remaining"] == 3
    finally:
        container.close()


def test_planning_fingerprint_ignores_known_verification_outputs() -> None:
    snapshot = {"src/a.ts": "before", "tsconfig.tsbuildinfo": "old cache"}
    session = ImplementationSession(
        COMMANDS,
        checks(),
        CheckSequence(),
        lambda: 0.0,
        stepped=True,
        snapshot=lambda: snapshot,
    )
    snapshot.update(
        {"tsconfig.tsbuildinfo": "new cache", "coverage/report.json": "new", ".coverage": "new"}
    )
    turns: list[str] = []

    def send(text: str) -> bool:
        turns.append(text)
        return True

    session.on_result(
        result(text='{"implementation_steps":["write feature"]}'),
        send,
    )
    assert len(turns) == 1 and "step 1/1" in turns[0]
    assert session.report.reason == "" and session.report.steps[0].state == "running"


def test_ordered_implementation_requires_a_verifier_before_the_engine_starts(
    tmp_path: Path,
) -> None:
    from cuanta.bootstrap import Container

    runner = FakeRunner()
    container = Container(tmp_path, Config(), runner=runner, home=tmp_path / "home")
    try:
        spec = LaunchSpec("mandate", "feature", str(tmp_path), (), implementation_steps=True)
        with pytest.raises(DomainFailure, match="requires verification commands"):
            container.implementation_session(spec, MemoryLedger())
        assert not any("-p" in command for command in runner.calls)
        assert (
            container.implementation_session(
                replace(spec, implementation_steps=False), MemoryLedger()
            )
            is None
        )
    finally:
        container.close()


def test_verifier_retains_early_and_late_diagnostics_and_eslint_anchors(tmp_path: Path) -> None:
    lines = [
        "src/first.ts:1 error first",
        *["noise"] * 1000,
        str(tmp_path / "src" / "last.tsx"),
        "  9:2 error synchronous setState react-hooks/set-state-in-effect",
    ]
    runner = FakeRunner(streams={"npm run lint": FakeStream(lines, 1)})
    verified = Verifier(runner, tmp_path, False).run(("npm run lint",))[0]
    assert "src/first.ts:1 error first" in verified.errors
    assert (
        "src/last.tsx:9:2 synchronous setState react-hooks/set-state-in-effect" in verified.errors
    )


def test_typecheck_and_lint_overlap_then_build_runs(tmp_path: Path) -> None:
    barrier = threading.Barrier(2)
    finished = threading.Event()

    class Parallel(FakeStream):
        def lines(self) -> Iterator[str]:
            barrier.wait(timeout=2)
            finished.set()
            yield "checked"

    class Build(FakeStream):
        def lines(self) -> Iterator[str]:
            assert finished.is_set()
            yield "built"

    runner = FakeRunner(
        streams={COMMANDS[0]: Parallel([]), COMMANDS[1]: Parallel([]), "npm run build": Build([])}
    )
    verified = Verifier(runner, tmp_path, False).run_checks((*COMMANDS, "npm run build"))
    assert len(verified) == 3 and all(item.passed for item in verified)


def test_eslint_paths_with_spaces_cannot_mask_a_new_failure(tmp_path: Path) -> None:
    root = tmp_path / "My Project"
    root.mkdir()
    runner = FakeRunner(
        queued={
            "npm run lint": [
                FakeStream(
                    [str(root / name), "  9:2 error invalid state react-hooks/set-state-in-effect"],
                    1,
                )
                for name in ("a.tsx", "b.tsx")
            ]
        }
    )
    verifier = Verifier(runner, root, True)
    baseline = verifier.run(("npm run lint",))
    current = verifier.run(("npm run lint",))
    delta = verification_delta(baseline, current, ("npm run lint",))
    assert baseline[0].errors[0].startswith("a.tsx:9")
    assert delta.introduced[0].startswith("npm run lint: b.tsx:9")
    assert not delta.preexisting and not delta.passed


def test_fast_profile_defaults_and_pins_native_writer(tmp_path: Path) -> None:
    engine = ClaudeCodeEngine(FakeRunner())
    service = flow(tmp_path, engine)
    prepared = service.prepare(
        REQUEST, 0, MandateOptions(profile="fast", model="sonnet", variant="low")
    )
    assert prepared.spec.profile == "fast" and prepared.spec.pure and prepared.spec.effort == "low"
    assert prepared.spec.shape == "single" and not prepared.spec.agents_file
    assert prepared.spec.read_discipline is False and prepared.composed.single
    assert {"Agent", "Task"}.isdisjoint(prepared.spec.allowed_tools)
    assert {"Workflow", "Agent", "RemoteTrigger", "CronCreate", "SendMessage"}.issubset(
        prepared.spec.disallowed_tools
    )
    sent = prepared.launcher.request(prepared.spec, "R", "trace", None)
    assert sent.env["CLAUDE_CODE_SUBAGENT_MODEL_FORCE"] == "1"
    assert sent.env["ANTHROPIC_SMALL_FAST_MODEL"] == "claude-sonnet-5"
    assert not per_role_run(
        MandateOptions(profile="fast", shape="scout", scout_mode="launch"), "feature", "claude"
    )
    assert implementation_profile("").value == Config().implementation_profile == "balanced"
    configured = merge([layer_from_table({"runs": {"profile": "fast", "repair_rounds": 2}})])
    assert configured.implementation_profile == "fast" and configured.repair_rounds == 2
    assert (
        layer_from_table({"runs": {"profile": "wrong", "repair_rounds": 4, "repair_timeout_s": 0}})
        == {}
    )


def test_fast_rejects_investigation_missing_model_and_allows_forced_ultracode(
    tmp_path: Path,
) -> None:
    service = flow(tmp_path, ClaudeCodeEngine(FakeRunner()))
    with pytest.raises(DomainFailure, match="requires"):
        service.prepare(
            replace(REQUEST, type="investigation"),
            0,
            MandateOptions(profile="fast", model="sonnet"),
        )
    with pytest.raises(DomainFailure, match="requires"):
        service.prepare(REQUEST, 0, MandateOptions(profile="fast"))
    prepared = service.prepare(
        REQUEST, 0, MandateOptions(profile="fast", model="opus", variant="ultracode")
    )
    assert {"Agent", "Workflow"}.issubset(prepared.spec.allowed_tools)
    assert {"Agent", "Workflow"}.isdisjoint(prepared.spec.disallowed_tools)
    assert {"RemoteTrigger", "CronCreate", "ScheduleWakeup"}.issubset(
        prepared.spec.disallowed_tools
    )
    assert prepared.spec.pure and prepared.spec.effort == "xhigh"


def test_rules_summary_carries_real_ts_and_react_constraints() -> None:
    rules = project_rules(
        {
            "tsconfig.json": '{"compilerOptions":{"strict":true}}',
            "eslint.config.mjs": "export default [reactHooks.configs.recommended]",
        }
    )
    assert '"strict":true' in rules and "set-state-in-effect" in rules
    assert "Project lint and TypeScript rules" in rules
    assert project_rules({"tsconfig.json": "broken json"})
    assert project_rules({}) == ""


def test_tool_allowlist_reaches_actual_claude_command_and_cannot_expand_guards(
    tmp_path: Path,
) -> None:
    from cuanta.domain.change_plan import ChangePlan

    service = flow(tmp_path, ClaudeCodeEngine(FakeRunner()))
    settings = merge([layer_from_table({"runs": {"tools": ["Write"]}})])
    service._implementation_tools = settings.implementation_tools
    service._change_plan = lambda _: ChangePlan(guard=("secret/**",))
    prepared = service.prepare(REQUEST, 0, MandateOptions(profile="fast", model="sonnet"))
    command = prepared.composed.command
    assert command[command.index("--tools") + 1] == "Write"
    assert prepared.spec.tools == ("Write",)
    assert layer_from_table({"runs": {"tools": ["unknown"]}}) == {}
    assert layer_from_table({"runs": {"tools": ["Write", "Write"]}}) == {}


def test_feedback_is_bounded_but_all_errors_and_remaining_rounds_are_reported() -> None:
    errors = tuple(f"src/a.ts:{line} error " + "x" * 200 for line in range(100))
    feedback = repair_feedback(errors)
    assert len(feedback.encode()) <= 8000 and "further errors" in feedback
    session = ImplementationSession(
        COMMANDS, checks(), CheckSequence(checks(*errors)), lambda: 0.0, cap_usd=0.2
    )
    session.on_result(result(), lambda _: pytest.fail("budget exhausted"))
    report = session.report.payload()
    assert report["repair_rounds_remaining"] == 3 and report["reason"] == "cost_limit"
    assert len(session.report.checks[0].introduced) == len(errors)


def test_incomplete_next_step_cannot_be_reported_green() -> None:
    session = ImplementationSession(
        COMMANDS, checks(), CheckSequence(checks()), lambda: 0.0, stepped=True
    )
    session.on_result(result(text='{"implementation_steps":["one","two"]}'), lambda _: True)
    session.on_result(result(), lambda _: True)
    assert not session.report.passed
    assert [step.state for step in session.report.steps] == ["green", "running"]
    assert not session.settle(EngineOutcome(0, result(), 0)).ok


def test_cross_writer_uses_same_session_loop_without_a_second_verifier(tmp_path: Path) -> None:
    from cuanta.adapters.storage.capsule_store import FileCapsuleStore
    from cuanta.application.cross_engine import CrossEnginePipeline
    from cuanta.domain.change_plan import ChangePlan
    from tests.unit.test_cross_engine import Recorder, ScriptedEngine, plan

    prompts: list[str] = []
    turns: list[str] = []
    ledger = MemoryLedger()
    sequence = iter(range(10))
    file = tmp_path / "a.ts"

    class Actor(ScriptedEngine):
        def accepts_turns(self) -> bool:
            return True

        def send_turn(self, text: str) -> bool:
            turns.append(text)
            return True

        def run(
            self, request: EngineRequest, on_event: Callable[[EngineEvent], None]
        ) -> EngineOutcome:
            prompts.append(request.prompt)
            response = result(text='{"summary":"finished","status":"done"}')
            if request.model == "model-senior":
                assert request.stream_input and request.continue_results
                file.write_text("changed", encoding="utf-8")
                on_event(response)
                assert len(turns) == 1 and NEW_ERROR in turns[0]
            on_event(response)
            return EngineOutcome(0, response, 0)

    def implementation(spec: LaunchSpec) -> ImplementationSession | None:
        if spec.read_only:
            return None
        verification = (
            CheckSequence(checks(NEW_ERROR), checks())
            if spec.role == "senior"
            else CheckSequence(checks())
        )
        return ImplementationSession(COMMANDS, checks(), verification, lambda: 0.0)

    def launcher(name: str) -> EngineLauncher:
        return EngineLauncher(
            Actor(name, prompts),
            ledger,
            FixedClock(),
            lambda: f"R{next(sequence)}",
            lambda size: b"x" * size,
            "project",
            4318,
            None,
            implementer=implementation,
        )

    pipeline = CrossEnginePipeline(
        launcher,
        tuple,
        FileCapsuleStore(tmp_path / "capsules"),
        str(tmp_path),
        5.0,
        change_plan=lambda _: ChangePlan(verify=COMMANDS),
        snapshot=lambda: {"a.ts": file.read_text("utf-8")} if file.exists() else {},
        verifier=lambda commands, stopped: pytest.fail("same-session checks must be reused"),
    )
    report = pipeline.run(REQUEST, plan(), Recorder())
    assert report.ok and len(prompts) == 3 and len(turns) == 1
    assert len(report.verifications) == 2 and report.verifications[-1].passed


def test_a_cross_pipeline_refuses_unusable_checks_before_any_paid_role(tmp_path: Path) -> None:
    from cuanta.adapters.storage.capsule_store import FileCapsuleStore
    from cuanta.application.cross_engine import CrossEnginePipeline
    from cuanta.domain.change_plan import ChangePlan
    from tests.unit.test_cross_engine import Recorder, ScriptedEngine, plan

    prompts: list[str] = []
    asked: list[tuple[str, int, tuple[str, ...]]] = []
    usable: list[bool] = []
    ledger = MemoryLedger()
    sequence = iter(range(10))

    class Steerable(ScriptedEngine):
        def accepts_turns(self) -> bool:
            return True

        def send_turn(self, text: str) -> bool:
            return True

    def implementation(spec: LaunchSpec) -> ImplementationSession | None:
        asked.append((spec.role, len(prompts), spec.verify_commands))
        if not usable:
            raise DomainFailure("verification is unusable: npm test (fails without diagnostics)")
        return None

    def launcher(name: str) -> EngineLauncher:
        return EngineLauncher(
            Steerable(name, prompts) if name == "claude" else ScriptedEngine(name, prompts),
            ledger,
            FixedClock(),
            lambda: f"R{next(sequence)}",
            lambda size: b"x" * size,
            "project",
            4318,
            None,
            implementer=implementation,
        )

    pipeline = CrossEnginePipeline(
        launcher,
        tuple,
        FileCapsuleStore(tmp_path / "capsules"),
        str(tmp_path),
        5.0,
        change_plan=lambda _: ChangePlan(verify=("npm test",)),
    )
    with pytest.raises(DomainFailure, match="fails without diagnostics"):
        pipeline.run(REQUEST, plan(), Recorder())
    assert asked == [("tester", 0, ("npm test",))]
    assert not prompts and not ledger.runs()
    usable.append(True)
    report = pipeline.run(REQUEST, plan(), Recorder())
    assert report.ok and len(prompts) == 3
    assert [(role, seen) for role, seen, _ in asked[1:]] == [("tester", 0), ("tester", 2)]


def test_files_written_by_preflight_checks_are_not_charged_to_the_first_role(
    tmp_path: Path,
) -> None:
    from cuanta.adapters.storage.capsule_store import FileCapsuleStore
    from cuanta.application.cross_engine import CrossEnginePipeline
    from cuanta.domain.change_plan import ChangePlan
    from cuanta.domain.routing import Role
    from tests.unit.test_cross_engine import Recorder, ScriptedEngine, plan

    prompts: list[str] = []
    ledger = MemoryLedger()
    sequence = iter(range(10))
    coverage = tmp_path / ".coverage"

    class Steerable(ScriptedEngine):
        def accepts_turns(self) -> bool:
            return True

        def send_turn(self, text: str) -> bool:
            return True

    def implementation(spec: LaunchSpec) -> ImplementationSession | None:
        coverage.write_text(f"checked after {len(prompts)} prompts", encoding="utf-8")
        return None

    def launcher(name: str) -> EngineLauncher:
        return EngineLauncher(
            Steerable(name, prompts) if name == "claude" else ScriptedEngine(name, prompts),
            ledger,
            FixedClock(),
            lambda: f"R{next(sequence)}",
            lambda size: b"x" * size,
            "project",
            4318,
            None,
            implementer=implementation,
        )

    pipeline = CrossEnginePipeline(
        launcher,
        tuple,
        FileCapsuleStore(tmp_path / "capsules"),
        str(tmp_path),
        5.0,
        change_plan=lambda _: ChangePlan(verify=("npm test",)),
        snapshot=lambda: {".coverage": coverage.read_text("utf-8")} if coverage.exists() else {},
    )
    report = pipeline.run(REQUEST, plan(), Recorder())
    assert report.ok and not report.guard_role and len(prompts) == 3
    assert report.steps[0].role is Role.ANALYST and report.steps[0].changed_files == ()
    assert report.steps[-1].role is Role.TESTER and report.steps[-1].changed_files == (".coverage",)


def test_pure_per_role_plan_pins_one_model_and_rejects_conflicting_role_pins() -> None:
    from cuanta.application.cross_engine import pure_plan
    from cuanta.domain.routing import Role
    from tests.unit.test_cross_engine import plan

    original = plan()
    with pytest.raises(DomainFailure, match="every role"):
        pure_plan(original, "sonnet")
    claude = replace(
        original,
        routes=tuple(
            replace(route, model=replace(route.model, engine="claude"))
            if route.model is not None
            else route
            for route in original.routes
        ),
    )
    pure = pure_plan(claude, "sonnet")
    assert {route.model.id for route in pure.routes if route.model is not None} == {"sonnet"}
    with pytest.raises(DomainFailure, match="explicit"):
        pure_plan(claude, "")
    conflicting = replace(claude, policy=replace(claude.policy, role_models={Role.SENIOR: "opus"}))
    with pytest.raises(DomainFailure, match="conflicts"):
        pure_plan(conflicting, "sonnet")


def test_actual_context_pack_contains_project_configuration_rules(tmp_path: Path) -> None:
    from cuanta.bootstrap import Container
    from cuanta.domain.mandate import MandateRequest

    (tmp_path / "a.tsx").write_text("export const title = 'Hello';\n", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{"strict":true}}', encoding="utf-8")
    (tmp_path / "eslint.config.mjs").write_text(
        "export default [reactHooks.configs.recommended];", encoding="utf-8"
    )
    container = Container.for_project(tmp_path)
    try:
        pack = container.context_pack(MandateRequest("feature", "edit a.tsx"), "quick")
        assert "set-state-in-effect" in pack.stable_prefix
        assert '"strict":true' in pack.stable_prefix
        assert any(item.key == "project-rules" for item in pack.items)
    finally:
        container.close()


def test_typecheck_retains_full_paths_with_spaces_and_complete_error_messages(
    tmp_path: Path,
) -> None:
    prefix = "error TS2322: " + "same prefix " * 35
    old = str(tmp_path / "Old Folder" / "a.ts") + f"(4,2): {prefix}old detail"
    new = str(tmp_path / "New Folder" / "a.ts") + f"(4,2): {prefix}new detail"
    runner = FakeRunner(queued={"npx tsc": [FakeStream([old], 1), FakeStream([old, new], 1)]})
    verifier = Verifier(runner, tmp_path, False)
    baseline = verifier.run((COMMANDS[0],))
    current = verifier.run((COMMANDS[0],))
    assert baseline[0].errors == (f"Old Folder/a.ts:4:2 {prefix}old detail",)
    delta = verification_delta(baseline, current, (COMMANDS[0],))
    assert not delta.passed and len(delta.preexisting) == 1
    assert delta.introduced == (f"{COMMANDS[0]}: New Folder/a.ts:4:2 {prefix}new detail",)


def test_lint_error_suffixes_beyond_the_display_limit_remain_distinct(tmp_path: Path) -> None:
    path = str(tmp_path / "a.tsx")
    prefix = "same message " * 35
    runner = FakeRunner(
        queued={
            "npm run lint": [
                FakeStream([path, f"  1:2 error {prefix}old-rule"], 1),
                FakeStream([path, f"  1:2 error {prefix}new-rule"], 1),
            ]
        }
    )
    verifier = Verifier(runner, tmp_path, False)
    baseline = verifier.run((COMMANDS[1],))
    current = verifier.run((COMMANDS[1],))
    delta = verification_delta(baseline, current, (COMMANDS[1],))
    assert not delta.passed and not delta.preexisting
    assert delta.introduced == (f"{COMMANDS[1]}: a.tsx:1:2 {prefix}new-rule",)


ANY_ROW = "error  Unexpected any. Specify a different type  @typescript-eslint/no-explicit-any"
ANY_MESSAGE = "Unexpected any. Specify a different type @typescript-eslint/no-explicit-any"


def compared(
    tmp_path: Path, command: str, before: list[str], after: list[str]
) -> VerificationDelta:
    runner = FakeRunner(queued={command: [FakeStream(before, 1), FakeStream(after, 1)]})
    verifier = Verifier(runner, tmp_path, False)
    baseline = verifier.run((command,))
    return verification_delta(baseline, verifier.run((command,)), (command,))


def test_a_new_same_line_duplicate_is_introduced_while_line_shifts_stay_known(
    tmp_path: Path,
) -> None:
    card = str(tmp_path / "src" / "components" / "Card.tsx")
    lint = compared(
        tmp_path,
        COMMANDS[1],
        [card, f"  12:15  {ANY_ROW}"],
        [card, f"  12:15  {ANY_ROW}", f"  12:26  {ANY_ROW}"],
    )
    assert not lint.passed
    assert lint.preexisting == (f"{COMMANDS[1]}: src/components/Card.tsx:12:15 {ANY_MESSAGE}",)
    assert lint.introduced == (f"{COMMANDS[1]}: src/components/Card.tsx:12:26 {ANY_MESSAGE}",)
    possibly = "error TS18048: 'user' is possibly 'undefined'."
    typecheck = compared(
        tmp_path,
        COMMANDS[0],
        [f"src/a.ts(10,5): {possibly}"],
        [f"src/a.ts(10,5): {possibly}", f"src/a.ts(10,20): {possibly}"],
    )
    assert not typecheck.passed
    assert typecheck.introduced == (f"{COMMANDS[0]}: src/a.ts:10:20 {possibly}",)
    shifted = compared(
        tmp_path, COMMANDS[1], [card, f"  12:15  {ANY_ROW}"], [card, f"  14:15  {ANY_ROW}"]
    )
    assert shifted.passed and len(shifted.preexisting) == 1


def test_lint_blocks_of_any_extension_keep_their_own_file(tmp_path: Path) -> None:
    util = str(tmp_path / "src" / "util.ts")
    app = str(tmp_path / "src" / "App.vue")
    unused = "error  'x' is assigned a value but never used  no-unused-vars"
    delta = compared(
        tmp_path,
        COMMANDS[1],
        [util, f"  5:9  {ANY_ROW}", f"  40:1  {unused}"],
        [util, f"  40:1  {unused}", "", app, f"  30:12  {ANY_ROW}"],
    )
    assert not delta.passed
    assert delta.introduced == (f"{COMMANDS[1]}: src/App.vue:30:12 {ANY_MESSAGE}",)
    assert delta.preexisting == (
        f"{COMMANDS[1]}: src/util.ts:40:1 'x' is assigned a value but never used no-unused-vars",
    )
    orphan = FakeRunner(
        streams={COMMANDS[1]: FakeStream([util, f"  5:9  {ANY_ROW}", "", f"  7:1  {ANY_ROW}"], 1)}
    )
    errors = Verifier(orphan, tmp_path, False).run((COMMANDS[1],))[0].errors
    assert errors[0] == f"src/util.ts:5:9 {ANY_MESSAGE}"
    assert not any(error.startswith("src/util.ts:7") for error in errors)


def test_next_lint_rows_keep_their_file_and_ignore_line_shifts(tmp_path: Path) -> None:
    undefined = "Error: 'x' is not defined.  no-undef"
    delta = compared(
        tmp_path,
        COMMANDS[1],
        ["./src/app/page.tsx", f"10:5  {undefined}"],
        [
            "./src/app/page.tsx",
            f"12:5  {undefined}",
            "3:1  Warning: Unexpected console statement.  no-console",
        ],
    )
    assert delta.preexisting == (
        f"{COMMANDS[1]}: src/app/page.tsx:12:5 'x' is not defined. no-undef",
    )
    assert delta.introduced == (
        f"{COMMANDS[1]}: src/app/page.tsx:3:1 Unexpected console statement. no-console",
    )


@pytest.mark.parametrize(
    ("command", "before", "after"),
    [
        (
            "uv run pytest",
            [
                "tests/test_a.py:10: AssertionError",
                "FAILED tests/test_a.py::test_x - assert 1 == 2",
                "==== 1 failed, 5 passed in 0.12s ====",
            ],
            [
                "tests/test_a.py:10: AssertionError",
                "FAILED tests/test_a.py::test_x - assert 1 == 2",
                "==== 1 failed, 6 passed in 0.15s ====",
            ],
        ),
        (
            "npm run lint",
            [
                "src/a.ts",
                "  2:1  error  'a' is not defined  no-undef",
                "  3:1  error  'b' is not defined  no-undef",
                "",
                "✖ 2 problems (2 errors, 0 warnings)",
            ],
            [
                "src/a.ts",
                "  2:1  error  'a' is not defined  no-undef",
                "",
                "✖ 1 problem (1 error, 0 warnings)",
                "  1 error and 0 warnings potentially fixable with the `--fix` option.",
            ],
        ),
        (
            "uv run mypy src",
            [
                'src/a.py:3: error: Name "x" is not defined  [name-defined]',
                'src/a.py:5: error: Name "y" is not defined  [name-defined]',
                "Found 2 errors in 1 file (checked 10 source files)",
            ],
            [
                'src/a.py:4: error: Name "x" is not defined  [name-defined]',
                "Found 1 error in 1 file (checked 11 source files)",
            ],
        ),
    ],
)
def test_summary_counts_and_durations_never_become_new_errors(
    tmp_path: Path, command: str, before: list[str], after: list[str]
) -> None:
    delta = compared(tmp_path, command, before, after)
    assert delta.passed and delta.preexisting and not delta.introduced
    worse = compared(tmp_path, command, before, [*after, "src/new.py:8: error: new failure"])
    assert worse.introduced == (f"{command}: src/new.py:8 error: new failure",)


def test_summaries_count_only_when_nothing_else_parsed_and_exit_codes_stay_blockers(
    tmp_path: Path,
) -> None:
    jest = ["Tests:       1 failed, 5 passed, 6 total"]
    assert compared(tmp_path, "npm test", jest, jest).passed
    worse = ["Tests:       2 failed, 4 passed, 6 total"]
    assert not compared(tmp_path, "npm test", jest, worse).passed
    silent = compared(tmp_path, "npm test", ["done"], ["done"])
    assert unavailable_checks(silent.results, ("npm test",)) == (
        "npm test (fails without diagnostics)",
    )
    broken = verification_delta((VerifyResult("npm test", 0),), silent.results, ("npm test",))
    assert broken.introduced == ("npm test: failed with exit code 1",)


JEST_ADDS = [
    " FAIL  src/a.test.js",
    "  ● math › adds",
    "",
    "    expect(received).toBe(expected) // Object.is equality",
    "",
    "    Expected: 4",
    "    Received: 3",
]
JEST_SUBTRACTS = [
    "  ● math › subtracts",
    "",
    "    expect(received).toBe(expected) // Object.is equality",
    "",
    "    Expected: 1",
    "    Received: 2",
]
TYPE_ERROR = "    TypeError: Cannot read properties of undefined (reading 'x')"
YARN_FAILED = "error Command failed with exit code 1."
PNPM_FAILED = " ELIFECYCLE  Test failed. See above for more details."
VITEST_ADDS = [
    " ❯ src/a.test.ts (6 tests | 1 failed) 5ms",
    " FAIL  src/a.test.ts > math > adds",
    "AssertionError: expected 3 to be 4 // Object.is equality",
    " ❯ src/a.test.ts:4:21",
]
SMOKE = ["  ● renders without crashing", "", "    expect(received).toBeTruthy()", ""]
APP_FAILS = [" FAIL  src/App.test.js", *SMOKE]
HEADER_FAILS = [" FAIL  src/Header.test.js", *SMOKE]
SMOKE_TITLE = "src/Header.test.js ● renders without crashing"


@pytest.mark.parametrize(
    ("command", "before", "after", "title"),
    [
        (
            "yarn test",
            [*JEST_ADDS, YARN_FAILED],
            [*JEST_ADDS, *JEST_SUBTRACTS, YARN_FAILED],
            "src/a.test.js ● math › subtracts",
        ),
        (
            "npm test",
            [*JEST_ADDS, "  console.error", "    Warning: an update was not wrapped"],
            [
                *JEST_ADDS,
                *JEST_SUBTRACTS,
                "  console.error",
                "    Warning: an update was not wrapped",
            ],
            "src/a.test.js ● math › subtracts",
        ),
        (
            "npm test",
            [*JEST_ADDS, TYPE_ERROR],
            [*JEST_ADDS, TYPE_ERROR, *JEST_SUBTRACTS],
            "src/a.test.js ● math › subtracts",
        ),
        (
            "pnpm test",
            [*JEST_ADDS, PNPM_FAILED],
            [*JEST_ADDS, *JEST_SUBTRACTS, PNPM_FAILED],
            "src/a.test.js ● math › subtracts",
        ),
        (
            "npm test",
            [*APP_FAILS, "Tests:       1 failed, 1 passed, 2 total"],
            [*APP_FAILS, *HEADER_FAILS, "Tests:       2 failed, 2 total"],
            SMOKE_TITLE,
        ),
        (
            "npm test",
            [*APP_FAILS, " PASS  src/Header.test.js"],
            [" PASS  src/App.test.js", *HEADER_FAILS],
            SMOKE_TITLE,
        ),
        (
            "python -m unittest",
            [
                "ERROR: test_b (tests.test_a.T.test_b)",
                "TypeError: cannot unpack non-iterable int object",
                "FAILED (errors=1)",
            ],
            [
                "ERROR: test_b (tests.test_a.T.test_b)",
                "TypeError: cannot unpack non-iterable int object",
                "FAIL: test_c (tests.test_a.T.test_c)",
                "AssertionError: 1 != 2",
                "FAILED (failures=1, errors=1)",
            ],
            "FAIL: test_c (tests.test_a.T.test_c)",
        ),
        (
            "pnpm vitest run",
            [*VITEST_ADDS, TYPE_ERROR, PNPM_FAILED],
            [
                *VITEST_ADDS,
                TYPE_ERROR,
                " FAIL  src/a.test.ts > math > subtracts",
                " ❯ src/a.test.ts:9:21",
                PNPM_FAILED,
            ],
            "FAIL src/a.test.ts > math > subtracts",
        ),
        (
            "yarn mocha",
            [
                "    1) fails a",
                "  1 failing",
                "  1) Array",
                "     TypeError: Cannot read properties of undefined",
                YARN_FAILED,
            ],
            [
                "    1) fails new",
                "    2) fails a",
                "  2 failing",
                "  1) Array",
                "     AssertionError [ERR_ASSERTION]: 1 == 2",
                "  2) Array",
                "     TypeError: Cannot read properties of undefined",
                YARN_FAILED,
            ],
            "1) fails new",
        ),
    ],
)
def test_a_new_failing_test_is_introduced_beside_constant_error_lines(
    tmp_path: Path, command: str, before: list[str], after: list[str], title: str
) -> None:
    assert compared(tmp_path, command, before, before).passed
    delta = compared(tmp_path, command, before, after)
    assert not delta.passed
    assert f"{command}: {title}" in delta.introduced
    assert not any("exit code" in error or "ELIFECYCLE" in error for error in delta.introduced)


def test_added_passing_tests_and_new_durations_keep_a_known_failure_known(tmp_path: Path) -> None:
    jest = [*JEST_ADDS, "Tests:       1 failed, 20 passed, 21 total"]
    more = [*JEST_ADDS, "Tests:       1 failed, 25 passed, 26 total"]
    added = compared(tmp_path, "npm test", jest, more)
    assert added.passed and added.preexisting == ("npm test: src/a.test.js ● math › adds",)
    slower = [line.replace("5ms", "7ms") for line in VITEST_ADDS]
    timed = compared(tmp_path, "npx vitest run", VITEST_ADDS, slower)
    assert timed.passed and not timed.introduced and timed.preexisting


def test_repeated_jest_titles_count_but_the_failing_test_summary_reprint_does_not(
    tmp_path: Path,
) -> None:
    twice = [*JEST_ADDS, "", "  ● math › adds", "", "    Expected: 5"]
    repeated = compared(tmp_path, "npm test", JEST_ADDS, twice)
    assert repeated.introduced == ("npm test: src/a.test.js ● math › adds",)
    assert repeated.preexisting == ("npm test: src/a.test.js ● math › adds",)
    reprint = [*JEST_ADDS, "", "Summary of all failing tests", *JEST_ADDS]
    reprinted = compared(tmp_path, "npm test", JEST_ADDS, reprint)
    assert reprinted.passed and reprinted.preexisting == ("npm test: src/a.test.js ● math › adds",)
    doubled = [*twice, "", "Summary of all failing tests", *twice]
    assert compared(tmp_path, "npm test", reprint, doubled).introduced == repeated.introduced


VITEST_WORDS = [
    " ❯ src/a.test.ts (2 tests | 1 failed) 5ms",
    "   × throws an error on bad input 3ms",
    "     → expected 3 to be 4",
    "   ✓ returns an error for empty input 1ms",
    " FAIL  src/a.test.ts > throws an error on bad input",
    "AssertionError: expected 3 to be 4 // Object.is equality",
    " ❯ src/a.test.ts:4:21",
]
JEST_VERBOSE = [
    " FAIL  src/a.test.js",
    "  math",
    "    ✕ adds (3 ms)",
    "    ✓ returns error object (1 ms)",
    "",
    "  ● math › adds",
    "",
    "    expect(received).toBe(expected) // Object.is equality",
]


@pytest.mark.parametrize(
    ("command", "before", "after"),
    [
        (
            "npx vitest run",
            VITEST_WORDS,
            [
                line.replace("3ms", "4ms").replace("1ms", "2ms").replace("5ms", "7ms")
                for line in VITEST_WORDS
            ],
        ),
        (
            "npm test",
            JEST_VERBOSE,
            [line.replace("(1 ms)", "(2 ms)").replace("(3 ms)", "(5 ms)") for line in JEST_VERBOSE],
        ),
        (
            "npm test",
            JEST_VERBOSE,
            [
                *JEST_VERBOSE[:4],
                "    ✓ shows an error message when empty (2 ms)",
                *JEST_VERBOSE[4:],
            ],
        ),
        (
            "npm test",
            [line.replace("✓", "√") for line in JEST_VERBOSE],
            [line.replace("✓", "√").replace("(1 ms)", "(4 ms)") for line in JEST_VERBOSE],
        ),
    ],
)
def test_error_words_in_timed_or_passing_test_lines_keep_known_failures_known(
    tmp_path: Path, command: str, before: list[str], after: list[str]
) -> None:
    delta = compared(tmp_path, command, before, after)
    assert delta.passed and delta.preexisting and not delta.introduced
    assert not any("✓" in error or "√" in error for error in delta.preexisting)
    worse = compared(tmp_path, command, before, [*after, "src/new.ts:8: error: new failure"])
    assert worse.introduced == (f"{command}: src/new.ts:8 error: new failure",)


def test_a_replaced_failing_test_in_the_same_file_is_introduced(tmp_path: Path) -> None:
    replaced = [
        line.replace("math > adds", "math > subtracts").replace(":4:21", ":9:21")
        for line in VITEST_ADDS
    ]
    delta = compared(tmp_path, "npx vitest run", VITEST_ADDS, replaced)
    assert delta.introduced == ("npx vitest run: FAIL src/a.test.ts > math > subtracts",)
    shifted = [line.replace(":4:21", ":9:21") for line in VITEST_ADDS]
    moved = compared(tmp_path, "npx vitest run", VITEST_ADDS, shifted)
    assert moved.passed and len(moved.preexisting) == 2


class MissingTool(FakeRunner):
    def stream(
        self,
        args: Sequence[str],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        stdin_text: str | None = None,
        unset: Sequence[str] = (),
        keep_stdin: bool = False,
    ) -> FakeStream:
        if args[0] == "missing-tool":
            raise FileNotFoundError("[WinError 2] not found")
        return super().stream(args, cwd, env, stdin_text, unset, keep_stdin)


def test_an_unavailable_baseline_check_refuses_before_the_writer_starts(tmp_path: Path) -> None:
    from cuanta.bootstrap import Container

    runner = MissingTool()
    container = Container(tmp_path, Config(), runner=runner, home=tmp_path / "home")
    try:
        spec = LaunchSpec(
            "mandate", "feature", str(tmp_path), (), verify_commands=("missing-tool check",)
        )
        with pytest.raises(DomainFailure, match="missing-tool check"):
            container.implementation_session(spec, MemoryLedger())
        assert not any("-p" in command for command in runner.calls)
        ready = container.implementation_session(
            replace(spec, verify_commands=("npm run check",)), MemoryLedger()
        )
        assert ready is not None and ready.baseline[0].passed
    finally:
        container.close()
    timed_out = (VerifyResult("npm run build", 1, timed_out=True),)
    assert unavailable_checks(timed_out, ("npm run build", "npm run lint")) == (
        "npm run lint (did not run)",
        "npm run build (timed out)",
    )
    assert unavailable_checks(checks(BASE_ERROR), COMMANDS) == ()


@pytest.mark.parametrize(
    "output",
    [
        ["1 passing (5ms)", "1 failing", "AssertionError [ERR_ASSERTION]: 1 == 2"],
        [YARN_FAILED],
        [PNPM_FAILED],
        ["npm ERR! code ELIFECYCLE", "npm ERR! Failed at the app@1.0.0 test script."],
        ["npm error Lifecycle script `test` failed with error:", "npm error code 1"],
    ],
)
def test_a_baseline_failing_without_diagnostics_refuses_before_the_writer_starts(
    tmp_path: Path, output: list[str]
) -> None:
    from cuanta.bootstrap import Container

    runner = FakeRunner(streams={"npm test": FakeStream(output, 1)})
    container = Container(tmp_path, Config(), runner=runner, home=tmp_path / "home")
    try:
        spec = LaunchSpec("mandate", "fix", str(tmp_path), (), verify_commands=("npm test",))
        with pytest.raises(DomainFailure, match=r"npm test \(fails without diagnostics\)"):
            container.implementation_session(spec, MemoryLedger())
        assert not any("-p" in command for command in runner.calls)
    finally:
        container.close()
    diagnosed = VerifyResult("npm test", 1, errors=(YARN_FAILED, "● math › adds"))
    assert unavailable_checks((diagnosed,), ("npm test",)) == ()


def test_a_refused_silent_baseline_is_not_cached_so_the_next_call_checks_again(
    tmp_path: Path,
) -> None:
    from cuanta.bootstrap import Container

    silent = FakeStream(["jest: not found", YARN_FAILED], 1)
    runner = FakeRunner(queued={"npm test": [silent, FakeStream(["ok"], 0)]})
    container = Container(tmp_path, Config(), runner=runner, home=tmp_path / "home")
    try:
        spec = LaunchSpec("mandate", "fix", str(tmp_path), (), verify_commands=("npm test",))
        with pytest.raises(DomainFailure, match=r"npm test \(fails without diagnostics\)"):
            container.implementation_session(spec, MemoryLedger())
        session = container.implementation_session(spec, MemoryLedger())
        assert session is not None and session.baseline[0].passed
        assert container.implementation_session(spec, MemoryLedger()) is not None
        assert runner.calls.count(("npm", "test")) == 2
    finally:
        container.close()


def test_a_new_diagnostics_version_ignores_cached_baselines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cuanta.bootstrap import Container
    from cuanta.domain import implementation

    runner = FakeRunner()
    container = Container(tmp_path, Config(), runner=runner, home=tmp_path / "home")
    spec = LaunchSpec("mandate", "fix", str(tmp_path), (), verify_commands=("npm run check",))
    check = ("npm", "run", "check")
    try:
        for _ in range(2):
            assert container.implementation_session(spec, MemoryLedger()) is not None
        assert runner.calls.count(check) == 1
        monkeypatch.setattr(implementation, "DIAGNOSTICS_VERSION", "older parser")
        assert container.implementation_session(spec, MemoryLedger()) is not None
        assert runner.calls.count(check) == 2
    finally:
        container.close()


def test_an_oversized_first_error_is_clipped_and_later_errors_still_reach_the_writer() -> None:
    huge = "src/a.ts:1:1 error TS2322: " + "x" * 12_000
    rest = tuple(f"src/b.ts:{line}:1 error TS2304: missing {line}" for line in range(5))
    feedback = repair_feedback((huge, *rest))
    rows = feedback.splitlines()
    assert rows[0].startswith("src/a.ts:1:1 error TS2322: xxx") and rows[0].endswith("...")
    assert len(rows[0].encode()) <= 1_000
    assert rows[1:] == list(rest) and "further errors" not in feedback
    wide = repair_feedback(("é" * 2_000,))
    assert len(wide.encode()) <= 1_000 and wide.endswith("...")


def test_fast_and_ordered_launches_refuse_without_same_session_input(tmp_path: Path) -> None:
    finished = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "done",
            "total_cost_usd": 0.1,
            "num_turns": 1,
            "session_id": "SESSION",
        }
    )
    runner = FakeRunner(
        streams={"claude": FakeStream([finished])},
        responses={"claude --help": Completed(0, "--print --model", "")},
    )
    ledger = MemoryLedger()
    run_ids = iter(("ORDERED", "FAST", "BALANCED"))
    launcher = EngineLauncher(
        ClaudeCodeEngine(runner),
        ledger,
        FixedClock(),
        lambda: next(run_ids),
        lambda size: b"x" * size,
        "project",
        4318,
        None,
        implementer=lambda _: pytest.fail("no session without stream input"),
    )
    base = LaunchSpec("mandate", "feature", str(tmp_path), (), model="sonnet")
    for spec in (replace(base, implementation_steps=True), replace(base, profile="fast")):
        with pytest.raises(DomainFailure, match="follow-up turns in one session"):
            launcher.launch(spec, lambda _: None)
    assert not [call for call in runner.calls if "-p" in call]
    assert {run.id: run.status for run in ledger.runs()} == {
        "ORDERED": "interrupted",
        "FAST": "interrupted",
    }
    balanced = launcher.launch(base, lambda _: None)
    assert balanced.outcome.ok and balanced.implementation is None
    assert len([call for call in runner.calls if "-p" in call]) == 1


@pytest.mark.parametrize(
    ("subtype", "terminal", "reason"),
    [
        ("error_max_budget_usd", "budget_exhausted", "cost_limit"),
        ("error_governor_stop", "", "cost_limit"),
        ("error_max_turns", "", "turn_limit"),
        ("error_during_execution", "max_turns", "turn_limit"),
        ("error_cost_unknown", "", "cost_unknown"),
        ("error_during_execution", "", "engine_failed"),
    ],
)
def test_engine_stops_map_to_catalog_reasons_and_keep_the_raw_subtype(
    subtype: str, terminal: str, reason: str
) -> None:
    session = ImplementationSession(COMMANDS, checks(), CheckSequence(), lambda: 0.0)
    session.on_result(
        replace(result(), ok=False, subtype=subtype, terminal_reason=terminal),
        lambda _: pytest.fail("no followup"),
    )
    payload = session.report.payload()
    assert payload["reason"] == reason and payload["engine_subtype"] == subtype
    assert session.report.steps[0].state == "failed"


def test_an_engine_stop_keeps_its_subtype_and_only_a_red_verification_is_rewritten(
    tmp_path: Path,
) -> None:
    stopped = json.dumps(
        {
            "type": "result",
            "subtype": "error_max_budget_usd",
            "is_error": True,
            "result": "partial",
            "total_cost_usd": 0.5,
            "num_turns": 3,
            "session_id": "SESSION",
            "terminal_reason": "budget_exhausted",
        }
    )
    runner = FakeRunner(
        streams={"claude": FakeStream([stopped])},
        responses={"claude --help": Completed(0, "--input-format", "")},
    )
    ledger = MemoryLedger()
    session = ImplementationSession(COMMANDS, checks(), CheckSequence(), lambda: 0.0)
    launcher = EngineLauncher(
        ClaudeCodeEngine(runner),
        ledger,
        FixedClock(),
        lambda: "R",
        lambda size: b"x" * size,
        "project",
        4318,
        None,
        implementer=lambda _: session,
    )
    launch = launcher.launch(
        LaunchSpec("mandate", "fix", str(tmp_path), (), model="sonnet"), lambda _: None
    )
    stop = launch.outcome.result
    assert stop is not None and not launch.outcome.ok
    assert (stop.subtype, stop.terminal_reason) == ("error_max_budget_usd", "budget_exhausted")
    assert stop.text == "partial\n\nCuanta verification: cost_limit"
    assert [run.end_reason for run in ledger.runs()] == ["error_max_budget_usd"]
    red = ImplementationSession(
        COMMANDS, checks(), CheckSequence(checks(NEW_ERROR)), lambda: 0.0, max_repairs=0
    )
    red.on_result(result(), lambda _: pytest.fail("no repair round"))
    settled = red.settle(EngineOutcome(0, result(), 1)).result
    assert settled is not None and not settled.ok
    assert (settled.subtype, settled.terminal_reason) == ("error_implementation", "repair_limit")
    assert NEW_ERROR in settled.text


def test_planning_guards_source_named_like_outputs_and_ignores_package_outputs() -> None:
    snapshot = {
        "package.json": "root",
        "apps/web/package.json": "web",
        "src/features/coverage/PlanCard.tsx": "v1",
    }
    before = implementation_fingerprint(snapshot)
    outputs = {
        ".turbo/turbo-lint.log": "log",
        "apps/web/.turbo/turbo-lint.log": "log",
        ".nyc_output/out.json": "raw",
        "apps/web/coverage/lcov.info": "lcov",
        "coverage/report.json": "report",
        "coverage.xml": "xml",
        "junit.xml": "xml",
        "apps/web/junit.xml": "xml",
        ".coverage": "data",
        "tsconfig.tsbuildinfo": "cache",
    }
    assert implementation_fingerprint({**snapshot, **outputs}) == before
    for path in ("src/features/coverage/PlanCard.tsx", "src/features/coverage/New.tsx"):
        assert implementation_fingerprint({**snapshot, path: "edited"}) != before
    assert implementation_fingerprint({**snapshot, "src/junit.xml": "xml"}) != before
    live = dict(snapshot)
    session = ImplementationSession(
        COMMANDS, checks(), CheckSequence(), lambda: 0.0, stepped=True, snapshot=lambda: live
    )
    live["src/features/coverage/PlanCard.tsx"] = "unauthorized"
    session.on_result(
        result(text='{"implementation_steps":["write feature"]}'),
        lambda _: pytest.fail("no step before planning is clean"),
    )
    assert session.report.reason == "planning_changes"


def test_titles_that_differ_only_by_a_duration_token_stay_distinct(tmp_path: Path) -> None:
    jest = [
        " FAIL  src/debounce.test.js",
        "  ● debounce › waits 100ms",
        "",
        "    expect(received).toBe(expected)",
    ]
    swapped = [line.replace("waits 100ms", "waits 300ms") for line in jest]
    assert compared(tmp_path, "npm test", jest, swapped).introduced == (
        "npm test: src/debounce.test.js ● debounce › waits 300ms",
    )
    vitest = [" FAIL  src/a.test.ts > caches for 5s", "Expected 1 to be 2", " ❯ src/a.test.ts:4:21"]
    moved = [line.replace("5s", "60s").replace(":4:21", ":9:21") for line in vitest]
    assert compared(tmp_path, "npx vitest run", vitest, moved).introduced == (
        "npx vitest run: FAIL src/a.test.ts > caches for 60s",
    )


@pytest.mark.parametrize(
    ("first", "reprinted"),
    [
        (" FAIL  src/app.controller.spec.ts", " FAIL  ./app.controller.spec.ts"),
        (" FAIL  client src/App.test.js", " FAIL  src/App.test.js"),
    ],
)
def test_a_reprint_under_other_headers_never_repeats_known_titles(
    tmp_path: Path, first: str, reprinted: str
) -> None:
    failure = ["  ● app › renders", "", "    expect(received).toBe(expected)"]
    before = [first, *failure]
    after = [first, *failure, "", "Summary of all failing tests", reprinted, *failure]
    delta = compared(tmp_path, "npm test", before, after)
    assert delta.passed and not delta.introduced and len(delta.preexisting) == 1


def test_passing_test_file_headers_with_error_words_are_never_errors(tmp_path: Path) -> None:
    added = [
        *JEST_ADDS,
        "",
        " PASS  app/error.test.tsx",
        " PASS  app/global-error.test.tsx (5.2 s)",
        " PASS  src/http-exception.filter.spec.ts",
    ]
    delta = compared(tmp_path, "npm test", JEST_ADDS, added)
    assert delta.passed and not delta.introduced
    worse = compared(
        tmp_path, "npm test", JEST_ADDS, [*added, " FAIL  app/error.test.tsx", "  ● boom"]
    )
    assert "npm test: app/error.test.tsx ● boom" in worse.introduced
