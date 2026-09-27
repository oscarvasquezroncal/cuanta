from __future__ import annotations

import json
from dataclasses import asdict, replace

import pytest

from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.read_efficiency import (
    CODE_FORMULA,
    INVESTIGATION_FORMULA,
    ReadEfficiency,
    read_efficiency,
)


def read(
    path: str,
    *,
    tool: str = "Read",
    kind: str = "tool_result",
    success: bool | None = True,
    size: int = 100,
    raw: str = "",
    identity: str = "",
) -> LedgerEvent:
    return LedgerEvent(
        run_id="run",
        source="claude_code",
        session_id="session",
        tool_name=tool,
        kind=kind,
        success=success,
        tool_result_bytes=size,
        file_path=path,
        raw=raw,
        tool_use_id=identity,
    )


def test_empty_report_and_events_have_unavailable_default() -> None:
    assert read_efficiency([], "") == ReadEfficiency()
    assert ReadEfficiency().read_count == 0
    assert ReadEfficiency().useful_count == 0


def test_most_specific_nested_copy_alias_wins_over_its_original_root() -> None:
    report = read_efficiency(
        [read("C:/repo/sandbox/copy/src/a.py"), read("C:/repo/src/a.py")],
        "src/a.py:8",
        task_type="investigation",
        project_roots=("C:/repo", "C:/repo/sandbox/copy"),
    )
    assert report.read_files == report.useful_files == ("src/a.py",)
    assert report.value == 1


def test_investigation_counts_unique_cited_files_actually_read() -> None:
    events = [read("src/a.py"), read("src/a.py"), read("src/b.py"), read("src/c.py")]
    report = "src/a.py:2 and src/a.py:3-10, src/b.py:100:4, src/unread.py:2"
    result = read_efficiency(events, report, task_type="investigation")
    assert result.read_files == ("src/a.py", "src/b.py", "src/c.py")
    assert result.cited_files == ("src/a.py", "src/b.py", "src/unread.py")
    assert result.useful_files == ("src/a.py", "src/b.py")
    assert result.value == 2 / 3
    assert result.formula == INVESTIGATION_FORMULA
    assert result.available
    assert result.why == ""


def test_code_counts_read_edited_or_cited_files_without_double_counting() -> None:
    result = read_efficiency(
        [read("src/a.py"), read("src/b.py"), read("src/c.py")],
        "src/a.py:1 src/b.py:2 src/unread.py:9",
        ["src/a.py", "src/c.py", "src/unread.py"],
        task_type="fix",
    )
    assert result.value == 1
    assert result.useful_count == 3
    assert result.read_count == 3
    assert result.formula == CODE_FORMULA
    assert result.edited_files == ("src/a.py", "src/c.py", "src/unread.py")


def test_edits_do_not_make_an_investigation_read_useful() -> None:
    result = read_efficiency([read("src/a.py")], "", ["src/a.py"], "investigation")
    assert result.value == 0
    assert result.useful_files == ()


def test_missing_report_and_observed_empty_report_remain_distinct() -> None:
    events = [read("src/a.py")]
    assert read_efficiency(events, None, task_type="investigation").value is None
    assert read_efficiency(events, None).why == "missing_report"
    assert read_efficiency(events, "", task_type="investigation").value == 0
    assert read_efficiency(events, "").available


def test_code_with_missing_report_can_use_observed_changed_files() -> None:
    result = read_efficiency([read("src/a.py"), read("src/b.py")], None, ["src/a.py"])
    assert result.value == 0.5
    assert result.cited_files == ()
    assert result.available


@pytest.mark.parametrize("tool", ["Read", "read", "View", "view", "NotebookRead"])
def test_successful_source_read_tools_count(tool: str) -> None:
    assert read_efficiency([read("src/a.py", tool=tool)], "src/a.py:1").value == 1


@pytest.mark.parametrize("tool", ["Glob", "Grep", "Edit", "Bash", "mcp__other__page"])
def test_search_edit_and_other_server_tools_do_not_count_as_source_reads(tool: str) -> None:
    assert read_efficiency([read("src/a.py", tool=tool)], "src/a.py:1").read_count == 0


@pytest.mark.parametrize("tool", ["find", "card", "impact", "facts", "tests_for", "note"])
def test_owned_index_metadata_tools_are_not_source_pages(tool: str) -> None:
    event = replace(read("src/a.py", tool=tool), source="cuanta_mcp", kind="index_call")
    assert read_efficiency([event], "src/a.py:1").read_count == 0


@pytest.mark.parametrize("tool", ["page", "cuanta.page", "mcp__cuanta__page"])
def test_owned_source_pages_count_and_deduplicate_mirrored_native_page(tool: str) -> None:
    owned = replace(read("src/a.py", tool=tool), source="cuanta_mcp", kind="index_call")
    native = read("src/a.py", tool="mcp__cuanta__page")
    result = read_efficiency([owned, native, owned], "src/a.py:1")
    assert result.read_files == ("src/a.py",)
    assert result.value == 1


def test_native_mcp_page_uses_otlp_json_parameters_when_normalized_path_is_missing() -> None:
    parameters = {"mcp_server_name": "cuanta", "mcp_tool_name": "page", "path": "src/a.py"}
    raw = json.dumps(
        {
            "attributes": [
                {"key": "tool_parameters", "value": {"stringValue": json.dumps(parameters)}}
            ]
        }
    )
    event = read("", tool="mcp_tool", kind="mcp_tool_call", raw=raw)
    assert read_efficiency([event], "src/a.py:2").value == 1


@pytest.mark.parametrize("key", ["tool_input", "tool.parameters", "arguments"])
def test_nested_parameter_path_fallback_preserves_native_read_path(key: str) -> None:
    event = read("", raw=json.dumps({"attributes": {key: json.dumps({"file_path": "src/a.py"})}}))
    assert read_efficiency([event], "src/a.py:1").read_files == ("src/a.py",)


def test_normalized_path_wins_over_conflicting_raw_parameters() -> None:
    event = read("src/a.py", raw='{"cuanta.parameters":{"file_path":"src/b.py"}}')
    assert read_efficiency([event], "src/a.py:1").read_files == ("src/a.py",)


@pytest.mark.parametrize("status", ["failed", "error", "denied", "running", "pending"])
def test_failed_denied_and_pending_reads_do_not_count_despite_response_bytes(status: str) -> None:
    event = read("src/a.py", raw=json.dumps({"status": status}))
    assert read_efficiency([event], "src/a.py:1").read_count == 0


def test_unknown_and_failed_reads_do_not_turn_empty_responses_into_success() -> None:
    events = [
        read("src/a.py", success=False),
        read("src/b.py", success=None, size=0),
        read("", success=True),
        read("src/c.py", raw='{"is_error":true}'),
    ]
    assert read_efficiency(events, "").read_count == 0
    assert read_efficiency([read("src/empty.py", size=0)], "").read_count == 1


@pytest.mark.parametrize("key", ["is_error", "isError"])
def test_explicit_native_error_flags_reject_returned_source_bytes(key: str) -> None:
    event = read("src/a.py", raw=json.dumps({key: True}))
    assert read_efficiency([event], "src/a.py:1").read_files == ()


def test_tool_use_fallback_requires_usable_bytes_or_explicit_success() -> None:
    events = [
        read("src/unknown.py", kind="tool_use", success=None, size=0),
        read("src/bytes.py", kind="tool_use", success=None),
        read("src/success.py", kind="tool_use", size=0),
        read("src/decision.py", kind="tool_decision"),
    ]
    assert read_efficiency(events, "").read_files == ("src/bytes.py", "src/success.py")


def test_failed_completed_call_overrides_tool_use_fallback_in_the_same_session() -> None:
    started = read("src/a.py", kind="tool_use", identity="call")
    failed = replace(started, kind="tool_result", success=False)
    other_session = replace(started, file_path="src/b.py", session_id="other")
    result = read_efficiency([started, failed, other_session], "")
    assert result.read_files == ("src/b.py",)


def test_missing_or_malformed_metadata_is_safe() -> None:
    events = [read("", raw="{broken"), read("", raw="[]"), read("", raw='{"path":12}')]
    assert read_efficiency(events, "").read_files == ()


def test_windows_source_and_deleted_sandbox_aliases_canonicalize_to_one_relative_path() -> None:
    events = [
        read("C:\\work\\project\\Src\\a.py"),
        read("D:\\temp\\trial-copy\\src\\A.py"),
        read("C:\\work\\project-extra\\src\\a.py"),
        read("D:\\temp\\other-copy\\src\\a.py"),
    ]
    result = read_efficiency(
        events,
        "C:/WORK/PROJECT/src/a.py:3 D:/temp/trial-copy/src/a.py:4",
        project_root="C:/work/project",
        project_roots=("D:/temp/trial-copy",),
    )
    assert result.read_files == ("src/a.py",)
    assert result.cited_files == ("src/a.py",)
    assert result.value == 1


def test_posix_root_matching_remains_case_sensitive_and_rejects_external_paths() -> None:
    result = read_efficiency(
        [read("/repo/Src/a.py"), read("/Repo/src/a.py"), read("/external/Src/a.py")],
        "Src/a.py:3 src/a.py:4 /external/Src/a.py:5",
        project_root="/repo",
    )
    assert result.read_files == ("Src/a.py",)
    assert result.cited_files == ("Src/a.py", "src/a.py")
    assert result.value == 1


def test_unc_source_paths_are_contained_by_share_components() -> None:
    result = read_efficiency(
        [read("\\\\SERVER\\Share\\Project\\src\\a.py"), read("//server/share/other/src/a.py")],
        "src/a.py:1",
        project_root="//server/share/project",
    )
    assert result.read_files == ("src/a.py",)


def test_relative_path_normalization_resolves_only_contained_parent_segments() -> None:
    events = [read("./src/../src/a.py"), read("../src/a.py"), read("src/../../src/a.py")]
    result = read_efficiency(events, "./src/a.py:2 ../src/a.py:3")
    assert result.read_files == ("src/a.py",)
    assert result.cited_files == ("src/a.py",)


def test_absolute_parent_escape_and_windows_drive_relative_paths_are_rejected() -> None:
    events = [
        read("C:/project/../other/src/a.py"),
        read("C:src/a.py"),
        read("https://host/src/a.py"),
        read("src/\x00a.py"),
    ]
    assert read_efficiency(events, "", project_root="C:/project").read_files == ()


def test_no_root_absolute_reads_remain_distinct_without_basename_matching() -> None:
    events = [read("C:/one/src/a.py"), read("C:/two/src/a.py"), read("src/a.py")]
    result = read_efficiency(events, "C:\\one\\src\\a.py:2 src/a.py:3")
    assert result.read_count == 3
    assert result.useful_files == ("src/a.py",)
    assert result.value == 1 / 3


def test_equal_basenames_in_unrelated_relative_directories_are_not_merged() -> None:
    result = read_efficiency([read("one/a.py"), read("two/a.py")], "one/a.py:2")
    assert result.value == 0.5
    assert result.useful_files == ("one/a.py",)


def test_actual_role_and_owned_page_events_share_unique_file_denominator() -> None:
    analyst = replace(read("src/a.py"), agent="architecture-analyst")
    implementer = replace(read("src/a.py"), agent="implementer", run_id="child")
    page = replace(read("src/b.py", tool="page"), source="cuanta_mcp", kind="index_call")
    result = read_efficiency([analyst, implementer, page], "src/a.py:1 src/b.py:2")
    assert result.value == 1
    assert result.read_count == 2


def test_json_export_contains_only_derived_safe_paths_and_the_explicit_formula() -> None:
    event = read("C:/project/src/a.py", raw='{"prompt":"private text"}')
    result = read_efficiency([event], "src/a.py:2", project_root="C:/project")
    serialized = json.dumps(asdict(result))
    assert "private text" not in serialized
    assert "C:/" not in serialized
    assert asdict(result)["formula"] == CODE_FORMULA
    assert asdict(result)["read_files"] == ("src/a.py",)
