from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from cuanta.cli.commands.route import parse_role_models
from cuanta.domain.models import ModelEntry, Tier
from cuanta.domain.routing import (
    ROLES,
    Role,
    RoutingPolicy,
    default_requests,
    depth_capped,
    plan_route,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from dev import acceptance, focus, gate, results, spec, trial


def definition(project: Path) -> spec.Spec:
    return spec.Spec(
        project,
        1.0,
        (
            spec.Trial(
                "one",
                "feature",
                "add something",
                "why",
                "build",
                "other changes",
                "quick",
                "claude",
                0.4,
                checks=(spec.Check("built.html", "hello"),),
            ),
        ),
    )


def test_junit_counts_nested_suites_and_failures_without_terminal_scraping(tmp_path: Path) -> None:
    xml = tmp_path / "tests.xml"
    xml.write_text(
        '<testsuites><testsuite><testcase classname="tests.test_a" name="ok"/>'
        '<testcase name="skip"><skipped/></testcase><testcase name="broken">'
        '<failure message="assert elapsed &lt; BUDGET_S"/></testcase>'
        '<testcase name="error"><error message="fixture failed&#10;details"/></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    parsed = results.junit(xml)
    assert (parsed.passed, parsed.failed, parsed.skipped) == (1, 2, 1)
    assert not parsed.speed_only
    assert parsed.failures[-1][1] == "fixture failed details"


def test_coverage_ruff_and_mypy_counts(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text('{"totals": {"percent_covered": 91.2}}', encoding="utf-8")
    assert results.coverage(report) == 91.2
    report.write_text('[{"code":"F401"}, {"code":"E501"}]', encoding="utf-8")
    assert results.ruff_count(report) == 2
    report.write_text(
        "a.py:3: error: bad\nb.py: error: duplicate\nFound 2 errors\n", encoding="utf-8"
    )
    assert results.mypy_count(report) == 2


def test_exit_two_only_for_speed_assertions_with_coverage(tmp_path: Path) -> None:
    xml = tmp_path / "tests.xml"
    xml.write_text(
        '<testsuite><testcase><failure message="assert timings[0] &lt; budget"/></testcase></testsuite>',
        encoding="utf-8",
    )
    step = results.Step("performance-1", [], 1, 1, "log", results.junit(xml), 95)
    assert results.exit_code([step]) == 2
    assert results.exit_code([replace(step, coverage=80)]) == 1
    assert results.exit_code([replace(step, code=2)]) == 1
    assert results.exit_code([step, replace(step, name="mypy")]) == 1


def test_junit_speed_detection_uses_the_failed_source_line(tmp_path: Path) -> None:
    xml = tmp_path / "tests.xml"
    xml.write_text(
        '<testsuite><testcase><failure message="assert 1.2 &lt; 0.7">'
        "\n&gt;    assert timings[0] &lt; budget\nE assert 1.2 &lt; 0.7"
        "</failure></testcase></testsuite>",
        encoding="utf-8",
    )
    assert results.junit(xml).speed_only
    xml.write_text(
        '<testsuite><testcase><failure message="assert 0 == 5000">'
        "\n&gt;    assert report.total.runs == RUNS\n    assert elapsed &lt; BUDGET_S"
        "</failure></testcase></testsuite>",
        encoding="utf-8",
    )
    assert not results.junit(xml).speed_only


def test_focus_mapping_imports_relative_imports_deleted_modules_and_changed_tests(
    tmp_path: Path,
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_text("from cuanta.domain import thing\n", encoding="utf-8")
    (tests / "test_b.py").write_text("from . import helper\n", encoding="utf-8")
    (tests / "test_c.py").write_text("import unrelated\n", encoding="utf-8")
    assert focus.related([tmp_path / "src/cuanta/domain/thing.py"], tmp_path) == ["tests/test_a.py"]
    assert focus.related([tests / "helper.py"], tmp_path) == ["tests/test_b.py"]
    assert focus.related([tests / "test_c.py"], tmp_path) == ["tests/test_c.py"]


def test_focus_git_status_handles_renames_and_spaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subprocess,
        "check_output",
        lambda *a, **k: b"R  new name.py\0old name.py\0?? other.py\0",
    )
    assert focus.changed(tmp_path) == [
        tmp_path / "new name.py",
        tmp_path / "old name.py",
        tmp_path / "other.py",
    ]


@pytest.mark.parametrize("value", [0, -1, True, float("inf"), float("nan"), "1"])
def test_spec_caps_are_finite_positive_numbers(value: Any) -> None:
    with pytest.raises(ValueError, match="finite positive"):
        spec.amount(value)


def write_spec(tmp_path: Path, extra: str = "") -> Path:
    file = tmp_path / "trials.toml"
    file.write_text(
        'project = "project"\ntotal_cap_usd = 1.0\n[[trials]]\n'
        'name = "one"\ntype = "feature"\nwhat = "something"\nwhy = "reason"\n'
        'tests = "build"\nout_of_scope = "other"\ndepth = "quick"\nengine = "claude"\n'
        "cap_usd = 0.4\n" + extra,
        encoding="utf-8",
    )
    return file


@pytest.mark.parametrize(
    "extra",
    [
        "cap_usd = 2.0",
        'engine = "opencode"',
        'cross_engine = "yes"',
        "unexpected = 1",
        'checks = [{file="../secret",regex="x"}]',
    ],
)
def test_spec_rejects_invalid_caps_settings_and_paths(tmp_path: Path, extra: str) -> None:
    file = write_spec(tmp_path)
    key = extra.split("=", 1)[0].strip()
    text = file.read_text()
    text = "\n".join(line for line in text.splitlines() if not line.startswith(key + " ="))
    file.write_text(text + "\n" + extra + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"exceed|support|boolean|Unknown|relative"):
        spec.load(file)


def test_gate_smoke_runs_a_fake_command_and_writes_compact_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    report = results.Report("smoke", tmp_path)
    monkeypatch.setattr(gate, "Report", lambda *a: report)
    monkeypatch.setattr(
        gate, "static", lambda r: r.run("fake", [sys.executable, "-c", "print('hello')"]).code == 0
    )
    assert gate.run(static_only=True) == 0
    assert json.loads((report.directory / "summary.json").read_text())["exit_code"] == 0
    assert "hello" not in capsys.readouterr().out
    assert (report.directory / "01-fake.log").read_text().strip() == "hello"


def test_focus_smoke_fake_command_stops_at_first_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = results.Report("smoke", tmp_path)
    monkeypatch.setattr(focus, "Report", lambda *a: report)
    monkeypatch.setattr(focus, "ROOT", tmp_path)
    monkeypatch.setattr(focus, "changed", lambda root: [])
    monkeypatch.setattr(focus, "python", lambda *a: [sys.executable, "-c", "raise SystemExit(1)"])
    assert focus.run([]) == 1
    assert len(report.steps) == 1


def test_trial_dry_run_has_no_subprocess_or_spend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        results.Report, "run", lambda *a, **k: pytest.fail("dry run spawned a process")
    )
    assert trial.run(spec.load(write_spec(tmp_path)), True, None, False) == 0
    output = capsys.readouterr().out
    assert "--sandbox" in output and "spend $0.00" in output


@pytest.mark.parametrize(
    ("model", "tier"),
    [("claude-sonnet-5", Tier.STANDARD), ("claude-opus-5-5", Tier.PREMIUM)],
)
def test_native_trial_role_pins_resolve_to_the_requested_models(
    tmp_path: Path, model: str, tier: Tier
) -> None:
    matrix = definition(tmp_path)
    chosen = replace(
        matrix.trials[0],
        depth="normal",
        role_models=tuple(f"{role.value}=claude:{model}" for role in ROLES),
    )
    command = trial.command(matrix, chosen)
    values = [command[index + 1] for index, value in enumerate(command) if value == "--role-model"]
    assert values == [f"{role.value}={model}" for role in ROLES]
    fallback = Tier.PREMIUM if tier is Tier.STANDARD else Tier.STANDARD
    policy = depth_capped(
        RoutingPolicy(
            roles=dict.fromkeys(ROLES, fallback),
            role_models={Role(role): value for role, value in parse_role_models(values).items()},
        ),
        Tier.PREMIUM,
    )
    models = (
        ModelEntry(
            "claude",
            "sonnet",
            "Sonnet",
            "anthropic",
            resolved="claude-sonnet-5",
            tier=Tier.STANDARD,
        ),
        ModelEntry(
            "claude", "opus", "Opus", "anthropic", resolved="claude-opus-5-5", tier=Tier.PREMIUM
        ),
    )
    routes = plan_route(policy, models, default_requests(policy))
    assert all(route.model is not None and route.model.resolved == model for route in routes)


def test_cross_trial_preserves_qualified_role_models(tmp_path: Path) -> None:
    matrix = definition(tmp_path)
    values = ("analyst=claude:claude-sonnet-5", "senior=codex:gpt-6-sol")
    chosen = replace(matrix.trials[0], cross_engine=True, role_models=values)
    command = trial.command(matrix, chosen)
    assert "--cross-engine" in command
    assert (
        tuple(command[index + 1] for index, value in enumerate(command) if value == "--role-model")
        == values
    )


def test_a_codex_team_trial_is_capped_and_counted_as_one_launch_per_role(tmp_path: Path) -> None:
    matrix = definition(tmp_path)
    team = replace(matrix.trials[0], engine="codex")
    command = trial.command(matrix, team)
    assert "--cross-engine" not in command
    assert command[command.index("--cross-budget-usd") + 1] == "0.40"
    audit = replace(team, type="investigation")
    assert trial.per_role(team) and not trial.per_role(audit)
    assert "--cross-budget-usd" not in trial.command(matrix, audit)
    assert "--cross-budget-usd" in trial.command(matrix, replace(audit, shape="pipeline"))
    assert "--cross-budget-usd" not in trial.command(matrix, replace(team, simple=True))
    assert "--cross-budget-usd" not in trial.command(matrix, matrix.trials[0])


@pytest.mark.parametrize(("senior", "code"), [(0.25, 0), (None, 1)])
def test_a_codex_team_trial_counts_every_role_and_stops_on_an_unknown_role_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, senior: float | None, code: int
) -> None:
    report = results.Report("team", tmp_path)
    monkeypatch.setattr(trial, "Report", lambda *a: report)
    original = report.run
    steps = [
        {"role": "analyst", "run_id": "run-a", "cost_usd": 0.1, "cost_source": "estimated"},
        {"role": "senior", "run_id": "run-s", "cost_usd": senior, "cost_source": "estimated"},
    ]

    def fake(name: str, command: list[str], **kwargs: Any) -> results.Step:
        value: dict[str, Any]
        if name.endswith("mandate"):
            value = {"spent_usd": 0.35, "steps": steps}
        elif name.endswith("show"):
            value = {"actual_usd": 0.1, "cost_source": "estimated"}
        else:
            value = {"totals": {"fresh_input": 10}}
        return original(name, [sys.executable, "-c", f"print({json.dumps(value)!r})"])

    monkeypatch.setattr(report, "run", fake)
    matrix = definition(tmp_path)
    matrix = replace(matrix, trials=(replace(matrix.trials[0], engine="codex"),))
    assert trial.run(matrix, False, None, True) == code
    summary = json.loads((report.directory / "summary.json").read_text())
    if senior is None:
        assert summary["unknown_spend_trials"] == ["one"]
    else:
        assert summary["known_spend_usd"] == 0.35
        assert summary["trials"][0]["cost_source"] == ["estimated"]


def test_native_trial_refuses_a_role_from_another_engine(tmp_path: Path) -> None:
    matrix = definition(tmp_path)
    chosen = replace(matrix.trials[0], role_models=("senior=codex:gpt-6-sol",))
    with pytest.raises(ValueError, match="engine must match"):
        trial.command(matrix, chosen)


def test_trial_smoke_fake_commands_collect_metrics_without_outcome_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = results.Report("smoke", tmp_path)
    monkeypatch.setattr(trial, "Report", lambda *a: report)
    original = report.run

    def fake(name: str, command: list[str], **kwargs: Any) -> results.Step:
        value: dict[str, Any]
        if name.endswith("mandate"):
            value = {"run_id": "run-one"}
        elif name.endswith("show"):
            value = {"actual_usd": 0.2, "cost_source": "reported", "turns": 3}
        else:
            value = {"totals": {"fresh_input": 10}}
        return original(name, [sys.executable, "-c", f"print({json.dumps(value)!r})"])

    monkeypatch.setattr(report, "run", fake)
    assert trial.run(definition(tmp_path), False, None, True) == 0
    data = json.loads((report.directory / "summary.json").read_text())
    assert data["known_spend_usd"] == 0.2
    assert data["trials"][0]["turns"] == 3
    assert all(
        "accept" not in step.command and "reject" not in step.command for step in report.steps
    )


def test_unknown_cost_is_never_zero() -> None:
    with pytest.raises(ValueError, match="unknown"):
        trial.cost(None)


def test_simple_trial_probes_projects_without_forge_agents(tmp_path: Path) -> None:
    loaded = spec.load(write_spec(tmp_path, "simple = true"))
    assert loaded.trials[0].simple
    assert "--simple" in trial.command(loaded, loaded.trials[0])
    with pytest.raises(ValueError, match="simple must be boolean"):
        spec.load(write_spec(tmp_path, 'simple = "true"'))
    with pytest.raises(ValueError, match="simple cannot"):
        spec.load(write_spec(tmp_path, "simple = true\ncross_engine = true"))


def test_snapshot_updates_require_explicit_tui_test_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = results.Report("smoke", tmp_path)
    monkeypatch.setattr(focus, "Report", lambda *args: report)
    assert focus.run([], snapshot_update=True) == 1
    assert focus.run(["tests/unit/test_cross_engine.py"], snapshot_update=True) == 1
    assert not report.steps


def test_trial_payload_reads_ndjson_events_then_a_multiline_result(tmp_path: Path) -> None:
    file = tmp_path / "output.log"
    file.write_text(
        '{"event":"start"}\n{\n "run_id": "one",\n "spent_usd": 0.2\n}\n', encoding="utf-8"
    )
    assert trial.payload(file) == {"run_id": "one", "spent_usd": 0.2}


def test_trial_displays_commands_with_windows_or_posix_quoting() -> None:
    arguments = ["cuanta", "--project", r"C:\Projects\my project", "runs", "accept", "one"]
    assert (
        trial.display(arguments, windows=True)
        == 'cuanta --project "C:\\Projects\\my project" runs accept one'
    )
    assert (
        trial.display(arguments, windows=False)
        == "cuanta --project 'C:\\Projects\\my project' runs accept one"
    )


def test_spec_rejects_duplicate_names_and_invalid_regex(tmp_path: Path) -> None:
    file = write_spec(tmp_path)
    content = file.read_text()
    file.write_text(
        content + content.split("[[trials]]", 1)[1].join(["[[trials]]", ""]), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Duplicate trial"):
        spec.load(file)
    file = write_spec(tmp_path, 'checks = [{file="built.html",regex="["}]')
    with pytest.raises(ValueError, match="Invalid check regex"):
        spec.load(file)


def test_gate_options_preserve_both_phases_and_twice_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = results.Report("smoke", tmp_path)
    monkeypatch.setattr(gate, "Report", lambda *a: report)
    monkeypatch.setattr(gate, "static", lambda r: True)

    def fake(name: str, command: list[str], **kwargs: Any) -> results.Step:
        step = results.Step(name, command, 0, 0, "fake")
        report.steps.append(step)
        return step

    monkeypatch.setattr(report, "run", fake)
    assert gate.run(twice=True) == 0
    assert [step.name for step in report.steps] == [
        "pytest-1",
        "performance-1",
        "pytest-2",
        "performance-2",
    ]
    report.steps.clear()
    assert gate.run(no_perf=True) == 0
    assert [step.name for step in report.steps] == ["pytest-1"]


def test_missing_structured_report_fails_closed(tmp_path: Path) -> None:
    report = results.Report("smoke", tmp_path)
    step = report.run(
        "pytest", [sys.executable, "-c", "print('success')"], xml=tmp_path / "missing.xml"
    )
    assert step.code == 1
    assert "Structured report unavailable" in step.problem
    assert report.finish() == 1


def test_timeout_ends_the_command_and_its_child_process(tmp_path: Path) -> None:
    marker = tmp_path / "child-finished"
    child = f"import time; from pathlib import Path; time.sleep(3); Path({str(marker)!r}).touch()"
    parent = (
        f"import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', {child!r}]); "
        "time.sleep(10)"
    )
    report = results.Report("timeout", tmp_path)
    step = report.run("fake", [sys.executable, "-c", parent], timeout=1)
    assert step.code == 1
    assert "timed out" in step.problem
    time.sleep(3)
    assert not marker.exists()


@pytest.mark.parametrize(
    "name", ["../bad.py", "/bad.py", "node_modules/x", ".cuanta/x", "C:\\bad.py", "a/../../bad.py"]
)
def test_after_images_reject_unsafe_paths_before_writing(tmp_path: Path, name: str) -> None:
    stored = tmp_path / ".cuanta/trials/run-one"
    stored.mkdir(parents=True)
    (stored / "trial.json").write_text(
        json.dumps({"changes": [{"path": name, "kind": "deleted"}]}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="relative"):
        acceptance.after_images(tmp_path, "run-one", tmp_path / "owned")


def test_after_images_verify_hashes_and_write_only_the_copy(tmp_path: Path) -> None:
    project = tmp_path / "project"
    files = project / ".cuanta/trials/run-one/files"
    files.mkdir(parents=True)
    (files / "file.py").write_bytes(b"new")
    manifest = {
        "changes": [
            {"path": "file.py", "kind": "added", "after": hashlib.sha256(b"new").hexdigest()}
        ]
    }
    (files.parent / "trial.json").write_text(json.dumps(manifest), encoding="utf-8")
    copy = tmp_path / "copy"
    copy.mkdir()
    assert acceptance.after_images(project, "run-one", copy) == ["file.py"]
    assert (copy / "file.py").read_bytes() == b"new"
    assert not (project / "file.py").exists()


@pytest.mark.parametrize("editable", [False, True])
def test_acceptance_imports_after_images_and_removes_its_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, editable: bool
) -> None:
    project = tmp_path / "project"
    source = project / "src"
    source.mkdir(parents=True)
    (source / "t0_sample.py").write_text("VALUE = 'before'\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(source))
    if editable:
        site = project / ".venv/Lib/site-packages"
        site.mkdir(parents=True)
        (site / "sample.pth").write_text(str(source), encoding="utf-8")
    images = project / ".cuanta/trials/run-one/files/src"
    images.mkdir(parents=True)
    content = b"VALUE = 'after'\n"
    (images / "t0_sample.py").write_bytes(content)
    manifest = {
        "changes": [
            {
                "path": "src/t0_sample.py",
                "kind": "modified",
                "after": hashlib.sha256(content).hexdigest(),
            }
        ]
    }
    (images.parent.parent / "trial.json").write_text(json.dumps(manifest), encoding="utf-8")
    probe = "import t0_sample; assert t0_sample.VALUE == 'after'; print(t0_sample.__file__)"
    task = replace(
        definition(project).trials[0], acceptance=(f'"{sys.executable}" -c "{probe}"',), checks=()
    )
    report = results.Report("acceptance", tmp_path)
    result = acceptance.verify(report, task, project, "run-one")
    assert result["passed"] and result["removed"]
    imported = Path(Path(report.steps[0].log).read_text().strip())
    assert not imported.is_relative_to(project)
    assert not imported.exists()
    assert (source / "t0_sample.py").read_text() == "VALUE = 'before'\n"


@pytest.mark.parametrize("actual", [None, 0.5])
def test_live_trial_stops_before_next_launch_on_unknown_or_exceeded_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, actual: float | None
) -> None:
    report = results.Report("cap", tmp_path)
    monkeypatch.setattr(trial, "Report", lambda *a: report)
    original = report.run
    launched: list[str] = []

    def fake(name: str, command: list[str], **kwargs: Any) -> results.Step:
        if name.endswith("mandate"):
            launched.append(name)
            value: dict[str, Any] = {"run_id": "run-one"}
        elif name.endswith("show"):
            value = {"actual_usd": actual, "cost_source": "reported"}
        else:
            value = {"totals": {"fresh_input": 10}}
        return original(name, [sys.executable, "-c", f"print({json.dumps(value)!r})"])

    monkeypatch.setattr(report, "run", fake)
    matrix = definition(tmp_path)
    matrix = replace(matrix, trials=(*matrix.trials, replace(matrix.trials[0], name="two")))
    assert trial.run(matrix, False, None, True) == 1
    assert launched == ["one-mandate"]
    summary = json.loads((report.directory / "summary.json").read_text())
    if actual is None:
        assert summary["unknown_spend_trials"] == ["one"]
    else:
        assert summary["known_spend_usd"] == 0.5


def test_a_trial_mode_defaults_to_v5_and_classic_passes_the_flag(tmp_path: Path) -> None:
    loaded = spec.load(write_spec(tmp_path))
    assert loaded.trials[0].mode == "v5"
    assert "--classic" not in trial.command(loaded, loaded.trials[0])
    classic = spec.load(write_spec(tmp_path, 'mode = "classic"'))
    assert classic.trials[0].mode == "classic"
    command = trial.command(classic, classic.trials[0])
    assert command.count("--classic") == 1 and "--sandbox" in command
    with pytest.raises(ValueError, match="mode must be classic or v5"):
        spec.load(write_spec(tmp_path, 'mode = "v4"'))


R3_RUN = {
    "actual_usd": 0.31,
    "cost_source": "reported",
    "outcome": "pending",
    "overhead": {"first_request_tokens": 21_000, "fixed_context_tokens": 19_500},
    "governor": {"blocked": {"reads": 3, "tokens_estimate": 12_000}},
}
R3_TOTALS = {
    "fresh_input": 1_200,
    "cache_read": 250_000,
    "cache_write": 30_000,
    "output": 4_000,
    "reasoning": 500,
}


def r3_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    recorded: str,
    rows: list[dict[str, Any]] | None = None,
) -> tuple[int, dict[str, Any]]:
    report = results.Report("r3", tmp_path)
    monkeypatch.setattr(trial, "Report", lambda *a: report)
    original = report.run
    metrics = (
        rows
        if rows is not None
        else [
            {"run_id": "other", "mode": "v5"},
            {
                "run_id": "run-one",
                "mode": recorded,
                "outcome": "pending",
                "forecast": {"p50_usd": 0.25, "p90_usd": 0.42, "cap_usd": 0.4},
                "actual_usd": 0.31,
                "p90_minus_actual_usd": 0.11,
                "blocked": {"reads": None},
            },
        ]
    )

    def fake(name: str, command: list[str], **kwargs: Any) -> results.Step:
        value: dict[str, Any]
        if name.endswith("mandate"):
            value = {"run_id": "run-one"}
        elif name.endswith("show"):
            value = R3_RUN
        elif name.endswith("metrics"):
            assert command[-2:] == ["costs", "--metrics"]
            value = {"metrics": metrics}
        else:
            value = {"totals": R3_TOTALS}
        return original(name, [sys.executable, "-c", f"print({json.dumps(value)!r})"])

    monkeypatch.setattr(report, "run", fake)
    matrix = definition(tmp_path)
    matrix = replace(matrix, trials=(replace(matrix.trials[0], mode=mode),))
    code = trial.run(matrix, False, None, True)
    summary = json.loads((report.directory / "summary.json").read_text())
    return code, summary


def test_the_trial_summary_collects_the_r3_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, summary = r3_run(tmp_path, monkeypatch, "classic", "classic")
    assert code == 0, summary["error"]
    row = summary["trials"][0]
    assert {
        key: row[key]
        for key in (
            "mode",
            "provider",
            "cost_usd",
            "fresh_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "output_tokens",
            "first_request_fixed_tokens",
            "reads_blocked",
            "blocked_tokens_estimate",
            "forecast_p50_usd",
            "forecast_p90_usd",
            "forecast_actual_usd",
            "p90_minus_actual_usd",
            "outcome",
        )
    } == {
        "mode": "classic",
        "provider": "claude",
        "cost_usd": 0.31,
        "fresh_tokens": 1_200,
        "cache_read_tokens": 250_000,
        "cache_write_tokens": 30_000,
        "output_tokens": 4_500,
        "first_request_fixed_tokens": 19_500,
        "reads_blocked": 3,
        "blocked_tokens_estimate": 12_000,
        "forecast_p50_usd": 0.25,
        "forecast_p90_usd": 0.42,
        "forecast_actual_usd": 0.31,
        "p90_minus_actual_usd": 0.11,
        "outcome": "pending",
    }
    assert isinstance(row["duration_s"], float) and "r3_error" not in row


def test_a_trial_stops_when_its_run_did_not_record_the_requested_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, summary = r3_run(tmp_path, monkeypatch, "classic", "v5")
    assert code == 1
    assert "recorded mode v5; the trial asked for classic" in summary["error"]


def test_missing_costs_metrics_leave_the_forecast_unknown_without_stopping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, summary = r3_run(tmp_path, monkeypatch, "v5", "v5", rows=[])
    assert code == 0
    row = summary["trials"][0]
    assert row["r3_error"] == "No costs metrics row for run-one"
    assert row["forecast_p50_usd"] is None and row["mode"] == "v5"
    assert row["reads_blocked"] == 3
