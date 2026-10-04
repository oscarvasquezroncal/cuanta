from __future__ import annotations

from pathlib import Path

from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.doctor import DoctorReport, gateway_check, result, suggestions_check
from cuanta.application.home import next_step
from cuanta.domain.messages import msg
from cuanta.domain.progress import Status
from cuanta.tui.i18n import Catalog
from tests.tui.fakes import DETECTION

GATEWAY = (
    "Run the suite with `cuanta test --json`; its output is bounded, never pipe it. "
    "Read failure detail with `cuanta cat <capsule> --level L2`.\n"
)
TESTER = ".claude/agents/tester.md"
TEMPLATE = "docs/MANDATE_TEMPLATE.md"
TELEMETRY = result(
    "telemetry claude", Status.WARN, msg("wiring.none"), "cuanta telemetry on --engine claude"
)


def write(root: Path, files: dict[str, str]) -> LocalWorkspace:
    workspace = LocalWorkspace(root)
    for relative, text in files.items():
        workspace.write_text(relative, text)
    return workspace


def test_a_tester_without_the_gateway_is_a_doctor_warning_that_names_its_fix(
    tmp_path: Path,
) -> None:
    workspace = write(
        tmp_path,
        {".cuanta/.gitignore": "*\n", TEMPLATE: GATEWAY, TESTER: "Run `pytest -q | tail`.\n"},
    )
    rows = gateway_check(workspace)(DETECTION)
    assert [(row.name, row.status, row.fix) for row in rows] == [("gateway", Status.WARN, "")]
    assert rows[0].detail.startswith(f"{TESTER} lacks the gateway instructions")
    assert "fix: add cuanta test --json, cuanta cat <capsule> --level L2" in rows[0].detail
    assert "arreglo:" in Catalog("es").message(rows[0].message)
    report = DoctorReport(DETECTION, (*rows, TELEMETRY))
    assert next_step(report) == TELEMETRY
    assert next_step(DoctorReport(DETECTION, tuple(rows))) is None


def test_the_gateway_row_is_ok_when_both_files_route_tests_through_cuanta(tmp_path: Path) -> None:
    workspace = write(tmp_path, {".cuanta/.gitignore": "*\n", TEMPLATE: GATEWAY, TESTER: GATEWAY})
    rows = gateway_check(workspace)(DETECTION)
    assert [(row.name, row.status) for row in rows] == [("gateway", Status.OK)]


def test_the_gateway_row_waits_for_cuanta_and_for_the_files(tmp_path: Path) -> None:
    assert gateway_check(write(tmp_path / "bare", {TESTER: "pytest\n"}))(DETECTION) == []
    empty = write(tmp_path / "empty", {".cuanta/.gitignore": "*\n"})
    assert gateway_check(empty)(DETECTION) == []


def test_files_an_older_init_left_beside_your_agents_are_a_warning(tmp_path: Path) -> None:
    workspace = write(
        tmp_path,
        {
            TESTER: "mine\n",
            ".claude/agents/tester.new.md": "forge\n",
            ".claude/commands/init-agents.md": "mine\n",
            ".claude/commands/init-agents.new.md": "forge\n",
            ".claude/agents/helper.new.md": "only copy\n",
            "docs/notes.new.md": "my notes\n",
        },
    )
    rows = suggestions_check(workspace)(DETECTION)
    assert [(row.name, row.status, row.fix) for row in rows] == [("suggestions", Status.WARN, "")]
    assert rows[0].detail == (
        "2 files an older init left beside yours in .claude/: "
        ".claude/agents/tester.new.md, .claude/commands/init-agents.new.md; "
        "cuanta init moves them to .cuanta/forge-suggested/"
    )
    assert "cuanta init" in Catalog("es").message(rows[0].message)
    assert workspace.exists(".claude/agents/tester.new.md")


def test_suggestions_waiting_under_cuanta_are_only_reported(tmp_path: Path) -> None:
    workspace = write(
        tmp_path,
        {
            TESTER: "mine\n",
            ".cuanta/forge-suggested/claude/agents/tester.md": "forge\n",
            ".claude/agents/docs-updater.md": "same\n",
            ".cuanta/forge-suggested/claude/agents/docs-updater.md": "same\n",
        },
    )
    rows = suggestions_check(workspace)(DETECTION)
    assert [(row.name, row.status, row.fix) for row in rows] == [("suggestions", Status.INFO, "")]
    assert rows[0].detail == (
        "1 Forge suggestion waits in .cuanta/forge-suggested/; "
        "compare it in the app's Init view or copy it over your file"
    )
    assert next_step(DoctorReport(DETECTION, tuple(rows))) is None
    assert workspace.exists(".cuanta/forge-suggested/claude/agents/docs-updater.md")


def test_a_clean_tree_has_no_suggestions_row(tmp_path: Path) -> None:
    assert suggestions_check(write(tmp_path, {TESTER: "mine\n"}))(DETECTION) == []
