from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from rich.console import Console

from cuanta.cli.document import Column, Commands, Document, KeyValues, Line, Table
from cuanta.cli.output import OutputMode, OutputSettings
from cuanta.cli.presenters.plain import PlainPresenter
from cuanta.cli.presenters.pretty import PrettyPresenter, build_theme
from cuanta.cli.theme import ThemeName
from cuanta.domain.progress import Status
from tests.fakes import FakeRunner
from tests.support import invoke, strip_ansi

SETTINGS = OutputSettings(
    OutputMode.PRETTY, ThemeName.DARK, unicode=True, emoji=False, verbose=False
)
BRACKETED = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Cc")), min_size=1, max_size=30
).map(lambda text: f"[{text}] x [/{text}]")


def _pretty(document: Document, width: int = 200) -> str:
    buffer = io.StringIO()
    console = Console(
        file=buffer, width=width, theme=build_theme(SETTINGS), force_terminal=False, highlight=False
    )
    PrettyPresenter(SETTINGS, console).render(document)
    return strip_ansi(buffer.getvalue())


def _plain(document: Document) -> str:
    buffer = io.StringIO()
    PlainPresenter(out=buffer, err=io.StringIO()).render(document)
    return buffer.getvalue()


@settings(max_examples=60, deadline=None)
@given(BRACKETED)
def test_bracketed_text_renders_verbatim(text: str) -> None:
    document = Document(
        blocks=(
            KeyValues((("detail", text),)),
            Table("", (Column("value"),), ((text,),)),
            Line(text, Status.WARN),
        )
    )
    pretty = _pretty(document)
    plain = _plain(document)
    assert pretty.count(text) == 3
    assert plain.count(text) == 3


def test_otel_detail_survives_pretty() -> None:
    rendered = _pretty(Document(blocks=(KeyValues((("telemetry codex", "no [otel] table"),)),)))
    assert "no [otel] table" in rendered


def test_fix_commands_never_wrap_at_80_columns() -> None:
    fixes = (
        ("engine codex", "npm install -g @openai/codex"),
        (
            "listener",
            "cuanta listen --background --project C:/Users/somebody/projects/very/long/path",
        ),
    )
    rendered = _pretty(Document(blocks=(Commands("fixes", fixes),)), width=80)
    lines = rendered.splitlines()
    for _, command in fixes:
        assert any(line.strip() == command for line in lines), rendered


def test_doctor_lists_fixes_in_their_own_block(tmp_path: Path, fake_runner: FakeRunner) -> None:
    result = invoke(
        ["doctor", "--project", str(tmp_path), "--no-emoji"], env={"COLUMNS": "80"}, pretty=True
    )
    output = strip_ansi(result.stdout)
    assert "fixes" in output
    assert any(line.strip() == "npm install -g @openai/codex" for line in output.splitlines())


def test_telemetry_env_cmd(tmp_path: Path, fake_runner: FakeRunner) -> None:
    result = invoke(["telemetry", "env", "--shell", "cmd", "--json", "--project", str(tmp_path)])
    snippet = json.loads(result.stdout)["snippet"]
    assert "set OTEL_EXPORTER_OTLP_PROTOCOL=http/json" in snippet
    assert "$env:" not in snippet


def test_telemetry_env_unknown_shell_prints_every_form(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CUANTA_SHELL", "tcsh")
    document = json.loads(invoke(["telemetry", "env", "--json", "--project", str(tmp_path)]).stdout)
    assert set(document["snippets"]) == {"cmd", "pwsh", "bash"}
    monkeypatch.setenv("CUANTA_SHELL", "cmd")
    detected = json.loads(invoke(["telemetry", "env", "--json", "--project", str(tmp_path)]).stdout)
    assert detected["shell"] == "cmd"
