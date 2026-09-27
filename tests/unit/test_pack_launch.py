from __future__ import annotations

import json
from collections.abc import Callable
from itertools import count
from pathlib import Path

import pytest

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine
from cuanta.adapters.engines.codex import CodexEngine
from cuanta.adapters.storage.capsule_store import FileCapsuleStore
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.application.cross_engine import CrossEnginePipeline
from cuanta.application.engine_run import EngineLauncher
from cuanta.application.mandate_flow import MandateFlow, MandateOptions, preview_of
from cuanta.application.route_apply import RouteOptions
from cuanta.domain.agents import AgentsPlan, contextual_agents, guarded_agents
from cuanta.domain.change_plan import ChangePlan, EditTarget, apply_overrides
from cuanta.domain.detection import Stack
from cuanta.domain.engine import EngineEvent, EngineOutcome, EngineRequest
from cuanta.domain.graph_policy import graphless_prompt
from cuanta.domain.mandate import MandateRequest, fill_request
from cuanta.domain.pack import ContextPack
from cuanta.domain.routing import Role
from tests.fakes import FakeRunner
from tests.unit.test_cross_engine import Recorder, ScriptedEngine, plan
from tests.unit.test_engine_profiles import flow, launcher
from tests.unit.test_route_apply import AGENT, routing


def indexed_pack(role: str) -> ContextPack:
    return ContextPack(
        f"STABLE {role}",
        f"EXCERPT {role} src/cart.py:4-7",
        "VOLATILE MUST NOT BE DUPLICATED",
        (),
        (),
        20,
        2000,
    )


def test_pack_context_follows_stable_instructions_and_precedes_volatile_hint() -> None:
    request = MandateRequest("bug", "Fix checkout", "Wrong sum", out_of_scope="renderer")
    prompt = fill_request(
        "Stable mandate instructions.\n\n=== REQUEST ===\n",
        request,
        "Volatile scope choice",
        "Pack stable prefix\n\nL2 numbered excerpts",
    )
    assert prompt.startswith("Stable mandate instructions.")
    assert prompt.index("Stable mandate") < prompt.index("Pack stable")
    assert prompt.index("Pack stable") < prompt.index("L2 numbered")
    assert prompt.index("L2 numbered") < prompt.index("Volatile scope")
    assert prompt.index("Volatile scope") < prompt.index("=== REQUEST ===")


def test_contextual_agents_preserve_sanitized_fields_and_original_prompts() -> None:
    original = AgentsPlan(
        {
            "python-senior": {"prompt": "Senior body", "tools": ["Read", "Write", "Bash"]},
            "tester": {"prompt": "Tester body", "tools": ["Read", "Bash"]},
            "architecture-analyst": {"prompt": "Analyst body"},
        },
        {"python-senior": Role.SENIOR, "tester": Role.TESTER, "architecture-analyst": Role.ANALYST},
    )
    protected = guarded_agents(original, ChangePlan(guard=("src/private/**",)))
    enriched = contextual_agents(
        protected, {Role.SENIOR: "Editable card", Role.TESTER: "Fresh anchored fact"}
    )
    assert enriched.agents["python-senior"]["prompt"] == "Senior body\n\nEditable card"
    assert enriched.agents["tester"]["prompt"] == "Tester body\n\nFresh anchored fact"
    assert enriched.agents["architecture-analyst"] == protected.agents["architecture-analyst"]
    for name, spec in enriched.agents.items():
        assert spec.get("tools") == protected.agents[name].get("tools")
        assert spec.get("disallowedTools") == protected.agents[name].get("disallowedTools")
    assert original.agents["python-senior"]["prompt"] == "Senior body"
    assert protected.agents["python-senior"]["prompt"] == "Senior body"
    assert contextual_agents(original, {}) is original


def test_native_pack_reaches_preview_command_and_guarded_role_prompts(tmp_path: Path) -> None:
    folder = tmp_path / ".claude" / "agents"
    folder.mkdir(parents=True)
    original: dict[str, str] = {}
    for name in ("python-senior", "tester", "architecture-analyst", "docs-updater"):
        original[name] = AGENT.format(name=name)
        (folder / f"{name}.md").write_text(original[name], encoding="utf-8")
    previews: list[EngineRequest] = []

    class CapturingClaude(ClaudeCodeEngine):
        def command(self, request: EngineRequest) -> list[str]:
            previews.append(request)
            return super().command(request)

    engine = CapturingClaude(FakeRunner())
    initial = ChangePlan(edit=(EditTarget("src/cart.py", 1.0),), guard=("src/private/**",))
    calls: list[tuple[str, str, ChangePlan | None]] = []

    def context_pack(
        request: MandateRequest, depth: str, role: str, protection: ChangePlan | None
    ) -> ContextPack:
        calls.append((depth, role, protection))
        return indexed_pack(role)

    service = MandateFlow(
        flow(tmp_path, engine).service,
        lambda _: engine,
        launcher,
        Stack,
        lambda _: ({}, None),
        str(tmp_path),
        "claude",
        0.0,
        routing=routing(tmp_path, {}),
        change_plan=lambda _: initial,
        context_pack=context_pack,
    )
    request = MandateRequest(
        "feature", "Extend checkout", tests="checkout fixture", out_of_scope="src/private/**"
    )
    overrides = (("src/cart.py", "read"), ("src/export.py", "edit"))
    options = MandateOptions(
        route=RouteOptions(mode="fixed"), depth="quick", plan_overrides=overrides
    )
    prepared = service.prepare(request, 0, options, preview=True)
    expected = apply_overrides(initial, overrides)
    assert calls == [
        ("quick", "orchestrator", expected),
        ("quick", "senior", expected),
        ("quick", "tester", expected),
    ]
    prompt = prepared.spec.prompt
    assert prompt.startswith("GRAPH_MODE=none")
    assert prompt.index("GRAPH_MODE=none") < prompt.index("STABLE orchestrator")
    assert prompt.index("STABLE orchestrator") < prompt.index("EXCERPT orchestrator")
    assert prompt.index("EXCERPT") < prompt.index("WHAT:")
    assert prompt.count(request.what) == 1
    assert "VOLATILE MUST NOT BE DUPLICATED" not in prompt
    assert prepared.composed.prompt == prompt == preview_of(prepared).prompt
    assert [sent.prompt for sent in previews] == [prompt]
    assert request.what not in preview_of(prepared).command
    assert prepared.spec.stable_prefix
    assert "--exclude-dynamic-system-prompt-sections" in prepared.composed.command
    written = json.loads(Path(prepared.spec.agents_file).read_text(encoding="utf-8"))
    for name, role in (("python-senior", "senior"), ("tester", "tester")):
        assert written[name]["prompt"].startswith(graphless_prompt(f"Body of {name}."))
        assert written[name]["prompt"].index(f"Body of {name}.") < written[name]["prompt"].index(
            f"STABLE {role}"
        )
        assert written[name]["prompt"].index(f"STABLE {role}") < written[name]["prompt"].index(
            f"EXCERPT {role}"
        )
        assert "VOLATILE" not in written[name]["prompt"]
        assert "Write(src/private/**)" in written[name]["disallowedTools"]
    assert written["architecture-analyst"]["prompt"] == graphless_prompt(
        "Body of architecture-analyst."
    )
    assert written["docs-updater"]["prompt"] == graphless_prompt("Body of docs-updater.")
    repeated = service.prepare(request, 0, options, preview=True)
    assert repeated.spec.agents_file == prepared.spec.agents_file
    assert repeated.spec.prompt == prompt
    for name, content in original.items():
        assert (folder / f"{name}.md").read_text(encoding="utf-8") == content


def test_codex_native_pack_is_available_as_text_without_claude_flags(tmp_path: Path) -> None:
    engine = CodexEngine(FakeRunner())
    service = flow(tmp_path, engine)
    service._context_pack = lambda request, depth, role, protection: indexed_pack(role)
    prepared = service.prepare(
        MandateRequest("bug", "Repair cart", "Wrong sum", out_of_scope="docs"),
        0,
        MandateOptions(simple=True),
        preview=True,
    )
    assert prepared.spec.prompt.index("GRAPH_MODE=none") < prepared.spec.prompt.index("STABLE")
    assert prepared.spec.prompt.index("STABLE") < prepared.spec.prompt.index("EXCERPT")
    assert prepared.spec.prompt.index("EXCERPT") < prepared.spec.prompt.index("=== REQUEST ===")
    assert "VOLATILE" not in prepared.spec.prompt
    assert "--exclude-dynamic-system-prompt-sections" not in prepared.composed.command


@pytest.mark.parametrize("kind", ["bug", "investigation"])
def test_cross_packs_precede_request_and_handoff_with_the_effective_role_plan(
    tmp_path: Path, kind: str
) -> None:
    ledger = MemoryLedger()
    ids = count(1)
    prompts: list[str] = []
    sent: list[EngineRequest] = []
    calls: list[tuple[str, str, ChangePlan | None]] = []

    class CapturingEngine(ScriptedEngine):
        def run(
            self, request: EngineRequest, on_event: Callable[[EngineEvent], None]
        ) -> EngineOutcome:
            sent.append(request)
            return super().run(request, on_event)

    def engine_launcher(name: str) -> EngineLauncher:
        return EngineLauncher(
            CapturingEngine(name, prompts),
            ledger,
            FixedClock(),
            lambda: f"RUN{next(ids)}",
            lambda size: b"\x01" * size,
            "project",
            4318,
            None,
        )

    def context_pack(
        request: MandateRequest, depth: str, role: str, protection: ChangePlan | None
    ) -> ContextPack:
        calls.append((depth, role, protection))
        return indexed_pack(role)

    protection = ChangePlan(
        edit=(EditTarget("src/cart.py", 1.0),),
        guard=("src/private/**",),
        read_only=kind == "investigation",
    )
    pipeline = CrossEnginePipeline(
        engine_launcher,
        tuple,
        FileCapsuleStore(tmp_path),
        str(tmp_path),
        5.0,
        depth="deep",
        change_plan=lambda _: protection,
        context_pack=context_pack,
    )
    request = MandateRequest(kind, "Inspect checkout", "Wrong result", out_of_scope="docs")
    report = pipeline.run(request, plan(), Recorder())
    assert report.ok
    assert [entry[:2] for entry in calls] == [
        ("deep", "analyst"),
        ("deep", "senior"),
        ("deep", "tester"),
    ]
    for index, (_depth, role, effective) in enumerate(calls):
        assert effective is not None
        assert effective.guard == protection.guard
        readonly = kind == "investigation" or role == "analyst"
        assert effective.read_only is readonly
        assert effective.edit == (() if readonly else protection.edit)
        assert sent[index].stable_prefix
        prompt = sent[index].prompt
        assert prompt.index("Do only your role's part.") < prompt.index(f"STABLE {role}")
        assert prompt.index(f"STABLE {role}") < prompt.index(f"EXCERPT {role}")
        assert prompt.index("EXCERPT") < prompt.index("=== REQUEST ===")
        assert prompt.index("=== REQUEST ===") < prompt.index("=== HANDOFF")
        assert prompt.count(request.what) == 1
        assert "VOLATILE" not in prompt
    assert '{"from": "model-analyst"}' in sent[1].prompt
    assert '{"from": "model-senior"}' in sent[2].prompt
