from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest

from cuanta.adapters.forge.installer import VendoredForgeKit
from cuanta.adapters.storage.capsule_store import FileCapsuleStore
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.adapters.system.workspace import LocalHome, LocalWorkspace
from cuanta.application.cross_engine import CrossEnginePipeline
from cuanta.application.detect import DetectProject
from cuanta.application.engine_run import EngineLauncher, LaunchSpec
from cuanta.application.forge import ForgeStage, VerifyStage
from cuanta.application.init_project import GraphStage, InitContext
from cuanta.application.mandate import report_payload
from cuanta.application.mandate_flow import MandateOptions
from cuanta.application.progress import RecordingSink
from cuanta.application.refresh import RefreshProject
from cuanta.domain.agents import parse_agent
from cuanta.domain.engine import EngineEvent, EngineOutcome, EngineRequest
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.progress import Note, ProgressEvent, Status
from cuanta.domain.telemetry import (
    LISTENER_LOG,
    UNREADABLE_KIND,
    unreadable_event,
    unreadable_note,
)
from cuanta.ports.listener import ListenerStatus
from cuanta.tui.i18n import Catalog
from tests.fakes import FakeGraph, FakeRunner, copy_repo
from tests.unit.test_cross_engine import REQUEST as BUG
from tests.unit.test_cross_engine import Recorder, ScriptedEngine, plan
from tests.unit.test_results_flow import REQUEST, ReportingEngine, build

ONE = "1 telemetry record could not be read; details in .cuanta/logs/listener.log"
THREE = "3 telemetry records could not be read; details in .cuanta/logs/listener.log"


def _markers(run_id: str, count: int) -> list[LedgerEvent]:
    return [LedgerEvent(run_id=run_id, source="cuanta", kind=UNREADABLE_KIND)] * count


def _unreadable(events: Sequence[ProgressEvent]) -> list[Note]:
    return [
        event
        for event in events
        if isinstance(event, Note)
        and event.message is not None
        and event.message.key.startswith("telemetry.unreadable")
    ]


class DroppingEngine(ReportingEngine):
    def __init__(self, ledger: MemoryLedger, dropped: int) -> None:
        super().__init__("## SUMMARY\nok\n")
        self.ledger = ledger
        self.dropped = dropped

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        self.ledger.add_events(_markers(request.env["CUANTA_RUN_ID"], self.dropped))
        return super().run(request, on_event)


@pytest.mark.parametrize(("dropped", "expected"), [(0, []), (1, [ONE]), (3, [THREE])])
def test_unreadable_telemetry_adds_one_note_to_the_run_result(
    tmp_path: Path, dropped: int, expected: list[str]
) -> None:
    ledger = MemoryLedger()
    flow = build(tmp_path, False, ledger, DroppingEngine(ledger, dropped))
    sink = RecordingSink()
    report = flow.run(flow.prepare(REQUEST, 0, MandateOptions(simple=True)), sink, verdict=False)
    notes = _unreadable(sink.events)
    assert [note.text for note in notes] == expected
    assert all(note.status is Status.WARN for note in notes)
    assert report.unreadable_telemetry == dropped
    assert report_payload(report).get("telemetry_unreadable") == (dropped or None)


class DroppingScripted(ScriptedEngine):
    def __init__(self, name: str, ledger: MemoryLedger, dropped: dict[str, int]) -> None:
        super().__init__(name, [])
        self.ledger = ledger
        self.dropped = dropped

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        count = self.dropped.get(request.model, 0)
        self.ledger.add_events(_markers(request.env["CUANTA_RUN_ID"], count))
        return super().run(request, on_event)


def _team(tmp_path: Path, dropped: dict[str, int]) -> CrossEnginePipeline:
    ledger = MemoryLedger()
    counter = iter(range(100))

    def launcher(name: str) -> EngineLauncher:
        return EngineLauncher(
            DroppingScripted(name, ledger, dropped),
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
    return CrossEnginePipeline(
        launcher, lambda: (senior,), FileCapsuleStore(tmp_path), str(tmp_path), 5.0
    )


@pytest.mark.parametrize(
    ("dropped", "expected"),
    [({}, []), ({"model-analyst": 1, "model-senior": 2}, [THREE])],
)
def test_the_team_pipeline_adds_one_note_for_all_its_roles(
    tmp_path: Path, dropped: dict[str, int], expected: list[str]
) -> None:
    recorder = Recorder()
    report = _team(tmp_path, dropped).run(BUG, plan(), recorder)
    assert report.ok
    assert len(report.steps) == 3
    assert [note.text for note in _unreadable(recorder.events)] == expected
    if expected:
        assert recorder.events[-1] == _unreadable(recorder.events)[0]


class DrainingListener:
    def __init__(self, ledger: MemoryLedger, runs: list[str]) -> None:
        self.ledger = ledger
        self.runs = runs

    def status(self) -> ListenerStatus:
        return ListenerStatus(False)

    def free_port(self, preferred: int) -> int:
        return preferred

    def start_background(self, port: int) -> ListenerStatus:
        return ListenerStatus(False, port)

    def stop(self) -> bool:
        return False

    def serve(self, port: int, on_ready: Callable[[ListenerStatus], None]) -> None:
        return None

    @contextmanager
    def scoped(self, port: int) -> Iterator[ListenerStatus]:
        yield ListenerStatus(True, port, 1, owned=True)
        for run_id in self.runs:
            self.ledger.add_events(_markers(run_id, 2))


class RecordingEngine(ReportingEngine):
    def __init__(self, runs: list[str]) -> None:
        super().__init__("done")
        self.runs = runs

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        self.runs.append(request.env["CUANTA_RUN_ID"])
        return super().run(request, on_event)


def test_the_launch_counts_markers_after_the_listener_drained() -> None:
    ledger = MemoryLedger()
    runs: list[str] = []
    launcher = EngineLauncher(
        RecordingEngine(runs),
        ledger,
        FixedClock(),
        lambda: "01DRAINED",
        lambda size: b"\x01" * size,
        "shop",
        4318,
        DrainingListener(ledger, runs),
    )
    ledger.add_events(_markers("01OTHER", 4))
    spec = LaunchSpec(kind="mandate", prompt="p", cwd=".", allowed_tools=())
    launch = launcher.launch(spec, lambda _: None)
    assert runs == ["01DRAINED"]
    assert launch.unreadable == 2


def _forge_launcher(ledger: MemoryLedger, dropped: int) -> EngineLauncher:
    return EngineLauncher(
        DroppingEngine(ledger, dropped),
        ledger,
        FixedClock(),
        lambda: "01FORGE",
        lambda size: b"\x01" * size,
        "shop",
        4318,
        None,
    )


def _detector(root: Path) -> DetectProject:
    return DetectProject(LocalWorkspace(root), LocalHome(root / "__home__"), FakeRunner())


@pytest.mark.parametrize(("dropped", "expected"), [(0, []), (1, [ONE]), (3, [THREE])])
def test_the_init_forge_run_notes_unreadable_telemetry_once(
    tmp_path: Path, dropped: int, expected: list[str]
) -> None:
    root = copy_repo("python_strong", tmp_path)
    ledger = MemoryLedger()
    sink = RecordingSink()
    forge = ForgeStage(
        VendoredForgeKit(root),
        lambda: _forge_launcher(ledger, dropped),
        sink,
        str(root),
        LocalWorkspace(root),
    )
    context = InitContext(detection=_detector(root).run(with_engines=False), dry_run=False)
    result = forge(context)
    assert result.status is Status.OK
    notes = _unreadable(sink.events)
    assert [note.text for note in notes] == expected
    assert all(note.status is Status.WARN for note in notes)


@pytest.mark.parametrize(("dropped", "expected"), [(0, []), (3, [THREE])])
def test_the_refresh_forge_run_notes_unreadable_telemetry_once(
    tmp_path: Path, dropped: int, expected: list[str]
) -> None:
    root = copy_repo("python_strong", tmp_path)
    workspace = LocalWorkspace(root)
    ledger = MemoryLedger()
    sink = RecordingSink()
    refresh = RefreshProject(
        workspace=workspace,
        detector=_detector(root),
        graph_stage=GraphStage(workspace, FakeGraph()),
        kit=VendoredForgeKit(root),
        launcher_factory=lambda: _forge_launcher(ledger, dropped),
        verify_stage=VerifyStage(workspace, MemoryLedger, FixedClock()),
        progress=sink,
    )
    report = refresh.run()
    assert dict(report.stages)["forge"].status is Status.OK
    assert [note.text for note in _unreadable(sink.events)] == expected


def test_markers_hold_only_the_event_name_and_the_error_class() -> None:
    event = unreadable_event(
        "01R", "trace", "2026-10-02T10:00:00.000Z", "tool_result", "ValueError"
    )
    assert (event.run_id, event.source, event.kind, event.trace_id, event.ts) == (
        "01R",
        "cuanta",
        UNREADABLE_KIND,
        "trace",
        "2026-10-02T10:00:00.000Z",
    )
    assert json.loads(event.raw) == {"event": "tool_result", "error": "ValueError"}


def test_the_note_names_the_log_in_both_languages() -> None:
    assert LISTENER_LOG == ".cuanta/logs/listener.log"
    assert unreadable_note(1).key == "telemetry.unreadable_one"
    assert dict(unreadable_note(1234).params)["count"] == "1,234"
    spanish = Catalog("es")
    assert spanish.message(unreadable_note(1)) == (
        "No se pudo leer 1 registro de telemetría; detalles en .cuanta/logs/listener.log"
    )
    assert spanish.message(unreadable_note(3)) == (
        "No se pudieron leer 3 registros de telemetría; detalles en .cuanta/logs/listener.log"
    )
