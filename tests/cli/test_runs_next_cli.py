from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.run_reports import RunReports
from cuanta.cli.commands.mandate import _final
from cuanta.domain.mandate_file import parse_mandate_file
from cuanta.tui.i18n import Catalog
from tests.cli.test_runs_defaults import mandate, seed
from tests.support import invoke
from tests.tui.fakes import mandate_report
from tests.unit.test_next_step import AUDIT_NEXT


def run_next(root: Path, *args: str) -> tuple[int, str]:
    result = invoke(["runs", "next", *args, "--project", str(root)])
    return result.exit_code, " ".join((result.stdout + result.stderr).split())


def test_runs_next_writes_the_next_mandate_of_the_last_run(tmp_path: Path) -> None:
    seed(tmp_path, mandate("01JA0AUDIT"))
    RunReports(LocalWorkspace(tmp_path)).save_report("01JA0AUDIT", AUDIT_NEXT)
    code, shown = run_next(tmp_path, "--lang", "en")
    assert code == 0, shown
    assert "cuanta run .cuanta/runs/01JA0AUDIT/next.md" in shown
    assert "Investigate" in shown and "app/routers/gateway.py (135-end)" in shown
    written = (tmp_path / ".cuanta/runs/01JA0AUDIT/next.md").read_text(encoding="utf-8")
    parsed = parse_mandate_file(written).request
    assert parsed.type == "investigation" and parsed.where.startswith("app/routers/gateway.py")
    result = invoke(["runs", "next", "01JA0", "--json", "--project", str(tmp_path)])
    data = json.loads(result.stdout)
    assert data["path"] == ".cuanta/runs/01JA0AUDIT/next.md"
    assert data["command"] == "cuanta run .cuanta/runs/01JA0AUDIT/next.md"
    assert data["request"]["type"] == "investigation"
    spanish = run_next(tmp_path, "--lang", "es")[1]
    assert "Ejecútalo: cuanta run .cuanta/runs/01JA0AUDIT/next.md" in spanish


def test_an_unknown_run_or_a_report_without_next_step_is_one_clear_line(tmp_path: Path) -> None:
    seed(tmp_path, mandate("01JA0PLAIN"))
    RunReports(LocalWorkspace(tmp_path)).save_report("01JA0PLAIN", "## SUMMARY\nDone.\n")
    code, shown = run_next(tmp_path, "--lang", "en")
    assert code != 0 and "run 01JA0PLAIN has no NEXT STEP in its report" in shown
    assert "Traceback" not in shown
    code, shown = run_next(tmp_path, "NOPE", "--lang", "es")
    assert code != 0 and "ninguna corrida coincide con NOPE" in shown


def test_an_audit_with_a_next_step_ends_with_the_command() -> None:
    report = replace(mandate_report(), task_type="investigation", text=AUDIT_NEXT)
    for language, line in (
        ("en", "Next step: cuanta runs next"),
        ("es", "Siguiente paso: cuanta runs next"),
    ):
        shown = repr(_final(report, catalog=Catalog(language)).blocks)
        assert line in shown
    plain = replace(report, text="## SUMMARY\nDone.\n")
    assert "runs next" not in repr(_final(plain, catalog=Catalog("en")).blocks)
