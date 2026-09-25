from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine
from cuanta.adapters.engines.codex import CodexEngine
from cuanta.adapters.engines.opencode import OpenCodeEngine
from cuanta.adapters.instinct.heuristic import HeuristicInstinct
from cuanta.adapters.instinct.jev import KEY_ENV, JevInstinct
from cuanta.adapters.instinct.llm import LlmInstinct
from cuanta.domain.engine import EngineEvent, EngineOutcome, EngineRequest, RunResult
from cuanta.domain.instinct import SCOPES, Choice, Noul
from cuanta.domain.models import ModelEntry
from cuanta.ports.engine import Engine
from cuanta.ports.instinct import Instinct
from cuanta.ports.models import ModelCatalog
from tests.fakes import FakeRunner, FakeStream

CLAUDE_LINES = [
    '{"type":"system","subtype":"init","session_id":"s","model":"m"}',
    '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t","name":"Read","input":{}}]}}',
    '{"type":"result","subtype":"success","is_error":false,"num_turns":1,"total_cost_usd":0.1,"session_id":"s","modelUsage":{"m":{"inputTokens":1,"outputTokens":1}}}',
]
CODEX_LINES = [
    '{"type":"thread.started","thread_id":"s"}',
    '{"type":"item.completed","item":{"id":"i","type":"command_execution","command":"ls"}}',
    '{"type":"turn.completed","usage":{"input_tokens":5,"cached_input_tokens":0,"output_tokens":1}}',
]
OPENCODE_LINES = [
    '{"type":"tool_use","sessionID":"s","part":{"tool":"read","callID":"c","state":{"input":{}}}}',
    '{"type":"step_finish","sessionID":"s","part":{"cost":0.1,"tokens":{"input":1,"output":1,"reasoning":0,"cache":{"read":0,"write":0}}}}',
]

ENGINES: dict[str, tuple[Callable[[FakeRunner], Engine], str, list[str]]] = {
    "claude": (ClaudeCodeEngine, "claude", CLAUDE_LINES),
    "codex": (CodexEngine, "codex", CODEX_LINES),
    "opencode": (OpenCodeEngine, "opencode", OPENCODE_LINES),
}


@pytest.fixture(params=sorted(ENGINES))
def engine_case(request: pytest.FixtureRequest) -> tuple[Engine, str, FakeRunner]:
    factory, binary, lines = ENGINES[request.param]
    runner = FakeRunner(binaries={binary: f"/bin/{binary}"}, streams={binary: FakeStream(lines)})
    return factory(runner), binary, runner


def test_engine_contract(engine_case: tuple[Engine, str, FakeRunner], tmp_path: Path) -> None:
    engine, binary, runner = engine_case
    assert engine.name == binary
    assert engine.available()
    request = EngineRequest(
        prompt="do it", cwd=str(tmp_path), env={"CUANTA_RUN_ID": "R"}, model="m"
    )
    command = engine.command(request)
    assert command[0] == binary
    assert "do it" not in command
    assert isinstance(engine.missing_flags(), tuple)
    seen: list[object] = []
    outcome = engine.run(request, seen.append)
    assert outcome.ok
    assert outcome.tool_calls == 1
    assert isinstance(outcome.result, RunResult)
    assert outcome.tokens > 0
    assert isinstance(seen[-1], RunResult)
    assert runner.stdins[-1] == "do it" or "--file" in runner.calls[-1]


class _LlmEngine:
    @property
    def name(self) -> str:
        return "claude"

    def available(self) -> bool:
        return True

    def version(self) -> str:
        return "x"

    def missing_flags(self) -> tuple[str, ...]:
        return ()

    def cancel(self) -> None:
        return None

    def command(self, request: EngineRequest) -> list[str]:
        return ["claude"]

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        if "p_yes" in request.prompt:
            text = '{"p_yes": 0.25}'
        else:
            text = 'Sure: {"option": "normal", "probability": 0.7}'
        result = RunResult(True, "success", 0.001, 1, "s", (), text)
        on_event(result)
        return EngineOutcome(0, result, 0)


def _jev_transport(request: httpx.Request) -> httpx.Response:
    question = json.loads(request.content)["questions"]["q"]
    if question["type"] == "noul":
        answer: dict[str, object] = {"type": "noul", "noul": 0.25}
    else:
        answer = {"type": "choice", "choice": "normal", "confidence": 0.7}
    return httpx.Response(200, json={"answers": {"q": answer}})


@pytest.fixture(params=["heuristic", "jev", "llm"])
def backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Instinct:
    monkeypatch.setenv(KEY_ENV, "k")
    if request.param == "heuristic":
        return HeuristicInstinct()
    if request.param == "jev":
        return JevInstinct(transport=httpx.MockTransport(_jev_transport))
    return LlmInstinct(_LlmEngine(), ".")


def test_instinct_contract(backend: Instinct) -> None:
    assert backend.available()[0]
    assert isinstance(backend.remote, bool)
    choice, receipt = backend.choose(
        "size?", SCOPES, {"kind": "scope", "what": "fix totals", "where": "a.py"}
    )
    assert isinstance(choice, Choice)
    assert choice.option in SCOPES
    assert 0.0 <= choice.probability <= 1.0
    assert receipt.backend == backend.name
    assert receipt.latency_ms >= 0
    answer, _ = backend.noul("network?", {"prior": 0.25})
    assert isinstance(answer, Noul)
    assert 0.0 <= answer.p_yes <= 1.0


def _catalogs(tmp_path: Path) -> list[ModelCatalog]:
    from cuanta.adapters.models.claude_catalog import ClaudeCatalog
    from cuanta.adapters.models.codex_catalog import CodexCatalog
    from cuanta.adapters.models.opencode_catalog import OpenCodeCatalog
    from cuanta.adapters.system.prices import load_prices

    runner = FakeRunner(binaries={"codex": "/bin/codex", "opencode": "/bin/opencode"})
    prices = load_prices()
    return [
        ClaudeCatalog(tmp_path, tmp_path, {}, prices, installed=True, managed=()),
        CodexCatalog(runner, tmp_path, {}, prices),
        OpenCodeCatalog(runner, tmp_path, tmp_path),
    ]


def test_model_catalog_contract(tmp_path: Path) -> None:
    for catalog in _catalogs(tmp_path):
        assert catalog.available()
        entries = catalog.list()
        assert isinstance(entries, tuple)
        assert all(isinstance(entry, ModelEntry) for entry in entries)
        assert all(entry.engine == catalog.engine for entry in entries)


def test_bench_sandbox_contract(tmp_path: Path) -> None:
    import sys

    from cuanta.adapters.bench.sandbox import LocalBenchSandbox
    from cuanta.adapters.system.process_runner import SubprocessRunner
    from cuanta.domain.bench import BenchTask
    from cuanta.domain.mandate import MandateRequest
    from cuanta.ports.bench import BenchSandbox
    from tests.support import FIXTURES

    sandbox: BenchSandbox = LocalBenchSandbox(
        FIXTURES / "repos", tmp_path / "kit", SubprocessRunner(), sys.executable, tmp_path
    )
    task = BenchTask(
        "contract",
        ("mini",),
        "bugfix",
        MandateRequest(type="bug", what="w"),
        "p",
        {"tests/test_contract.py": "def test_ok() -> None:\n    assert True\n"},
    )
    root = sandbox.prepare(task, True, "contract")
    assert Path(root, "src", "calc", "__init__.py").is_file()
    accepted, output = sandbox.accept(task, root)
    assert accepted is True, output
    sandbox.discard(root)
    assert not Path(root).exists()
