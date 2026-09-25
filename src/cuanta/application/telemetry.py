from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from cuanta.application.init_project import InitContext, StageResult, stage
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.messages import msg
from cuanta.domain.progress import Status
from cuanta.domain.shells import Shell, snippet
from cuanta.domain.telemetry import WiringPlan, WiringReport, WiringState, claude_env
from cuanta.ports.ledger import EventQuery, Ledger
from cuanta.ports.listener import ListenerControl, ListenerStatus
from cuanta.ports.telemetry import EngineWiring

ImportResult = tuple[list[LedgerEvent], object]


@dataclass(frozen=True, slots=True)
class TelemetryStatus:
    listener: ListenerStatus
    port: int
    engines: tuple[WiringReport, ...]


class TelemetryService:
    def __init__(
        self,
        project_name: str,
        port: int,
        wirings: Sequence[EngineWiring],
        listener: ListenerControl,
        persist_port: Callable[[int], None],
    ) -> None:
        self._project = project_name
        self._port = port
        self._wirings = wirings
        self._listener = listener
        self._persist_port = persist_port

    def resolve_port(self) -> int:
        status = self._listener.status()
        if status.running:
            return status.port
        chosen = self._listener.free_port(self._port)
        if chosen != self._port:
            self._persist_port(chosen)
            self._port = chosen
        return chosen

    def _select(self, engine: str) -> Sequence[EngineWiring]:
        if engine in {"", "all"}:
            return self._wirings
        return [wiring for wiring in self._wirings if wiring.engine == engine]

    def enable(self, engine: str = "all") -> tuple[WiringReport, ...]:
        port = self.resolve_port()
        return tuple(wiring.enable(port, self._project) for wiring in self._select(engine))

    def plan(self, engine: str = "all") -> tuple[WiringPlan, ...]:
        return tuple(wiring.plan() for wiring in self._select(engine))

    def disable(self, engine: str = "all") -> tuple[WiringReport, ...]:
        return tuple(wiring.disable() for wiring in self._select(engine))

    def status(self) -> TelemetryStatus:
        listener = self._listener.status()
        port = listener.port if listener.running else self._port
        return TelemetryStatus(
            listener=listener,
            port=port,
            engines=tuple(wiring.status(port) for wiring in self._wirings),
        )

    def env_snippet(self, shell: Shell) -> str:
        return snippet(claude_env(self._port, self._project), shell)


class TelemetryStage:
    def __init__(self, service: TelemetryService, consent: bool) -> None:
        self._service = service
        self._consent = consent

    def __call__(self, context: InitContext) -> StageResult:
        if not self._consent:
            context.telemetry_line = "not wired (no consent; run cuanta telemetry on)"
            return stage(Status.SKIP, "stage.no_consent")
        if context.dry_run:
            context.planned.append(msg("plan.telemetry"))
            context.telemetry_line = "would wire claude"
            return stage(Status.INFO, "stage.planned")
        reports = self._service.enable("claude")
        port = self._service.resolve_port()
        wired = [report for report in reports if report.state is WiringState.ON]
        context.telemetry_line = (
            f"127.0.0.1:{port} · claude {'on' if wired else 'off'} · launched runs on"
        )
        return stage(Status.OK, "stage.port", port=port)


class TranscriptImport:
    def __init__(
        self,
        ledger: Ledger,
        importers: Sequence[
            tuple[str, Callable[[dict[str, str]], tuple[list[LedgerEvent], object]]]
        ],
    ) -> None:
        self._ledger = ledger
        self._importers = importers
        self.added: dict[str, int] = {}
        self.removed: dict[str, int] = {}

    def _watermarks(self, source: str) -> dict[str, str]:
        marks: dict[str, str] = {}
        for event in self._ledger.events(EventQuery()):
            if event.source == source and event.session_id:
                marks[event.session_id] = max(marks.get(event.session_id, ""), event.ts)
        return marks

    def run(self, rebuild: bool = False) -> dict[str, object]:
        results: dict[str, object] = {}
        for source, importer in self._importers:
            if rebuild:
                self.removed[source] = self._ledger.delete_events(source)
            events, counts = importer({} if rebuild else self._watermarks(source))
            self.added[source] = self._ledger.add_events(events)
            results[source] = counts
        return results
