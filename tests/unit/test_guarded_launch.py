from __future__ import annotations

import json
import shlex
import sys
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine
from cuanta.adapters.engines.codex import CodexEngine
from cuanta.adapters.storage.sqlite_index import SqliteIndex
from cuanta.application.change_plan import IndexChangePlan
from cuanta.application.engine_run import LaunchSpec
from cuanta.bootstrap import Container
from cuanta.domain.change_plan import (
    EXECUTION,
    WRITERS,
    ChangePlan,
    EditTarget,
    apply_overrides,
    compile_change_plan,
    guarded,
    move_plan,
    path_matches,
    plan_metrics,
)
from cuanta.domain.code_index import IndexedFile
from cuanta.domain.config import Config
from cuanta.domain.mandate import MandateRequest
from tests.fakes import FakeRunner


def _files(paths: tuple[str, ...]) -> tuple[IndexedFile, ...]:
    return tuple(IndexedFile(path, "current", "typescript", 100, coverage="ast") for path in paths)


def _plan(request: MandateRequest, paths: tuple[str, ...]) -> ChangePlan:
    return compile_change_plan(request, _files(paths), (), (), (), (), (), ())


def test_unprotected_full_session_keeps_explicit_user_profile(tmp_path: Path) -> None:
    container = Container(project=tmp_path, runner=FakeRunner(), config=Config(run_session="full"))
    try:
        request = container.launcher(
            ClaudeCodeEngine(container.runner), container.shared_ledger(), telemetry=False
        ).request(
            LaunchSpec("mandate", "change cart", str(tmp_path), (), change_plan=ChangePlan()),
            "RUN",
            "TRACE",
            None,
        )
        assert not request.settings_file and not request.mcp_config
        assert request.setting_sources is None and not request.strict_guard
    finally:
        container.close()


@pytest.mark.parametrize("guard", ["src/**", "*.tsx", "src/private.tsx"])
def test_edit_override_cannot_cross_a_remaining_guard(guard: str) -> None:
    plan = ChangePlan(read=("src/private.tsx",), guard=(guard, "src/private.tsx"))
    moved = move_plan(plan, "src/private.tsx", "edit")
    if guard == "src/private.tsx":
        assert moved.edit == (EditTarget("src/private.tsx", 1.0, "explicit user selection"),)
    else:
        assert not moved.edit and moved.read == ("src/private.tsx",)
        assert guard in moved.guard


def test_new_guard_demotes_all_existing_overlapping_edits_to_read() -> None:
    plan = ChangePlan((EditTarget("src/cart.tsx", 1), EditTarget("tests/cart.ts", 0.7)))
    moved = move_plan(plan, "src/**", "guard")
    assert moved.edit == (EditTarget("tests/cart.ts", 0.7),)
    assert moved.read == ("src/cart.tsx",)
    assert moved.guard == ("src/**",)
    assert all(not guarded(item.path, moved) for item in moved.edit)


def test_broad_edit_pattern_cannot_cover_a_remaining_narrower_guard() -> None:
    moved = move_plan(ChangePlan(guard=("src/private/**",)), "src/**", "edit")
    assert not moved.edit and moved.read == ("src/**",)
    assert moved.guard == ("src/private/**",)


def test_partially_overlapping_edit_and_guard_globs_remain_protected() -> None:
    moved = move_plan(ChangePlan(guard=("src/*Card.tsx",)), "src/cart*.tsx", "edit")
    assert not moved.edit and moved.read == ("src/cart*.tsx",)


def test_disjoint_wildcard_edit_keeps_its_edit_role() -> None:
    moved = move_plan(ChangePlan(guard=("renderer/**",)), "src/cart*.tsx", "edit")
    assert moved.edit[0].path == "src/cart*.tsx"
    assert moved.guard == ("renderer/**",)


def test_final_override_choices_release_guards_before_edits_regardless_of_input_order() -> None:
    plan = ChangePlan(read=("src/cart.tsx",), guard=("src/**",))
    choices = (("src/cart.tsx", "edit"), ("src/**", "read"))
    assert apply_overrides(plan, choices) == apply_overrides(plan, tuple(reversed(choices)))
    assert apply_overrides(plan, choices).edit[0].path == "src/cart.tsx"
    repeated = apply_overrides(plan, (("src/**", "guard"), ("src/**", "read")))
    assert repeated.guard == ()


@pytest.mark.parametrize(
    ("path", "pattern", "matches"),
    [
        ("src/cart.tsx", "*.tsx", True),
        ("src/cart.tsx", "src/*.tsx", True),
        ("src/sub/cart.tsx", "src/*.tsx", False),
        ("src/cart.tsx", "src/**/*.tsx", True),
        ("src/sub/cart.tsx", "src/**/*.tsx", True),
        ("vendor/pkg/src/cart.tsx", "src/**", True),
        ("vendor/pkg/src/components/cart.tsx", "src/components/**", False),
        ("src/components/cart.tsx", "src/components/**", True),
    ],
)
def test_guard_glob_matching_tracks_claude_permission_segment_semantics(
    path: str, pattern: str, matches: bool
) -> None:
    assert path_matches(path, pattern) is matches


def test_readonly_move_clears_even_preexisting_invalid_edits() -> None:
    moved = move_plan(ChangePlan((EditTarget("old.py", 1),), read_only=True), "new.py", "edit")
    assert not moved.edit and moved.read == ("new.py", "old.py")


@pytest.mark.parametrize("phrase", ["Do not change anything", "No modifiques nada"])
@pytest.mark.parametrize("field", ["what", "why", "where", "constraints", "out_of_scope"])
def test_explicit_global_readonly_language_protects_all_files_in_a_feature_request(
    phrase: str, field: str
) -> None:
    request = replace(MandateRequest("feature", "Inspect src/cart.tsx"), **{field: phrase})
    plan = _plan(request, ("src/cart.tsx", "src/renderer/view.tsx"))
    assert plan.read_only and not plan.edit
    assert guarded("future.tsx", plan)


@pytest.mark.parametrize(
    "out",
    [
        "Do not modify src/renderer/**",
        "No modifiques src/renderer/**",
        "No changes to src/renderer/**",
        "Do not change anything in src/renderer/**",
        "No modifiques nada en src/renderer/**",
    ],
)
def test_file_specific_exclusions_leave_the_requested_feature_writable(out: str) -> None:
    plan = _plan(
        MandateRequest("feature", "Change src/cart.tsx", out_of_scope=out),
        ("src/cart.tsx", "src/renderer/view.tsx"),
    )
    assert not plan.read_only and {item.path for item in plan.edit} == {"src/cart.tsx"}
    assert guarded("src/renderer/view.tsx", plan)


@pytest.mark.parametrize("out", ["Do not touch *.tsx", "Sin tocar *.tsx", "Exclude **/*.tsx"])
def test_unquoted_glob_exclusions_protect_existing_and_future_paths(out: str) -> None:
    plan = _plan(
        MandateRequest("feature", "Change src/cart.tsx and src/cart.ts", out_of_scope=out),
        ("src/cart.tsx", "src/cart.ts"),
    )
    assert {item.path for item in plan.edit} == {"src/cart.ts"}
    assert guarded("src/new.tsx", plan)
    assert not guarded("src/new.ts", plan)


def test_override_composition_releases_parent_guard_before_final_edit_choice(
    tmp_path: Path,
) -> None:
    index = SqliteIndex(tmp_path / "index.db")
    try:
        index.replace_files(_files(("src/cart.tsx", "src/private.tsx")), ())
        service = IndexChangePlan(index, lambda: "2026-09-27T00:00:00Z")
        request = MandateRequest("feature", "Change src/cart.tsx", out_of_scope="src/**")
        plan = service.compile(request, (("src/cart.tsx", "edit"), ("src/**", "read")))
        assert {item.path for item in plan.edit} == {"src/cart.tsx"}
        assert not plan.guard
        protected = service.compile(
            request, (("src/cart.tsx", "edit"), ("src/private.tsx", "guard"))
        )
        assert not protected.edit
        assert "src/**" in protected.guard
    finally:
        index.close()


def _container(root: Path, discipline: bool) -> Container:
    home = root / "empty-home"
    return Container(
        root,
        Config(read_discipline=discipline, run_session="full"),
        runner=FakeRunner(),
        home=home,
    )


@pytest.mark.parametrize("discipline", [False, True])
def test_native_gsap_launch_composes_guards_owned_settings_and_isolated_hooks(
    tmp_path: Path, discipline: bool
) -> None:
    paths = (
        "src/app/globals.css",
        "src/lib/gsap.ts",
        "src/components/hero-nutfall-3d/view.tsx",
    )
    plan = _plan(
        MandateRequest(
            "bug",
            "Fix GSAP reveal fallback",
            "Leave hidden content visible",
            "src/app/globals.css src/lib/gsap.ts",
            out_of_scope="Do not touch hero-nutfall-3d/**",
        ),
        paths,
    )
    container = _container(tmp_path, discipline)
    try:
        engine = ClaudeCodeEngine(container.runner)
        launch = container.launcher(engine, container.ledger(), telemetry=False)
        request = launch.request(
            LaunchSpec(
                "mandate",
                "GSAP fallback",
                str(tmp_path),
                ("Read", "Edit", "Write", "Bash", "PowerShell", "Agent"),
                change_plan=plan,
                agents_file="agents.json",
                session="full",
            ),
            "RUN",
            "trace",
            None,
        )
        command = engine.command(request)
        tools = command[command.index("--tools") + 1].split(",")
        assert request.strict_guard and request.setting_sources == ()
        assert command[command.index("--setting-sources") + 1] == ""
        assert not set(EXECUTION).intersection(tools)
        assert "Skill" not in tools and "Agent" in tools and "Task" in tools
        assert set(WRITERS).issubset(tools)
        denied = set(command[command.index("--disallowedTools") + 1].split(","))
        for writer in WRITERS:
            assert f"{writer}(src/components/hero-nutfall-3d/**)" in denied
        assert set(EXECUTION).issubset(denied)
        settings = json.loads(Path(request.settings_file).read_text(encoding="utf-8"))
        required = {
            *(f"{writer}(src/components/hero-nutfall-3d/**)" for writer in WRITERS),
            *EXECUTION,
        }
        assert required.issubset(settings["permissions"]["deny"])
        assert settings["disableAllHooks"] is not discipline
        if discipline:
            assert set(settings["hooks"]) == {"PreToolUse", "PostToolUse"}
            commands = [
                settings["hooks"][event][0]["hooks"][0]["command"] for event in settings["hooks"]
            ]
            assert all("cuanta.cli.hooks" in command for command in commands)
            assert all("\\" not in command for command in commands)
            assert all(
                shlex.split(command)[0] == Path(sys.executable).as_posix() for command in commands
            )
        else:
            assert "hooks" not in settings
        assert all(value is False for value in settings["enabledPlugins"].values())
        assert json.loads(Path(request.mcp_config).read_text(encoding="utf-8")) == {
            "mcpServers": {}
        }
        assert not cast(FakeRunner, container.runner).calls
    finally:
        container.close()


def test_native_readonly_investigator_has_no_writers_or_execution_tools(tmp_path: Path) -> None:
    container = _container(tmp_path, False)
    try:
        engine = ClaudeCodeEngine(container.runner)
        request = container.launcher(engine, container.ledger(), telemetry=False).request(
            LaunchSpec(
                "mandate",
                "Audit GSAP",
                str(tmp_path),
                ("Read", "Edit", "Bash", "Agent"),
                change_plan=ChangePlan(read_only=True),
            ),
            "RUN",
            "trace",
            None,
        )
        assert request.read_only and request.strict_guard
        assert not {*WRITERS, *EXECUTION}.intersection(request.allowed_tools)
        assert {*WRITERS, *EXECUTION}.issubset(request.disallowed_tools)
        assert request.setting_sources == ()
        assert (
            engine.command(request)[engine.command(request).index("--tools") + 1]
            == "Read,Grep,Glob,Agent,Task"
        )
    finally:
        container.close()


@pytest.mark.parametrize("readonly", [False, True])
def test_codex_guard_is_labelled_best_effort_and_preserves_os_readonly(
    tmp_path: Path, readonly: bool
) -> None:
    container = _container(tmp_path, True)
    try:
        engine = CodexEngine(container.runner)
        plan = ChangePlan(guard=("renderer/**",), read_only=readonly)
        request = container.launcher(engine, container.ledger(), telemetry=False).request(
            LaunchSpec(
                "mandate",
                "Inspect cart",
                str(tmp_path),
                ("Read", "Bash"),
                read_only=readonly,
                change_plan=plan,
            ),
            "RUN",
            "trace",
            None,
        )
        command = engine.command(request)
        assert command[command.index("--sandbox") + 1] == (
            "read-only" if readonly else "workspace-write"
        )
        assert not request.strict_guard and request.setting_sources is None
        assert not request.settings_file and not request.mcp_config
        assert plan_metrics(plan, (), "codex")["enforcement"] == "best-effort"
        assert not cast(FakeRunner, container.runner).calls
    finally:
        container.close()
