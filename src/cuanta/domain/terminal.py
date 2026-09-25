from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from cuanta.domain.messages import Message, english, msg

OVERRIDE_ENV = "CUANTA_TERMINAL"
MODERN_CLASS = "PseudoConsoleWindow"
LEGACY_CLASS = "ConsoleWindowClass"
MODERN_HOSTS = frozenset({"windowsterminal.exe", "openconsole.exe"})
WEAK_HINTS = ("WT_SESSION", "TERM_PROGRAM")


class TerminalKind(StrEnum):
    MODERN = "modern"
    LEGACY = "legacy"


@dataclass(frozen=True, slots=True)
class TerminalProbe:
    platform: str
    window_class: str = ""
    vt_enabled: bool | None = None
    ancestors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TerminalReport:
    kind: TerminalKind
    evidence: Message

    @property
    def detail(self) -> str:
        return english(self.evidence)

    @property
    def legacy(self) -> bool:
        return self.kind is TerminalKind.LEGACY


def _modern_host(ancestors: Sequence[str]) -> str:
    return next((name for name in ancestors if name.lower() in MODERN_HOSTS), "")


def classify(probe: TerminalProbe, environ: Mapping[str, str]) -> TerminalReport:
    override = environ.get(OVERRIDE_ENV, "").strip().lower()
    if override in {kind.value for kind in TerminalKind}:
        return TerminalReport(
            TerminalKind(override), msg("terminal.override", setting=f"{OVERRIDE_ENV}={override}")
        )
    if probe.platform != "win32":
        return TerminalReport(
            TerminalKind.MODERN, msg("terminal.platform", platform=probe.platform)
        )
    if probe.window_class == MODERN_CLASS:
        return TerminalReport(TerminalKind.MODERN, msg("terminal.conpty", window=MODERN_CLASS))
    if probe.window_class == LEGACY_CLASS:
        return TerminalReport(TerminalKind.LEGACY, msg("terminal.conhost", window=LEGACY_CLASS))
    if probe.vt_enabled is False:
        return TerminalReport(TerminalKind.LEGACY, msg("terminal.vt_off"))
    host = _modern_host(probe.ancestors)
    if host:
        return TerminalReport(TerminalKind.MODERN, msg("terminal.host", host=host))
    hints = ", ".join(name for name in WEAK_HINTS if environ.get(name))
    if hints:
        return TerminalReport(TerminalKind.MODERN, msg("terminal.hints", names=hints))
    if probe.vt_enabled:
        return TerminalReport(TerminalKind.MODERN, msg("terminal.vt_on"))
    return TerminalReport(TerminalKind.MODERN, msg("terminal.no_evidence"))
