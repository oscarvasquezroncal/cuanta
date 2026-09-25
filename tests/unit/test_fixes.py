from __future__ import annotations

import pytest

from cuanta.domain.fixes import FixAction, FixKind, classify


@pytest.mark.parametrize(
    ("command", "kind", "action", "argument"),
    [
        ("cuanta init", FixKind.OPEN, FixAction.INIT, ""),
        ("cuanta refresh", FixKind.OPEN, FixAction.INIT, ""),
        ("cuanta test", FixKind.RUN, FixAction.TESTS, ""),
        ("cuanta telemetry on --engine codex", FixKind.RUN, FixAction.TELEMETRY_ON, "codex"),
        ("cuanta telemetry on", FixKind.RUN, FixAction.TELEMETRY_ON, "all"),
        ("cuanta listen --background", FixKind.RUN, FixAction.LISTENER_START, ""),
        ("cuanta instinct use heuristic", FixKind.COPY, FixAction.NONE, ""),
        ("npm install -g @openai/codex", FixKind.COPY, FixAction.NONE, ""),
        ('delete "C:/x y/forge-state.json"', FixKind.COPY, FixAction.NONE, ""),
        ("cuanta 'unbalanced", FixKind.COPY, FixAction.NONE, ""),
    ],
)
def test_classify(command: str, kind: FixKind, action: FixAction, argument: str) -> None:
    fix = classify(command)
    assert (fix.kind, fix.action, fix.argument, fix.command) == (kind, action, argument, command)
