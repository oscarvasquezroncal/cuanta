from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from cuanta.adapters.system.process_runner import SubprocessRunner
from cuanta.bootstrap import Container
from tests.fakes import FakeRunner, copy_repo
from tests.support import FIXTURES, invoke

FAKE = FIXTURES / "fake_claude.py"
REPORT = "## SUMMARY\n" + "".join(
    f"- finding {index} at src/calc/__init__.py:2\n" for index in range(400)
)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, fake_runner: FakeRunner, tmp_path: Path) -> dict[str, str]:
    original = Container.for_project

    def build(cls: type[Container], project: Path) -> Container:
        container = original(project)
        container.runner = SubprocessRunner()
        return container

    monkeypatch.setattr(Container, "for_project", classmethod(build))
    report = tmp_path / "report.md"
    report.write_text(REPORT, encoding="utf-8")
    values = {
        "CUANTA_CLAUDE_BIN": f'"{sys.executable}" "{FAKE}"',
        "CUANTA_PORT": "47430",
        "FAKE_CLAUDE_REPORT": str(report),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def test_a_fresh_project_stops_the_pipeline_and_simple_mode_keeps_the_report(
    tmp_path: Path, env: dict[str, str]
) -> None:
    root = copy_repo("bugfix", tmp_path)
    request = [
        "mandate",
        "--type",
        "investigation",
        "--what",
        "revisa la landing",
        "--why",
        "no se",
        "--out-of-scope",
        "nada",
        "--json",
        "--project",
        str(root),
    ]
    stopped = invoke(request, env=env)
    assert stopped.exit_code != 0
    error = json.loads(stopped.stdout)["error"]
    assert "no Forge agents" in error["message"]
    assert "--simple" in error["hint"]
    ran = invoke([*request, "--simple"], env=env)
    assert ran.exit_code == 0, ran.stdout + ran.stderr
    run_id = json.loads(ran.stdout)["run_id"]
    folder = root / ".cuanta" / "runs" / run_id
    assert (folder / "report.md").read_text(encoding="utf-8").rstrip("\n") == REPORT.rstrip("\n")
    meta = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    assert meta["simple"] is True
    assert meta["shape"] == "single"
    assert meta["task_type"] == "investigation"


def test_runs_list_show_and_markdown_read_the_stored_report(
    tmp_path: Path, env: dict[str, str]
) -> None:
    root = copy_repo("bugfix", tmp_path)
    ran = invoke(
        [
            "mandate",
            "--type",
            "investigation",
            "--what",
            "how does add work",
            "--why",
            "is it the sum of both numbers?",
            "--out-of-scope",
            "nada",
            "--simple",
            "--plain",
            "--project",
            str(root),
        ],
        env=env,
    )
    assert ran.exit_code == 0, ran.stdout + ran.stderr
    assert "finding 399 at src/calc/__init__.py:2" in ran.stdout
    assert "report saved: .cuanta/runs/" in ran.stdout
    listed = json.loads(invoke(["runs", "list", "--json", "--project", str(root)], env=env).stdout)
    run_id = listed["runs"][0]["id"]
    assert listed["runs"][0]["report"] is True
    shown = json.loads(
        invoke(["runs", "show", run_id[:12], "--json", "--project", str(root)], env=env).stdout
    )
    assert shown["run_id"] == run_id
    assert shown["simple"] is True
    assert shown["shape"] == "single"
    assert shown["report"].rstrip("\n") == REPORT.rstrip("\n")
    assert shown["sections"] == []
    plain = invoke(["runs", "show", run_id, "--plain", "--project", str(root)], env=env)
    assert "simple (one agent, no project knowledge)" in plain.stdout
    assert "finding 0 at src/calc/__init__.py:2" in plain.stdout
    human = invoke(
        ["runs", "show", run_id, "--markdown", "--plain", "--project", str(root)], env=env
    )
    assert human.stdout.startswith(f"# Run {run_id}")
    assert "## Report" in human.stdout
    meta_path = root / ".cuanta" / "runs" / run_id / "run.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.pop("shape")
    meta.pop("simple")
    meta["handoffs"] = []
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    legacy = invoke(["runs", "show", run_id, "--json", "--project", str(root)], env=env)
    assert json.loads(legacy.stdout)["shape"] == "unknown"
    legacy_plain = invoke(["runs", "show", run_id, "--plain", "--project", str(root)], env=env)
    assert "unknown shape" in legacy_plain.stdout
    missing = invoke(["runs", "show", "ZZZ", "--json", "--project", str(root)], env=env)
    assert missing.exit_code == 1
    assert "no run matches ZZZ" in json.loads(missing.stdout)["error"]["message"]


def test_runs_show_drops_preamble_from_a_stored_report(tmp_path: Path, env: dict[str, str]) -> None:
    root = copy_repo("bugfix", tmp_path)
    ran = invoke(
        [
            "mandate",
            "--type",
            "investigation",
            "--what",
            "review the code",
            "--why",
            "find duplicate work",
            "--out-of-scope",
            "changes",
            "--simple",
            "--json",
            "--project",
            str(root),
        ],
        env=env,
    )
    assert ran.exit_code == 0, ran.stdout + ran.stderr
    run_id = json.loads(ran.stdout)["run_id"]
    raw = "I have enough evidence.\n\n## SUMMARY\nok\n\n## NEXT STEP\n- none\n"
    (root / ".cuanta" / "runs" / run_id / "report.md").write_text(raw, encoding="utf-8")
    shown = invoke(["runs", "show", run_id, "--json", "--project", str(root)], env=env)
    assert shown.exit_code == 0, shown.stdout + shown.stderr
    payload = json.loads(shown.stdout)
    assert payload["report"].startswith("## SUMMARY")
    assert payload["sections"][0] == "summary"


def test_runs_open_launches_the_app_on_that_run(
    tmp_path: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import cuanta.tui.app as tui_app

    root = copy_repo("bugfix", tmp_path)
    ran = invoke(
        [
            "mandate",
            "--type",
            "investigation",
            "--what",
            "how does add work",
            "--why",
            "is it the sum of both numbers?",
            "--out-of-scope",
            "nada",
            "--simple",
            "--json",
            "--project",
            str(root),
        ],
        env=env,
    )
    run_id = json.loads(ran.stdout)["run_id"]
    assert json.loads(ran.stdout)["report_text"].startswith("## SUMMARY")
    opened: list[str] = []

    def fake_run(
        project: Path, language: str, theme: str, motion: bool = True, open_run: str = ""
    ) -> None:
        opened.append(open_run)

    monkeypatch.setattr(tui_app, "run", fake_run)
    result = invoke(
        ["runs", "open", run_id[:10], "--project", str(root)],
        env={**env, "CUANTA_FORCE_TTY": "1"},
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert opened == [run_id]
    refused = invoke(["runs", "open", run_id, "--plain", "--project", str(root)], env=env)
    assert refused.exit_code == 2
