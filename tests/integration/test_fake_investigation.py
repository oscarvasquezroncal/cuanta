from __future__ import annotations

import importlib.util
import json
import shutil
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

from cuanta.adapters.telemetry.otlp_receiver import map_payload
from cuanta.domain.anatomy import Phase, analyze_anatomy
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.read_efficiency import INVESTIGATION_FORMULA, read_efficiency
from cuanta.domain.report import file_refs
from cuanta.domain.spectrum import resolve_agents

ROOT = Path(__file__).parents[2]
FAKE = ROOT / "tests" / "fixtures" / "fake_claude.py"
FIXTURES = ROOT / "tests" / "fixtures" / "repos"
PRICING = """def cart_total(prices: list[float], percent: float) -> float:
    total = sum(prices)
    return round(total - total * percent / 100, 2)


def invoice_total(amount: float, percent: float, shipping: float) -> float:
    discounted = round(amount - amount * percent / 100, 2)
    return round(discounted + shipping, 2)
"""


@dataclass(frozen=True)
class FakeRun:
    mapped: tuple[LedgerEvent, ...]
    resolved: tuple[LedgerEvent, ...]
    stream: tuple[dict[str, object], ...]
    answer: str


class Response:
    def read(self) -> bytes:
        return b"{}"


def fixture_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fake_investigation", FAKE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project(tmp_path: Path, fixture: str) -> Path:
    root = tmp_path / "project"
    shutil.copytree(FIXTURES / fixture, root)
    if fixture == "python_strong":
        (root / "src" / "shop" / "pricing.py").write_text(PRICING, encoding="utf-8")
    return root


def exercise(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    pipeline: bool,
    opt_in: bool = True,
) -> FakeRun:
    payloads: list[dict[str, object]] = []

    def capture(request: urllib.request.Request, *, timeout: float) -> Response:
        assert timeout == 5
        assert request.full_url == "http://127.0.0.1:1/v1/logs"
        assert isinstance(request.data, bytes)
        payloads.append(cast(dict[str, object], json.loads(request.data)))
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", capture)
    monkeypatch.chdir(root)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("TRACEPARENT", "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "cuanta.run_id=R,service.name=claude-code")
    monkeypatch.setenv("FAKE_CLAUDE_BENCH", "1")
    if opt_in:
        monkeypatch.setenv("FAKE_CLAUDE_INVESTIGATION", "1")
    else:
        monkeypatch.delenv("FAKE_CLAUDE_INVESTIGATION", raising=False)
    arguments = ["-p", "Investigate the source.", "--model", "claude-sonnet-5"]
    if pipeline:
        agents = root.parent / "agents.json"
        agents.write_text(
            json.dumps({"architecture-analyst": {"model": "claude-sonnet-5"}}),
            encoding="utf-8",
        )
        arguments.extend(["--agents", str(agents)])
    assert fixture_module().main(arguments) == 0
    stream = tuple(
        cast(dict[str, object], json.loads(line)) for line in capsys.readouterr().out.splitlines()
    )
    answer = stream[-1]["result"]
    assert isinstance(answer, str)
    assert len(payloads) == 1
    mapped = tuple(
        replace(event, id=number)
        for number, event in enumerate(map_payload("/v1/logs", payloads[0]), 1)
    )
    return FakeRun(mapped, tuple(resolve_agents(mapped)), stream, answer)


def streamed_tools(stream: tuple[dict[str, object], ...]) -> tuple[str, ...]:
    tools: list[str] = []
    for row in stream:
        message = row.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_use":
                name = item.get("name")
                assert isinstance(name, str)
                tools.append(name)
    return tuple(tools)


@pytest.mark.parametrize("fixture", ["next_landing", "python_strong"])
@pytest.mark.parametrize("pipeline", [False, True], ids=["single", "pipeline"])
def test_fake_investigation_preserves_source_and_maps_real_read_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fixture: str,
    pipeline: bool,
) -> None:
    root = project(tmp_path, fixture)
    before = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    monkeypatch.setenv("FAKE_CLAUDE_FIX", "src/shop/pricing.py|percent / 100|percent / 10")
    done = exercise(root, monkeypatch, capsys, pipeline)
    after = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    assert after == before
    assert not (root / "CLAUDE.md").exists()
    reads = tuple(event for event in done.mapped if event.kind == "tool_result")
    assert reads
    assert all(event.tool_name == "Read" and event.success for event in reads)
    assert all(json.loads(event.raw)["attributed"] == "default" for event in reads)
    assert all((root / event.file_path).is_file() for event in reads)
    resolved_reads = tuple(event for event in done.resolved if event.kind == "tool_result")
    owner = "architecture-analyst" if pipeline else "main"
    assert {event.agent for event in resolved_reads} == {owner}
    tools = streamed_tools(done.stream)
    assert set(tools) == ({"Read", "Agent"} if pipeline else {"Read"})
    completed = tuple(event for event in done.mapped if event.kind == "subagent_completed")
    assert len(completed) == int(pipeline)
    if pipeline:
        decision = next(event for event in done.mapped if event.kind == "tool_decision")
        assert decision.tool_name == "Agent"
        assert decision.agent == "main"
        fields = json.loads(decision.raw)
        assert fields["cuanta.parameters"]["subagent_type"] == owner
        assert "allowed" in decision.raw
        assert owner in completed[0].raw
    anatomy = analyze_anatomy(done.resolved)
    usage = tuple(event for event in done.resolved if event.kind == "api_request")
    assert anatomy.totals.total == sum(event.total_tokens for event in usage)
    assert anatomy.totals.requests == len(usage) == len(anatomy.events)
    assert sum(phase.totals.total for phase in anatomy.phases) == anatomy.totals.total
    assert sum(agent.totals.total for agent in anatomy.agents) == anatomy.totals.total
    assert anatomy.totals.cost_usd == pytest.approx(sum(event.cost_usd or 0 for event in usage))
    phases = {event.phase for event in anatomy.events}
    assert {Phase.START, Phase.EXPLORATION, Phase.WRITING} <= phases
    assert (Phase.HANDOFF in phases) is pipeline
    assert {agent.agent for agent in anatomy.agents} == ({"main", owner} if pipeline else {"main"})
    result = done.stream[-1]
    assert result["total_cost_usd"] == pytest.approx(anatomy.totals.cost_usd)
    efficiency = read_efficiency(done.resolved, done.answer, task_type="investigation")
    assert efficiency.available
    assert efficiency.value == 1.0
    assert efficiency.formula == INVESTIGATION_FORMULA
    assert not efficiency.edited_files
    assert set(efficiency.read_files) == {event.file_path for event in resolved_reads}
    for reference in file_refs(done.answer):
        source = root / reference.path
        assert source.is_file()
        assert 1 <= reference.line <= len(source.read_text().splitlines())
    assert not any(event.tool_name in {"Write", "Edit", "Task"} for event in done.mapped)


def test_fake_investigation_report_override_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, "python_strong")
    report = tmp_path / "report.md"
    report.write_text("Custom delivered answer at src/shop/pricing.py:1.\n", encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_REPORT", str(report))
    done = exercise(root, monkeypatch, capsys, pipeline=False)
    assert done.answer == report.read_text(encoding="utf-8")


def test_fake_investigation_citations_follow_source_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, "python_strong")
    source = root / "src" / "shop" / "pricing.py"
    source.write_text("\n\n" + PRICING, encoding="utf-8")
    done = exercise(root, monkeypatch, capsys, pipeline=False)
    assert "src/shop/pricing.py:3-5" in done.answer
    assert "src/shop/pricing.py:8-10" in done.answer


def test_legacy_fake_keeps_calc_fix_and_default_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, "bugfix")
    monkeypatch.setenv("FAKE_CLAUDE_FIX", "src/calc/__init__.py|left - right|left + right")
    done = exercise(root, monkeypatch, capsys, pipeline=False, opt_in=False)
    assert done.answer == "done"
    assert "return left + right" in (root / "src" / "calc" / "__init__.py").read_text()
    assert streamed_tools(done.stream) == ("Agent", "Write", "Task")
    assert len([event for event in done.mapped if event.kind == "api_request"]) == 1
