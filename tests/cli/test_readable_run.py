from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path

import pytest
from rich.console import Console

from cuanta.cli.commands.mandate import _final
from cuanta.cli.document import MarkdownText
from cuanta.cli.presenters.plain import PlainPresenter, block_lines
from cuanta.domain.errors import CuantaError
from cuanta.domain.messages import msg
from cuanta.domain.progress import Status, note
from cuanta.domain.verification import DEFAULT_VERIFICATION
from cuanta.tui.i18n import Catalog
from tests.fakes import FakeRunner
from tests.support import assert_golden, invoke
from tests.tui.fakes import mandate_report


@pytest.mark.parametrize("language", ["es", "en"])
@pytest.mark.parametrize("task_type", ["investigation", "feature"])
def test_result_shows_investigation_answer_and_always_report_link(
    language: str, task_type: str
) -> None:
    original = mandate_report()
    path = f".cuanta/runs/{original.run.id}/report.md"
    report = replace(
        original, text="## SUMMARY\nRespuesta solicitada", task_type=task_type, report_path=path
    )
    document = _final(report, catalog=Catalog(language))
    markdown = [block.text for block in document.blocks if isinstance(block, MarkdownText)]
    assert markdown == ([report.text] if task_type == "investigation" else [])
    shown = "\n".join(line for block in document.blocks for line in block_lines(block))
    assert path in shown
    assert f"cuanta runs show {report.run.id}" in shown
    assert document.payload["report_text"] == report.text


@pytest.mark.parametrize("language", ["es", "en"])
def test_long_answer_is_clipped_without_changing_saved_payload(language: str) -> None:
    original = mandate_report()
    text = "\n".join(f"Finding {index}" for index in range(65))
    report = replace(original, text=text, task_type="investigation")
    document = _final(report, catalog=Catalog(language))
    markdown = [block.text for block in document.blocks if isinstance(block, MarkdownText)]
    assert markdown == ["\n".join(text.splitlines()[:60])]
    shown = "\n".join(line for block in document.blocks for line in block_lines(block))
    assert f"cuanta runs show {report.run.id}" in shown
    assert "respuesta completa" in shown if language == "es" else "complete answer" in shown
    assert "Finding 60" not in shown
    assert document.payload["report_text"] == text


def test_writing_answer_remains_available_with_verbose() -> None:
    report = replace(mandate_report(), task_type="feature", text="## SUMMARY\nDone")
    document = _final(report, verbose=True)
    assert any(
        isinstance(block, MarkdownText) and block.text == report.text for block in document.blocks
    )


def test_cached_jev_backend_uses_the_current_spanish_progress_sink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from cuanta.adapters.instinct.jev import JevInstinct
    from cuanta.bootstrap import Container
    from cuanta.domain.config import Config
    from cuanta.domain.errors import EnvironmentFailure

    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    container = Container(tmp_path, Config(instinct="jev"))
    try:
        backend = container.instinct_backend()
        assert isinstance(backend, JevInstinct)

        def transport(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline")

        backend._transport = httpx.MockTransport(transport)
        output = io.StringIO()
        container.progress = PlainPresenter(output, catalog=Catalog("es"))
        for question in ("first?", "second?"):
            with pytest.raises(EnvironmentFailure):
                backend.noul(question, {})
        assert output.getvalue().count("Nota: JEV no disponible") == 1
        assert "using local fallback" not in output.getvalue()
    finally:
        container.close()


def test_language_is_a_global_option(tmp_path: Path) -> None:
    result = invoke(["run", "--lang", "es", "--project", str(tmp_path)])
    assert "No such option" not in result.output
    assert "Ocurrió" in result.output


def test_notes_and_failures_use_the_same_catalog() -> None:
    out, err = io.StringIO(), io.StringIO()
    presenter = PlainPresenter(out, err, catalog=Catalog("es"))
    presenter.publish(note(Status.INFO, msg("limits.none")))
    presenter.fail(CuantaError("request failed", "try again"))
    assert "límites" in out.getvalue()
    assert len(err.getvalue().splitlines()) == 3
    assert "Ahora:" in err.getvalue()


@pytest.mark.parametrize("language", ["es", "en"])
def test_default_result_is_short_and_keeps_partial_accounting(language: str) -> None:
    report = mandate_report(ok=False)
    report = replace(report, run=replace(report.run, partial=True, cost_source="reported"))
    document = _final(report, catalog=Catalog(language))
    lines = [line for block in document.blocks for line in block_lines(block)]
    assert len(lines) <= 12
    shown = "\n".join(lines)
    assert "partial" in shown if language == "en" else "parcial" in shown
    assert "$0.42" in shown
    assert "scope hint" not in shown
    assert "utilization" not in shown
    assert "cap:" not in shown
    assert document.payload["report_text"] == report.text


def test_unexpected_failure_is_logged_without_an_internal_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cuanta.cli.commands import mandate

    def broken(*args: object, **kwargs: object) -> object:
        raise RuntimeError("private implementation fault")

    monkeypatch.setattr(mandate, "run_mandate", broken)
    result = invoke(["mandate", "--what", "Explain the fixture", "--project", str(tmp_path)])
    assert result.exit_code == 2
    assert "RuntimeError" not in result.output
    assert "Traceback" not in result.output
    logs = list((tmp_path / ".cuanta/logs").glob("????-??-??.log"))
    assert len(logs) == 1
    assert "Traceback" in logs[0].read_text(encoding="utf-8")
    assert logs[0].name in result.output


def test_plain_live_status_prints_at_most_once_per_minute() -> None:
    from cuanta.domain.progress import LiveStatus

    out = io.StringIO()
    presenter = PlainPresenter(out)
    for half_second in range(241):
        presenter.publish(LiveStatus(half_second / 2, half_second, "main", "Read", "cart.py"))
    assert len(out.getvalue().splitlines()) == 3
    assert "120 tokens" in out.getvalue().splitlines()[1]


def test_a_json_run_keeps_its_final_document(tmp_path: Path, fake_runner: FakeRunner) -> None:
    import json

    from tests.cli.test_engine_guarantees import forge, mandate_args
    from tests.fakes import FakeStream

    forge(tmp_path)
    fake_runner.streams["claude -p"] = FakeStream(
        ['{"type":"result","subtype":"success","total_cost_usd":0.001,"is_error":false}']
    )
    result = invoke([*mandate_args(tmp_path, "claude"), "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "ok"


def test_missing_engine_is_explained_in_spanish(tmp_path: Path, fake_runner: FakeRunner) -> None:
    from tests.cli.test_engine_guarantees import forge, mandate_args

    forge(tmp_path)
    fake_runner.binaries.clear()
    result = invoke([*mandate_args(tmp_path, "claude"), "--lang", "es"])
    assert result.exit_code == 3
    assert "no está instalado" in result.output
    assert "not found on PATH" not in result.output


def test_a_sandboxed_native_run_keeps_the_live_observer(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from typing import cast

    from cuanta.application.sandbox import SandboxResult
    from cuanta.bootstrap import Container
    from cuanta.cli.commands.mandate import MandateArgs, run_sandbox
    from cuanta.cli.output import GlobalOptions, OutputMode, OutputSettings
    from cuanta.cli.runtime import Session
    from cuanta.cli.theme import ThemeName
    from cuanta.domain.mandate import MandateRequest

    seen: list[object] = []

    def run(*args: object, **kwargs: object) -> SandboxResult:
        seen.append(kwargs.get("observer"))
        return SandboxResult(None, "fixture", True, report=mandate_report())

    container = cast(Container, SimpleNamespace(shared_ledger=lambda: None, run_sandboxed=run))
    settings = OutputSettings(OutputMode.PLAIN, ThemeName.DARK, False, False, False)
    session = Session(GlobalOptions(), settings, PlainPresenter(io.StringIO()), tmp_path)
    run_sandbox(session, container, MandateArgs(), (MandateRequest("bug", "Fix", "Evidence"), 0))
    assert callable(seen[0])


def test_multiple_agents_in_one_role_share_one_result_line() -> None:
    tokens = {f"language{index}-senior": 10 for index in range(8)}
    report = replace(mandate_report(), tokens_by_agent=tokens, handoffs=tuple(tokens))
    document = _final(report, catalog=Catalog("es"))
    lines = [line for block in document.blocks for line in block_lines(block)]
    assert len(lines) <= 12
    label = Catalog("es").keyed("role", "senior")
    assert sum(f"{label}:" in line for line in lines) == 1
    assert "80 tokens" in "\n".join(lines)


def test_run_progress_keeps_warnings_and_hides_repeated_forecasts() -> None:
    from cuanta.cli.live_run import RunProgress

    out = io.StringIO()
    progress = RunProgress(PlainPresenter(out), False)
    progress.publish(note(Status.INFO, msg("envelope.line_unknown", cache="cold")))
    progress.publish(note(Status.WARN, msg("envelope.failed", error="offline")))
    assert len(out.getvalue().splitlines()) == 1
    assert "offline" in out.getvalue()


@pytest.mark.parametrize(("cause", "visible"), [("audit.env", False), ("audit.unknown", True)])
def test_only_an_unexplained_model_change_gets_a_default_route_line(
    cause: str, visible: bool
) -> None:
    from cuanta.domain.audit import AuditRow, AuditStatus

    row = AuditRow(
        "python-senior",
        "senior",
        "planned",
        ("actual",),
        AuditStatus.MISMATCH,
        msg(cause, variable="override"),
    )
    report = replace(mandate_report(), audit=(row,))
    shown = "\n".join(line for block in _final(report).blocks for line in block_lines(block))
    assert ("Model differed" in shown) is visible


@pytest.mark.parametrize("language", ["es", "en"])
@pytest.mark.parametrize("width", [80, 120])
@pytest.mark.parametrize("view", ["hiss", "launch", "result", "live", "audit", "fallback"])
def test_readable_console_snapshots(language: str, width: int, view: str) -> None:
    from types import SimpleNamespace
    from typing import cast

    from cuanta.application.mandate_flow import Prepared
    from cuanta.cli.commands.mandate import launch_rows
    from cuanta.cli.document import Document, KeyValues, Line, Panel
    from cuanta.cli.output import OutputMode, OutputSettings
    from cuanta.cli.presenters.pretty import PrettyPresenter, build_theme
    from cuanta.cli.theme import ThemeName
    from cuanta.domain.limits import NO_LIMITS
    from cuanta.domain.mandate import MandateRequest
    from cuanta.domain.progress import LiveStatus
    from cuanta.domain.scout import DocsChoice, DocsReason

    t = Catalog(language)
    output = io.StringIO()
    settings = OutputSettings(OutputMode.PRETTY, ThemeName.DARK, True, False, False, language)
    presenter = PrettyPresenter(
        settings, Console(file=output, width=width, color_system=None, theme=build_theme(settings))
    )
    if view == "hiss":
        error = CuantaError(t("failure.unexpected"))
        error.log_path = ".cuanta/logs/2026-10-04.log"
        presenter.fail(error)
    elif view == "result":
        report = mandate_report(ok=False)
        presenter.render(_final(replace(report, run=replace(report.run, partial=True)), catalog=t))
    elif view == "audit":
        report = replace(
            mandate_report(),
            task_type="investigation",
            text="## SUMMARY\n"
            + ("Respuesta del agente." if language == "es" else "Agent answer."),
        )
        presenter.render(_final(report, catalog=t))
    elif view == "fallback":
        presenter.publish(note(Status.WARN, msg("instinct.unavailable")))
    elif view == "live":
        presenter.render(
            Document(blocks=(Line(t.live(LiveStatus(83, 2410, "main", "Read", "src/cart.py"))),))
        )
    else:
        request = MandateRequest(
            "bug",
            "Corrige el total del carrito",
            "AssertionError",
            where="src/cart.py",
            out_of_scope="docs",
        )
        prepared = cast(
            Prepared,
            SimpleNamespace(
                composed=SimpleNamespace(request=request),
                applied=None,
                spec=SimpleNamespace(model="claude-sonnet-5"),
                forecast=None,
                docs=DocsChoice(True, DocsReason.FORCED_ON),
                limits=NO_LIMITS,
                verification=DEFAULT_VERIFICATION,
            ),
        )
        rows = launch_rows(prepared, t)
        assert len(rows) <= 8
        presenter.render(Document(blocks=(Panel(t("run_output.launch"), (KeyValues(rows),)),)))
    presenter.close()
    assert_golden(f"run_{view}_{language}_{width}.txt", output.getvalue())
