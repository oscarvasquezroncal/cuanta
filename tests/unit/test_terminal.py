from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from cuanta.domain.terminal import TerminalKind, TerminalProbe, classify

WIN = "win32"


@pytest.mark.parametrize(
    ("probe", "environ", "kind", "evidence"),
    [
        (TerminalProbe(WIN, "PseudoConsoleWindow"), {}, TerminalKind.MODERN, "ConPTY"),
        (
            TerminalProbe(WIN, "ConsoleWindowClass", True),
            {"WT_SESSION": "leaked"},
            TerminalKind.LEGACY,
            "conhost",
        ),
        (TerminalProbe(WIN, "", False), {}, TerminalKind.LEGACY, "unavailable"),
        (
            TerminalProbe(WIN, "", None, ("cmd.exe", "WindowsTerminal.exe")),
            {},
            TerminalKind.MODERN,
            "WindowsTerminal.exe",
        ),
        (TerminalProbe(WIN), {"WT_SESSION": "x"}, TerminalKind.MODERN, "WT_SESSION set"),
        (TerminalProbe(WIN), {}, TerminalKind.MODERN, "no evidence"),
        (TerminalProbe("linux"), {}, TerminalKind.MODERN, "platform linux"),
        (
            TerminalProbe(WIN, "PseudoConsoleWindow"),
            {"CUANTA_TERMINAL": "legacy"},
            TerminalKind.LEGACY,
            "CUANTA_TERMINAL=legacy",
        ),
        (
            TerminalProbe(WIN, "ConsoleWindowClass"),
            {"CUANTA_TERMINAL": "Modern"},
            TerminalKind.MODERN,
            "CUANTA_TERMINAL=modern",
        ),
    ],
)
def test_classification_matrix(
    probe: TerminalProbe, environ: dict[str, str], kind: TerminalKind, evidence: str
) -> None:
    report = classify(probe, environ)
    assert report.kind is kind
    assert evidence in report.detail


windows_only = pytest.mark.skipif(sys.platform != "win32", reason="needs real Windows consoles")


@windows_only
def test_real_legacy_conhost_is_detected(tmp_path: Path) -> None:
    from tests.windows.consoles import run_in_conhost

    report = json.loads(run_in_conhost(tmp_path / "legacy.json"))
    assert report == {"class": "ConsoleWindowClass", "kind": "legacy"}


@windows_only
def test_real_conpty_host_is_detected(tmp_path: Path) -> None:
    from tests.windows.consoles import run_in_conpty

    report = json.loads(run_in_conpty(tmp_path / "conpty.json"))
    assert report == {"class": "PseudoConsoleWindow", "kind": "modern"}
