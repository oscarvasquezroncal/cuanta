from __future__ import annotations

from pathlib import Path

import pytest

from cuanta.adapters.system.clock import FixedClock
from cuanta.bootstrap import Container, load_config
from cuanta.ports.system import Completed
from tests.fakes import FakeRunner

CLAUDE_HELP = (
    "-p, --print --output-format stream-json --verbose --permission-mode dontAsk "
    "--allowedTools --disallowedTools --tools --model --max-budget-usd --agents"
    " --strict-mcp-config --mcp-config --settings --effort --append-system-prompt"
    " --exclude-dynamic-system-prompt-sections --no-session-persistence"
)


@pytest.fixture
def fake_runner(monkeypatch: pytest.MonkeyPatch, isolated_user_dirs: Path) -> FakeRunner:
    runner = FakeRunner(
        binaries={"claude": "/bin/claude"},
        responses={
            "claude --version": Completed(0, "2.1.280 (Claude Code)\n", ""),
            "claude --help": Completed(0, CLAUDE_HELP, ""),
        },
    )

    def build(cls: type[Container], project: Path) -> Container:
        return cls(
            project=project,
            config=load_config(project),
            runner=runner,
            clock=FixedClock(),
            home=isolated_user_dirs,
        )

    monkeypatch.setattr(Container, "for_project", classmethod(build))
    return runner
