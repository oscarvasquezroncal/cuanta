from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from cuanta.application.detect import DetectProject, read_forge_state
from cuanta.domain.detection import Detection, GraphMode, SizeTier, VerifyTier
from cuanta.domain.forge_verify import check_ceilings, placeholders
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.messages import Message, english, msg
from cuanta.domain.overhead import session_overhead, startup_message
from cuanta.domain.plugins import InstalledPlugin, older_forge
from cuanta.domain.progress import Status
from cuanta.domain.telemetry import WiringReport, WiringState
from cuanta.domain.terminal import TerminalReport
from cuanta.ports.engine import Engine
from cuanta.ports.forge import ForgeKit
from cuanta.ports.ledger import EventQuery, Ledger
from cuanta.ports.listener import ListenerControl
from cuanta.ports.workspace import Workspace


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    status: Status
    detail: str
    fix: str = ""
    message: Message | None = None


def result(name: str, status: Status, message: Message, fix: str = "") -> CheckResult:
    return CheckResult(name, status, english(message), fix, message)


Check = Callable[[Detection], Sequence[CheckResult]]


@dataclass(frozen=True, slots=True)
class DoctorReport:
    detection: Detection
    checks: tuple[CheckResult, ...]

    @property
    def healthy(self) -> bool:
        return all(check.status is not Status.FAIL for check in self.checks)

    def count(self, status: Status) -> int:
        return sum(1 for check in self.checks if check.status is status)


def python_status(version: tuple[int, ...]) -> CheckResult:
    text = ".".join(str(part) for part in version)
    if version[:2] >= (3, 12):
        return result("python", Status.OK, msg("doctor.python.ok", version=text))
    return result("python", Status.FAIL, msg("doctor.python.old", version=text))


def python_check(_: Detection) -> Sequence[CheckResult]:
    return [python_status(tuple(sys.version_info[:3]))]


ENGINE_FIXES = {
    "claude": "npm install -g @anthropic-ai/claude-code",
    "codex": "npm install -g @openai/codex",
    "opencode": "npm install -g opencode-ai",
}


def engines_check(detection: Detection) -> Sequence[CheckResult]:
    results: list[CheckResult] = []
    for name, fix in ENGINE_FIXES.items():
        engine = detection.engine(name)
        if engine is None:
            results.append(result(f"engine {name}", Status.WARN, msg("doctor.engine.missing"), fix))
        else:
            version = engine.version or engine.path
            results.append(
                result(f"engine {name}", Status.OK, msg("doctor.engine.found", version=version))
            )
    return results


def verify_check(detection: Detection) -> Sequence[CheckResult]:
    tier = detection.verify_tier
    evidence = detection.verify.evidence
    if tier is VerifyTier.STRONG:
        message = msg("doctor.whiskers.strong", tier=tier.value, evidence=evidence)
        return [result("whiskers", Status.OK, message)]
    message = msg("doctor.whiskers.weak", tier=tier.value, evidence=evidence)
    return [result("whiskers", Status.WARN, message)]


def graph_check(detection: Detection) -> Sequence[CheckResult]:
    mode = detection.graph_mode.value
    if detection.graph_mode is GraphMode.BROKEN:
        separator = "; " if sys.platform == "win32" else " && "
        fix = separator.join(("uv self update", "uv tool upgrade --all"))
        reinstall = separator.join(("uv tool uninstall graphifyy", "uv tool install graphifyy"))
        fix = f"{fix}; or reinstall: {reinstall}"
        return [
            result(
                "graph",
                Status.FAIL,
                msg("doctor.graph.broken", evidence=detection.graph_evidence),
                fix,
            )
        ]
    if detection.size_tier is SizeTier.SMALL:
        return [result("graph", Status.OK, msg("doctor.graph.small", mode=mode))]
    if mode == "none":
        return [result("graph", Status.WARN, msg("doctor.graph.none"), "cuanta init")]
    evidence = detection.graph_evidence
    return [result("graph", Status.OK, msg("doctor.graph.ok", mode=mode, evidence=evidence))]


def forge_state_check(workspace: Workspace) -> Check:
    def check(detection: Detection) -> Sequence[CheckResult]:
        state = read_forge_state(workspace)
        if state.problem is not None:
            message = msg("doctor.forge_state.corrupt", problem=state.problem)
            return [result("forge-state", Status.FAIL, message, "cuanta init")]
        if state.state is None:
            return [result("forge-state", Status.WARN, msg("doctor.absent"), "cuanta init")]
        recorded = state.state.verify_tier
        current = detection.verify_tier.value
        if recorded and recorded != current:
            message = msg("doctor.forge_state.drift", recorded=recorded, current=current)
            return [result("forge-state", Status.WARN, message, "cuanta refresh")]
        phase = state.state.next_phase
        if phase is None:
            return [result("forge-state", Status.OK, msg("doctor.forge_state.complete"))]
        return [result("forge-state", Status.OK, msg("doctor.forge_state.next", phase=phase))]

    return check


def cuanta_dir_check(workspace: Workspace) -> Check:
    def check(_: Detection) -> Sequence[CheckResult]:
        if not workspace.is_dir(".cuanta"):
            return [result(".cuanta", Status.WARN, msg("doctor.absent"), "cuanta init")]
        size = f"{workspace.size_bytes('.cuanta') / 1_048_576:.1f}"
        return [result(".cuanta", Status.OK, msg("doctor.cuanta_dir.size", megabytes=size))]

    return check


class Doctor:
    def __init__(self, detector: DetectProject, checks: Sequence[Check]) -> None:
        self._detector = detector
        self._checks = checks

    def run(self) -> DoctorReport:
        detection = self._detector.run()
        results: list[CheckResult] = []
        for check in self._checks:
            results.extend(check(detection))
        return DoctorReport(detection, tuple(results))


def engine_flags_check(engines: Callable[[str], Engine | None]) -> Check:
    def check(detection: Detection) -> Sequence[CheckResult]:
        results: list[CheckResult] = []
        for info in detection.engines:
            engine = engines(info.name)
            if engine is None:
                continue
            missing = engine.missing_flags()
            name = f"flags {info.name}"
            if missing:
                message = msg("doctor.flags.missing", flags=", ".join(missing), engine=info.name)
                results.append(result(name, Status.FAIL, message))
            else:
                results.append(result(name, Status.OK, msg("doctor.flags.ok")))
        return results

    return check


def forge_version_check(kit: ForgeKit) -> Check:
    def check(_: Detection) -> Sequence[CheckResult]:
        installed = kit.installed_skill()
        vendored = kit.vendored_version()
        if installed is None:
            message = msg("doctor.forge.missing", version=vendored)
            return [result("forge", Status.WARN, message, "cuanta init")]
        if installed.replace("\r\n", "\n") == kit.vendored_skill().replace("\r\n", "\n"):
            return [result("forge", Status.OK, msg("doctor.forge.same", version=vendored))]
        message = msg("doctor.forge.differs", version=vendored)
        return [result("forge", Status.WARN, message, "cuanta refresh")]

    return check


def rulebook_check(workspace: Workspace) -> Check:
    def check(_: Detection) -> Sequence[CheckResult]:
        texts = {
            f".claude/agents/{name}": workspace.read_text(f".claude/agents/{name}") or ""
            for name in workspace.list_names(".claude/agents")
            if name.endswith(".md") and not name.endswith(".new.md")
        }
        hits = placeholders(texts)
        if hits:
            message = msg("doctor.placeholders.found", hits=", ".join(hits[:3]))
            results = [result("placeholders", Status.FAIL, message, "cuanta refresh")]
        else:
            message = msg("doctor.placeholders.clean", count=len(texts))
            results = [result("placeholders", Status.OK, message)]
        for finding in check_ceilings(workspace.read_text("CLAUDE.md"), {}):
            missing = finding.missing
            status = Status.WARN if finding.status is Status.FAIL and missing else finding.status
            fix = (
                ("cuanta init" if missing else "cuanta refresh") if status is not Status.OK else ""
            )
            results.append(result("ceilings", status, finding.message, fix))
        return results

    return check


def listener_check(listener: ListenerControl) -> Check:
    def check(_: Detection) -> Sequence[CheckResult]:
        status = listener.status()
        if status.running:
            message = msg("doctor.listener.on", port=status.port, written=f"{status.written:,}")
            return [result("listener", Status.OK, message)]
        message = msg("doctor.listener.off")
        return [result("listener", Status.WARN, message, "cuanta listen --background")]

    return check


def telemetry_check(status: Callable[[], Sequence[WiringReport]]) -> Check:
    def check(_: Detection) -> Sequence[CheckResult]:
        results: list[CheckResult] = []
        for report in status():
            name = f"telemetry {report.engine}"
            if report.state is WiringState.ON:
                results.append(result(name, Status.OK, report.message))
            elif report.state is WiringState.UNAVAILABLE:
                results.append(result(name, Status.SKIP, report.message))
            else:
                fix = f"cuanta telemetry on --engine {report.engine}"
                results.append(result(name, Status.WARN, report.message, fix))
        return results

    return check


def terminal_check(report: Callable[[], TerminalReport]) -> Check:
    def check(_: Detection) -> Sequence[CheckResult]:
        found = report()
        message = msg(f"doctor.terminal.{found.kind.value}", evidence=found.evidence)
        return [result("terminal", Status.WARN if found.legacy else Status.OK, message)]

    return check


def ledger_check(
    ledger_factory: Callable[[], Ledger], expected: int, exists: Callable[[], bool]
) -> Check:
    def check(_: Detection) -> Sequence[CheckResult]:
        if not exists():
            return [result("ledger", Status.INFO, msg("doctor.ledger.none"), "cuanta test")]
        ledger = ledger_factory()
        try:
            version = ledger.schema_version()
        finally:
            ledger.close()
        if version == expected:
            return [result("ledger", Status.OK, msg("doctor.ledger.ok", version=version))]
        message = msg("doctor.ledger.mismatch", version=version, expected=expected)
        return [result("ledger", Status.FAIL, message)]

    return check


def instinct_check(
    configured: str,
    availability: Callable[[], tuple[bool, Message]],
    setup_warning: Callable[[], Message | None],
    ledger_factory: Callable[[], Ledger],
    exists: Callable[[], bool],
) -> Check:
    def check(_: Detection) -> Sequence[CheckResult]:
        if configured != "jev":
            return []
        found: list[CheckResult] = []
        usable, detail = availability()
        if not usable:
            found.append(result("instinct jev", Status.WARN, detail))
        warning = setup_warning()
        if warning is not None:
            found.append(result("instinct jev key", Status.WARN, warning))
        if exists():
            ledger = ledger_factory()
            try:
                recent = ledger.decisions(limit=20)
            finally:
                ledger.close()
            error = next(
                (
                    item.fallback_error
                    for item in recent
                    if item.backend == "heuristic"
                    and item.fallback_from == "jev"
                    and item.fallback_error
                ),
                "",
            )
            if error:
                found.append(
                    result(
                        "instinct jev fallback",
                        Status.WARN,
                        msg("doctor.instinct.recent_fallback", error=error),
                    )
                )
        return found

    return check


STARTUP_WARN_MS = 5_000
RECENT_CLAUDE_RUNS = 20


def user_forge_check(
    plugins: Callable[[], Sequence[InstalledPlugin]], vendored: Callable[[], str]
) -> Check:
    def check(_: Detection) -> Sequence[CheckResult]:
        version = vendored()
        return [
            result(
                "user forge",
                Status.WARN,
                msg("doctor.user_forge.old", version=plugin.version, vendored=version),
                f"claude plugin disable {plugin.key}",
            )
            for plugin in older_forge(plugins(), version)
        ]

    return check


def session_check(ledger_factory: Callable[[], Ledger], exists: Callable[[], bool]) -> Check:
    def check(_: Detection) -> Sequence[CheckResult]:
        if not exists():
            return []
        ledger = ledger_factory()
        try:
            claude_runs = [run for run in ledger.runs(limit=RECENT_CLAUDE_RUNS) if run.engine]
            events: tuple[LedgerEvent, ...] = ()
            for run in claude_runs:
                events = ledger.events(EventQuery(run_id=run.id))
                if events:
                    break
        finally:
            ledger.close()
        overhead = session_overhead(events, 0)
        found: list[CheckResult] = []
        for server in overhead.failed_servers:
            message = msg(
                "doctor.mcp.failed",
                name=server.name,
                seconds=f"{server.duration_ms / 1000:.1f}",
                error=server.error or "no reason given",
            )
            found.append(result(f"mcp {server.name}", Status.WARN, message))
        startup = overhead.startup
        if startup is not None:
            slow = startup.before_request_ms > STARTUP_WARN_MS
            message = msg(
                "doctor.startup",
                hooks=len(overhead.hooks),
                servers=len(overhead.servers),
                plugins=len(overhead.plugins),
                detail=startup_message(startup),
            )
            found.append(result("session startup", Status.WARN if slow else Status.OK, message))
        return found

    return check
