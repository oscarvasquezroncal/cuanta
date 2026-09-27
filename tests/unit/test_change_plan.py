from __future__ import annotations

from pathlib import Path

import pytest

from cuanta.adapters.engines.claude_code import build_command
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.application.engine_run import EngineLauncher, LaunchSpec
from cuanta.domain.change_plan import (
    ChangePlan,
    EditTarget,
    compile_change_plan,
    deny_rules,
    move_plan,
    plan_metrics,
)
from cuanta.domain.code_index import IndexedFile, IndexRow
from cuanta.domain.mandate import MandateRequest
from tests.unit.test_lean_session import NamedEngine


def _file(path: str) -> IndexedFile:
    return IndexedFile(path, "current", "typescript", 100, coverage="ast")


@pytest.mark.parametrize(
    "out", ["Do not touch `hero-nutfall-3d/**`.", "Sin tocar `hero-nutfall-3d/**`."]
)
def test_gsap_plan_compiles_edit_read_protected_and_verify_without_remote(out: str) -> None:
    paths = (
        "src/app/globals.css",
        "src/lib/gsap.ts",
        "src/components/hero-nutfall-3d/view.tsx",
        "tests/gsap.test.ts",
    )
    files = tuple(_file(path) for path in paths)
    tests = (
        IndexRow(
            "test",
            paths[3],
            "current",
            "ast",
            "tsc --noEmit\nnpm run build",
            target=paths[1],
            relation="tests",
        ),
    )
    request = MandateRequest(
        "bug",
        "Fix GSAP reveal fallback",
        "GSAP must leave content visible",
        "src/app/globals.css src/lib/gsap.ts",
        out_of_scope=out,
    )
    plan = compile_change_plan(
        request, files, (), (), (), (), (), tests, ("tsc --noEmit", "npm run lint")
    )
    assert {item.path for item in plan.edit} == {paths[0], paths[1], paths[3]}
    assert "src/components/hero-nutfall-3d/**" in plan.guard
    assert plan.verify == ("tsc --noEmit", "npm run lint", "npm run build")
    assert all(0 < item.confidence <= 1 for item in plan.edit)
    assert "Edit(src/components/hero-nutfall-3d/**)" in deny_rules(plan)
    assert "Write(src/components/hero-nutfall-3d/**)" in deny_rules(plan)


def test_future_protected_paths_survive_without_current_files() -> None:
    plan = compile_change_plan(
        MandateRequest(
            "feature",
            "Add cart",
            out_of_scope="Keep future/private.py and future/generated/** untouched",
        ),
        (_file("cart.py"),),
        (),
        (),
        (),
        (),
        (),
        (),
    )
    assert "future/private.py" in plan.guard and "future/generated/**" in plan.guard
    assert all(".." not in path for path in plan.guard)


def test_investigation_and_moved_choices_preserve_read_only() -> None:
    plan = compile_change_plan(
        MandateRequest("investigation", "Explain cart.py"),
        (_file("cart.py"),),
        (),
        (),
        (),
        (),
        (),
        (),
    )
    assert not plan.edit and plan.read == ("cart.py",) and plan.read_only
    moved = move_plan(plan, "cart.py", "edit")
    assert not moved.edit and moved.read == ("cart.py",)
    with pytest.raises(ValueError, match="inside the project"):
        move_plan(plan, "../outside.py", "edit")


def test_actual_snapshot_paths_distinguish_unplanned_and_guarded_changes() -> None:
    plan = ChangePlan((EditTarget("cart.py", 1),), guard=("renderer/**",))
    result = plan_metrics(plan, ("cart.py", "new.py", "renderer/view.tsx"), "claude")
    assert result["out_of_plan_edits"] == ("new.py", "renderer/view.tsx")
    assert result["guard_violations"] == ("renderer/view.tsx",)
    assert plan_metrics(plan, (), "codex")["enforcement"] == "best-effort"


@pytest.mark.parametrize("readonly", [False, True])
def test_central_strict_policy_has_no_shell_or_writer_bypass(
    tmp_path: Path, readonly: bool
) -> None:
    plan = ChangePlan((EditTarget("cart.py", 1),), guard=("renderer/**",), read_only=readonly)
    engine = NamedEngine("claude")
    launcher = EngineLauncher(
        engine,
        MemoryLedger(),
        FixedClock(),
        lambda: "RUN",
        lambda size: b"x" * size,
        "fixture",
        4318,
        None,
    )
    request = launcher.request(
        LaunchSpec(
            "mandate",
            "request",
            str(tmp_path),
            ("Read", "Edit", "Bash", "Agent"),
            read_only=readonly,
            change_plan=plan,
        ),
        "RUN",
        "trace",
        None,
    )
    command = build_command(("claude",), request)
    tools = command[command.index("--tools") + 1].split(",")
    assert "Bash" not in tools and "PowerShell" not in tools and "Skill" not in tools
    assert "Agent" in tools and "Task" in tools
    assert request.strict_guard
    assert "Bash" in request.disallowed_tools
    assert (
        all(
            writer in request.disallowed_tools
            for writer in ("Edit", "Write", "MultiEdit", "NotebookEdit")
        )
        if readonly
        else "Write(renderer/**)" in request.disallowed_tools
    )
    if readonly:
        assert "Edit" not in tools and "Write" not in tools
