from __future__ import annotations

import pytest

from cuanta.domain.detection import (
    DocsState,
    ForgeState,
    GraphMode,
    SizeTier,
    VerifySignals,
    VerifyTier,
    docs_state,
    forge_state,
    is_excluded_dir,
    is_source_file,
    size_tier,
    verify_tier,
)
from cuanta.domain.forge_state import (
    CorruptState,
    parse_state,
    run_note,
    state_to_dict,
)
from cuanta.domain.graph_policy import (
    GraphBranch,
    graph_outcome_line,
    matches_graph_server,
    plan_graph,
)
from cuanta.domain.ids import is_run_id, make_run_id, run_id_timestamp_ms, trace_id_of, traceparent
from cuanta.domain.ignore import missing_entries, with_entries


@pytest.mark.parametrize(
    ("count", "tier"),
    [
        (0, SizeTier.SMALL),
        (99, SizeTier.SMALL),
        (100, SizeTier.MEDIUM),
        (499, SizeTier.MEDIUM),
        (500, SizeTier.LARGE),
        (1999, SizeTier.LARGE),
        (2000, SizeTier.XL),
    ],
)
def test_size_tier_boundaries(count: int, tier: SizeTier) -> None:
    assert size_tier(count) is tier


def test_docs_state() -> None:
    assert docs_state(False, 3) is DocsState.ABSENT
    assert docs_state(True, 0) is DocsState.PARTIAL
    assert docs_state(True, 1) is DocsState.EXISTS


def test_forge_state_signals() -> None:
    assert forge_state((), False, False, False) is ForgeState.FRESH
    assert forge_state(("backend-senior.md",), False, False, False) is ForgeState.INITIALIZED
    assert forge_state(("tester.md",), False, False, False) is ForgeState.INITIALIZED
    assert forge_state(("custom.md",), False, False, False) is ForgeState.FRESH
    assert forge_state((), True, False, False) is ForgeState.INITIALIZED
    assert forge_state((), False, True, False) is ForgeState.INITIALIZED
    assert forge_state((), False, False, True) is ForgeState.INITIALIZED


def test_verify_tier_strong_needs_runner_and_check() -> None:
    decision = verify_tier(VerifySignals(test_runners=("pytest",), typecheckers=("mypy",)))
    assert decision.tier is VerifyTier.STRONG
    assert decision.evidence == "pytest + mypy"
    built = verify_tier(VerifySignals(test_runners=("jest",), builders=("npm run build",)))
    assert built.tier is VerifyTier.STRONG


def test_verify_tier_moderate_and_weak() -> None:
    assert verify_tier(VerifySignals(typecheckers=("tsc",))).tier is VerifyTier.MODERATE
    assert verify_tier(VerifySignals(linters=("eslint",))).tier is VerifyTier.MODERATE
    assert verify_tier(VerifySignals(test_runners=("pytest",))).tier is VerifyTier.STRONG
    assert verify_tier(VerifySignals(builders=("npm run build",))).tier is VerifyTier.WEAK
    assert verify_tier(VerifySignals()).tier is VerifyTier.WEAK


def test_runner_without_typecheck_is_strong_and_says_so() -> None:
    decision = verify_tier(VerifySignals(test_runners=("pytest",)))
    assert decision.tier is VerifyTier.STRONG
    assert decision.evidence == "pytest, no typecheck"
    linted = verify_tier(VerifySignals(test_runners=("jest",), linters=("eslint",)))
    assert linted.evidence == "jest + eslint, no typecheck"
    checked = verify_tier(VerifySignals(test_runners=("pytest",), typecheckers=("mypy",)))
    assert "no typecheck" not in checked.evidence


def test_placeholder_test_script_is_not_a_runner() -> None:
    signals = VerifySignals(builders=("npm run build",), placeholder_tests=('"test": "no tests"',))
    decision = verify_tier(signals)
    assert decision.tier is VerifyTier.WEAK


def test_exclusions() -> None:
    assert is_excluded_dir("node_modules")
    assert is_excluded_dir("shop.egg-info")
    assert is_excluded_dir("fixtures", frozenset({"fixtures"}))
    assert not is_excluded_dir("src")
    assert is_source_file("app.py")
    assert not is_source_file("bundle.min.js")
    assert not is_source_file("poetry.lock")
    assert not is_source_file("go.sum")
    assert not is_source_file("README.md")


def test_graph_plan_size_gate_outranks_mode() -> None:
    plan = plan_graph(SizeTier.SMALL, GraphMode.CLI, "graphify on PATH")
    assert plan.branch is GraphBranch.SKIP
    assert plan.reason == "skipped (small tier; graphify on PATH)"
    assert plan_graph(SizeTier.MEDIUM, GraphMode.MCP, "x").branch is GraphBranch.DEFER
    assert plan_graph(SizeTier.LARGE, GraphMode.CLI, "x").branch is GraphBranch.UPDATE
    assert plan_graph(SizeTier.XL, GraphMode.NONE, "").branch is GraphBranch.INSTALL


def test_graph_outcome_lines() -> None:
    install = plan_graph(SizeTier.MEDIUM, GraphMode.NONE, "")
    assert graph_outcome_line(install, True) == "installed"
    assert graph_outcome_line(install, False, "boom") == "FAILED — boom"


def test_mcp_server_matching() -> None:
    assert matches_graph_server(["graphify-mcp"]) == "graphify-mcp"
    assert matches_graph_server(["node", "/opt/code-graph-server/index.js"]) is not None
    assert matches_graph_server(["repo-graph-server"]) == "repo-graph-server"
    assert matches_graph_server(["github", "npx"]) is None


def test_forge_state_roundtrip_keeps_unknown_keys() -> None:
    data = {
        "version": "0.3",
        "started_at": "2026-01-01T00:00:00Z",
        "size_tier": "small",
        "graph_mode": "none",
        "docs_state": "absent",
        "forge_state": "fresh",
        "verify_tier": "strong",
        "phases_completed": ["0", "0.5"],
        "dirs_written": [],
        "dirs_queued": [],
        "dirs_rejected": [{"path": "x", "reason": "y"}],
        "unverified": [],
        "verify_markers": [],
        "producer": "cuanta",
        "future": 1,
    }
    state = parse_state(data)
    assert state.cuanta_handoff
    assert state.next_phase == "1"
    assert state_to_dict(state) == data


def test_corrupt_forge_state() -> None:
    with pytest.raises(CorruptState):
        parse_state([])
    with pytest.raises(CorruptState):
        parse_state({"phases_completed": ["7"]})
    with pytest.raises(CorruptState):
        parse_state({"phases_completed": "0"})


def test_run_note() -> None:
    assert run_note(None, None) == "fresh run"
    assert run_note(None, "invalid JSON") == "state file discarded: invalid JSON"
    state = parse_state({"phases_completed": ["0", "0.5", "1"]})
    assert run_note(state, None) == "resuming from Phase 2A"


def test_ignore_entries() -> None:
    assert (
        missing_entries("node_modules/\n/.claude/forge-state.json\n", [".claude/forge-state.json"])
        == ()
    )
    assert with_entries("a", ["b"]) == "a\nb\n"
    assert with_entries("b\n", ["b"]) is None
    assert with_entries("", ["x/"]) == "x/\n"


def test_run_ids_sort_by_time() -> None:
    first = make_run_id(1_000, bytes(10))
    second = make_run_id(2_000, bytes(10))
    assert first < second
    assert is_run_id(first)
    assert run_id_timestamp_ms(second) == 2_000
    with pytest.raises(ValueError, match="entropy"):
        make_run_id(1, b"x")


def test_traceparent_format() -> None:
    value = traceparent(bytes(range(16)), bytes(range(8)))
    assert value == "00-000102030405060708090a0b0c0d0e0f-0001020304050607-01"
    assert trace_id_of(value) == "000102030405060708090a0b0c0d0e0f"
    assert trace_id_of("bad") == ""


def test_python_build_command_uses_the_manager_native_build() -> None:
    from cuanta.domain.manifests import python_build_command

    assert python_build_command("uv") == "uv build"
    assert python_build_command("poetry") == "poetry build"
    assert python_build_command("pdm") == "pdm build"
    assert python_build_command("pip") == "python -m build"
    assert python_build_command("pipenv") == "python -m build"
