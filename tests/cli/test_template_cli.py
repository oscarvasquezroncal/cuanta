from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
from typer.testing import Result

from cuanta.adapters.forge.installer import VendoredForgeKit
from cuanta.adapters.system.sandbox_cleanup import BackgroundCleanup, finish_cleanup
from cuanta.application.home import HomeSnapshot
from cuanta.application.mandate_flow import MandateOptions
from cuanta.application.route_apply import RouteOptions
from cuanta.bootstrap import Container
from cuanta.domain.errors import DomainFailure
from cuanta.domain.fixes import FixAction, FixKind, classify
from cuanta.domain.forge_template import MANDATE_TEMPLATE, TEMPLATE_FIX, filled_template
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.progress import Note, ProgressEvent, Status
from cuanta.ports.system import Completed
from cuanta.tui.i18n import Catalog
from cuanta.tui.services import ContainerServices
from cuanta.tui.widgets.header import forge_fact
from tests.cli.test_engine_guarantees import AGENT, codex_ready
from tests.cli.test_forecast_preview import REAL_RUN_FLAGS
from tests.fakes import FakeRunner, FakeStream
from tests.real_run import phased_mandate
from tests.support import invoke

VENDORED = (
    Path(__file__).parents[2]
    / "src/cuanta/assets/forge/skills/agent-system-init/templates/MANDATE_TEMPLATE.template.md"
)
REAL_TEAM = ("architecture-analyst", "backend-senior", "tester", "docs-updater")
VERSION = VendoredForgeKit(Path(".")).vendored_version()
WRITTEN = f"docs/MANDATE_TEMPLATE.md was missing: wrote it from the vendored Forge {VERSION}"
PLANNED = f"write: docs/MANDATE_TEMPLATE.md from the vendored Forge {VERSION}"
BORROWED = (
    f"docs/MANDATE_TEMPLATE.md is missing: this run uses the vendored Forge {VERSION} copy; "
    "cuanta init --template keeps it"
)
MISSING = (
    "docs/MANDATE_TEMPLATE.md missing: cuanta writes it from the vendored Forge "
    f"{VERSION} when a mandate needs it"
)
NOT_FOUND = "docs/MANDATE_TEMPLATE.md not found"
BROKEN = "docs/MANDATE_TEMPLATE.md has no fenced === REQUEST === block"
UNREADABLE = "docs/MANDATE_TEMPLATE.md cannot be read as a plain file inside the project"
UNUSABLE_HINT = "fix it, or delete it and run cuanta init --template"
WRITE_HINT = "make sure docs is a folder you can write to"
RESULT = (
    '{"type":"result","subtype":"success","total_cost_usd":0.01,"is_error":false,'
    '"result":"## SUMMARY\\nok"}'
)
WAYS_IN = [
    ("mandate", "-f", "mandato.md"),
    ("pounce", "--from", "mandato.md"),
    ("run", "mandato.md"),
    ("feat", "Separa el total del carrito"),
    ("fix", "El total suma dos veces el envío"),
]
UNUSABLE = [("no_block", BROKEN), ("folder", UNREADABLE)]


def real_project(root: Path, names: tuple[str, ...] = REAL_TEAM) -> Path:
    agents = root / ".claude" / "agents"
    agents.mkdir(parents=True)
    for name in names:
        (agents / f"{name}.md").write_text(AGENT.format(name=name), encoding="utf-8")
    (root / "mandato.md").write_text(phased_mandate(), encoding="utf-8")
    return root


def expected(root: Path, project: str = "") -> str:
    text = VENDORED.read_text(encoding="utf-8")
    return filled_template(text, project or root.name, "backend-senior")


def template_of(root: Path) -> str | None:
    path = root / MANDATE_TEMPLATE
    return path.read_text(encoding="utf-8") if path.is_file() else None


def tree(root: Path) -> set[str]:
    found: set[str] = set()
    for folder, names, files in os.walk(root):
        relative = Path(folder).relative_to(root)
        if relative == Path("."):
            names[:] = [name for name in names if name != ".cuanta"]
        found.update((relative / name).as_posix() for name in (*names, *files))
    return found


def real_command(root: Path) -> list[str]:
    return [
        "mandate",
        "--type",
        "feature",
        *REAL_RUN_FLAGS,
        "--what",
        "Refactor del intérprete",
        "--evidence",
        str(root / "mandato.md"),
        "--tests",
        "pytest tests/test_consult.py",
        "--out-of-scope",
        "frontend",
        "--route",
        "fixed",
        "--project",
        str(root),
    ]


def file_command(root: Path, *extra: str) -> list[str]:
    return [
        "mandate",
        "-f",
        str(root / "mandato.md"),
        "--profile",
        "balanced",
        *extra,
        "--project",
        str(root),
    ]


def output(result: Result) -> str:
    return result.stdout + result.stderr


def flat(result: Result) -> str:
    return " ".join(output(result).split())


def payload(result: Result) -> dict[str, object]:
    assert result.exit_code == 0, output(result)
    data = json.loads(result.stdout)
    assert isinstance(data, dict)
    return data


def refusal(result: Result) -> tuple[str, str]:
    assert result.exit_code == 1, output(result)
    error = json.loads(result.stdout)["error"]
    return error["message"], error["hint"]


def checks(root: Path) -> dict[str, dict[str, str]]:
    document = json.loads(invoke(["doctor", "--json", "--project", str(root)]).stdout)
    return {check["name"]: check for check in document["checks"]}


def home_of(root: Path) -> HomeSnapshot:
    container = Container.for_project(root)
    try:
        return container.home_query().run()
    finally:
        container.close()


def test_the_real_run_dry_run_plans_the_missing_template_and_writes_nothing(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    root = real_project(tmp_path)
    before = tree(root)
    data = payload(invoke([*real_command(root), "--dry-run", "--json"]))
    assert tree(root) == before
    assert data["template_note"] == PLANNED
    team = data["team"]
    assert isinstance(team, list) and team.count(PLANNED) == 1
    prompt = data["prompt"]
    assert isinstance(prompt, str)
    assert f"# MANDATE — {root.name}" in prompt
    assert "2. **backend-senior** — implements ONE phase per invocation" in prompt
    assert "{{" not in prompt
    shown = invoke(["--plain", *real_command(root), "--dry-run"])
    assert shown.exit_code == 0, output(shown)
    assert flat(shown).count(PLANNED) == 1
    assert WRITTEN not in flat(shown)
    assert tree(root) == before
    assert not fake_runner.stdins


def test_the_real_run_launch_writes_the_template_says_so_once_and_runs(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    root = real_project(tmp_path)
    fake_runner.streams["claude -p"] = FakeStream([RESULT])
    result = invoke(real_command(root))
    assert result.exit_code == 0, output(result)
    assert output(result).count(WRITTEN) == 1
    assert NOT_FOUND not in output(result)
    assert "run cuanta init first" not in output(result)
    assert template_of(root) == expected(root)
    sent = [stdin for stdin in fake_runner.stdins if stdin]
    assert sent and f"# MANDATE — {root.name}" in sent[0]
    assert "**backend-senior**" in sent[0]


@pytest.mark.parametrize("way", WAYS_IN, ids=[way[0] for way in WAYS_IN])
def test_every_way_into_a_team_mandate_plans_the_missing_template_without_writing_it(
    tmp_path: Path, fake_runner: FakeRunner, way: tuple[str, ...]
) -> None:
    root = real_project(tmp_path)
    command, *rest = way
    arguments = [str(root / part) if part.endswith(".md") else part for part in rest]
    flags = ["--profile", "balanced", "--dry-run", "--json", "--project", str(root)]
    data = payload(invoke([command, *arguments, *flags]))
    assert template_of(root) is None
    assert data["template_note"] == PLANNED


def test_queue_run_writes_the_missing_template_and_launches(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    root = real_project(tmp_path)
    project = ["--project", str(root)]
    added = invoke(
        ["queue", "add", "-f", str(root / "mandato.md"), "--profile", "balanced", *project]
    )
    assert added.exit_code == 0, output(added)
    assert template_of(root) is None
    fake_runner.queued["claude -p"] = [FakeStream([RESULT])]
    ran = invoke(["queue", "run", "--yes", *project])
    assert ran.exit_code == 0, output(ran)
    assert WRITTEN in output(ran)
    assert template_of(root) == expected(root)
    assert [stdin for stdin in fake_runner.stdins if stdin]


def test_runs_that_never_read_the_template_leave_it_alone(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    root = real_project(tmp_path)
    audit = payload(
        invoke(["audit", "Explica el intérprete", "--dry-run", "--json", "--project", str(root)])
    )
    simple = payload(
        invoke(
            ["feat", "Separa el total", "--simple", "--dry-run", "--json", "--project", str(root)]
        )
    )
    assert audit["template_note"] is None and simple["template_note"] is None
    assert template_of(root) is None


def test_a_team_of_separate_launches_never_reads_the_template_and_its_dry_run_says_so(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    root = real_project(tmp_path)
    codex_ready(fake_runner)
    data = payload(invoke(file_command(root, "--engine", "codex", "--dry-run", "--json")))
    assert data["per_role"] is True
    assert data["template_note"] is None
    assert template_of(root) is None


def test_an_incomplete_team_still_fails_with_the_init_hint(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    root = real_project(tmp_path, ("architecture-analyst", "backend-senior", "tester"))
    result = invoke([*real_command(root), "--dry-run", "--json"])
    assert refusal(result) == (NOT_FOUND, "run cuanta init first")
    assert template_of(root) is None
    assert checks(root)["template"]["fix"] == "cuanta init"


def test_doctor_reports_the_missing_template_until_a_launch_writes_it(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    root = real_project(tmp_path)
    names = list(checks(root))
    assert names.index("template") == names.index("forge") + 1
    before = checks(root)["template"]
    assert (before["status"], before["detail"], before["fix"]) == ("warn", MISSING, TEMPLATE_FIX)
    payload(invoke([*real_command(root), "--dry-run", "--json"]))
    planned = checks(root)["template"]
    assert (planned["status"], planned["detail"], planned["fix"]) == (
        "warn",
        MISSING,
        TEMPLATE_FIX,
    )
    fake_runner.streams["claude -p"] = FakeStream([RESULT])
    launched = invoke(real_command(root))
    assert launched.exit_code == 0, output(launched)
    after = checks(root)["template"]
    assert (after["status"], after["detail"], after["fix"]) == (
        "ok",
        "docs/MANDATE_TEMPLATE.md ready",
        "",
    )


@pytest.mark.parametrize("engine", ["absent", "old"])
def test_a_launch_that_cannot_start_its_engine_writes_no_template(
    tmp_path: Path, fake_runner: FakeRunner, engine: str
) -> None:
    root = real_project(tmp_path)
    if engine == "absent":
        fake_runner.binaries.clear()
    else:
        fake_runner.responses["claude --help"] = Completed(0, "-p, --print", "")
    before = tree(root)
    result = invoke(file_command(root, "--route", "fixed"))
    assert result.exit_code == 3, output(result)
    assert tree(root) == before
    assert "MANDATE_TEMPLATE" not in output(result)
    assert not [stdin for stdin in fake_runner.stdins if stdin]


@pytest.fixture
def isolated_copies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    scratch = tmp_path / "temp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))

    def cleanup(job: BackgroundCleanup) -> None:
        assert finish_cleanup(job._path)

    monkeypatch.setattr(BackgroundCleanup, "__call__", cleanup)
    return scratch


def test_a_launch_in_an_isolated_copy_never_writes_the_template_and_says_so(
    tmp_path: Path, fake_runner: FakeRunner, isolated_copies: Path
) -> None:
    root = real_project(tmp_path / "project")
    before = tree(root)
    planned = payload(invoke([*real_command(root), "--sandbox", "--dry-run", "--json"]))
    assert tree(root) == before
    assert planned["template_note"] == BORROWED
    fake_runner.streams["claude -p"] = FakeStream([RESULT])
    result = invoke([*real_command(root), "--sandbox"])
    assert result.exit_code == 0, output(result)
    assert tree(root) == before
    assert flat(result).count(BORROWED) == 1
    assert WRITTEN not in flat(result)
    launches = [
        cwd
        for call, cwd in zip(fake_runner.calls, fake_runner.cwds, strict=True)
        if call[:2] == ("claude", "-p")
    ]
    assert launches and all(
        cwd is not None and Path(cwd).resolve().is_relative_to(isolated_copies.resolve())
        for cwd in launches
    )
    sent = [stdin for stdin in fake_runner.stdins if stdin]
    assert sent and "**backend-senior**" in sent[0]


def spoil(root: Path, kind: str) -> None:
    target = root / MANDATE_TEMPLATE
    target.parent.mkdir(parents=True, exist_ok=True)
    if kind == "folder":
        target.mkdir()
    else:
        target.write_text("# my template\n\nno request block\n", encoding="utf-8")


@pytest.mark.parametrize(("kind", "problem"), UNUSABLE, ids=[kind for kind, _ in UNUSABLE])
def test_a_template_that_cannot_be_used_gets_one_message_everywhere(
    tmp_path: Path, fake_runner: FakeRunner, kind: str, problem: str
) -> None:
    root = real_project(tmp_path)
    spoil(root, kind)
    before = tree(root)
    row = checks(root)["template"]
    assert (row["status"], row["detail"], row["fix"]) == ("warn", f"{problem}: {UNUSABLE_HINT}", "")
    for command in (
        [*real_command(root), "--dry-run", "--json"],
        ["init", str(root), "--template", "--json"],
        ["init", str(root), "--template", "--dry-run", "--json"],
    ):
        assert refusal(invoke(command)) == (problem, UNUSABLE_HINT)
    home = home_of(root)
    assert home.initialized and not home.template_missing
    assert forge_fact(Catalog("en"), home) == "installed · template unusable"
    assert forge_fact(Catalog("es"), home) == "instalado · plantilla inutilizable"
    assert tree(root) == before


def linked_docs(root: Path, outside: Path) -> None:
    docs = root / "docs"
    if sys.platform == "win32":
        winapi: Any = importlib.import_module("_winapi")
        winapi.CreateJunction(str(outside), str(docs))
        assert docs.is_junction()
    else:
        docs.symlink_to(outside, target_is_directory=True)
        assert docs.is_symlink()


def linked_template(root: Path, target: Path) -> None:
    (root / "docs").mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, root / MANDATE_TEMPLATE)
    except OSError as error:
        pytest.skip(f"symbolic links are not available here: {error}")


@pytest.mark.parametrize("link", ["template", "docs"])
def test_a_linked_template_path_is_never_read_or_written_through(
    tmp_path: Path, fake_runner: FakeRunner, link: str
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = real_project(tmp_path / "project")
    if link == "template":
        linked_template(root, outside / "escaped.md")
    else:
        linked_docs(root, outside)
    row = checks(root)["template"]
    assert (row["status"], row["detail"], row["fix"]) == (
        "warn",
        f"{UNREADABLE}: {UNUSABLE_HINT}",
        "",
    )
    for command in (
        [*real_command(root), "--dry-run", "--json"],
        ["init", str(root), "--template", "--json"],
    ):
        assert refusal(invoke(command)) == (UNREADABLE, UNUSABLE_HINT)
    with pytest.raises(DomainFailure) as previewed:
        ContainerServices(root).preview_mandate(app_request(), 0, app_options())
    assert (previewed.value.message, previewed.value.hint) == (UNREADABLE, UNUSABLE_HINT)
    assert list(outside.iterdir()) == []


def test_a_docs_link_in_a_project_without_forge_agents_gets_no_template_row(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    linked_docs(root, outside)
    assert "template" not in checks(root)
    assert home_of(root).check("template") is None
    assert refusal(invoke(["init", str(root), "--template", "--json"])) == (
        "docs/MANDATE_TEMPLATE.md needs the four Forge agents; missing architecture-analyst.md, "
        "tester.md, docs-updater.md, <lang>-senior.md",
        "run cuanta init",
    )
    assert list(outside.iterdir()) == []


def test_the_explicit_template_fix_names_the_write_failure_and_what_to_check(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    root = real_project(tmp_path)
    (root / "docs").write_text("not a folder\n", encoding="utf-8")
    message, hint = refusal(invoke(["init", str(root), "--template", "--json"]))
    assert message.startswith("could not write docs/MANDATE_TEMPLATE.md (")
    assert "vendored" not in message
    assert hint == WRITE_HINT
    with pytest.raises(DomainFailure) as failed:
        ContainerServices(root).apply_fix(classify(TEMPLATE_FIX))
    assert failed.value.message == message
    assert failed.value.hint == WRITE_HINT
    assert (root / "docs").read_text(encoding="utf-8") == "not a folder\n"


def test_the_template_carries_the_project_name_home_shows(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    root = real_project(tmp_path / "dry")
    manifest = '[project]\nname = "consult-service"\nversion = "0.1.0"\n'
    (root / "pyproject.toml").write_text(manifest, encoding="utf-8")
    data = payload(invoke([*real_command(root), "--dry-run", "--json"]))
    prompt = data["prompt"]
    assert isinstance(prompt, str)
    assert "# MANDATE — consult-service" in prompt
    assert "# MANDATE — dry" not in prompt
    payload(invoke(["init", str(root), "--template", "--json"]))
    assert template_of(root) == expected(root, "consult-service")
    assert home_of(root).report.detection.project_name == "consult-service"


def test_the_doctor_fix_writes_the_template_in_seconds_and_keeps_init_working(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    root = real_project(tmp_path)
    planned = invoke(["init", str(root), "--template", "--dry-run"])
    assert planned.exit_code == 0, output(planned)
    assert PLANNED in planned.stdout
    assert template_of(root) is None
    written = payload(invoke([*TEMPLATE_FIX.split()[1:], "--json", "--project", str(root)]))
    assert written["template"] == {
        "path": MANDATE_TEMPLATE,
        "state": "restorable",
        "written": True,
        "message": WRITTEN,
    }
    assert template_of(root) == expected(root)
    assert not fake_runner.calls
    again = payload(invoke(["init", str(root), "--template", "--json"]))
    assert again["template"] == {
        "path": MANDATE_TEMPLATE,
        "state": "ready",
        "written": False,
        "message": "docs/MANDATE_TEMPLATE.md ready",
    }
    full = payload(invoke(["init", str(root), "--dry-run", "--json"]))
    assert full["dry_run"] is True and "template" not in full
    partial = real_project(tmp_path / "partial", ("architecture-analyst", "python-senior"))
    refused = invoke(["init", str(partial), "--template", "--json"])
    assert refusal(refused) == (
        "docs/MANDATE_TEMPLATE.md needs the four Forge agents; missing tester.md, docs-updater.md",
        "run cuanta init",
    )
    assert template_of(partial) is None


def test_home_and_the_app_fix_see_the_template_of_the_real_project(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    root = real_project(tmp_path)
    home = home_of(root)
    check = home.check("template")
    assert home.initialized and home.template_missing
    assert check is not None and (check.status, check.detail, check.fix) == (
        Status.WARN,
        MISSING,
        TEMPLATE_FIX,
    )
    assert forge_fact(Catalog("en"), home) == "installed · template missing"
    fix = classify(check.fix)
    assert (fix.kind, fix.action) == (FixKind.RUN, FixAction.TEMPLATE)
    assert ContainerServices(root).apply_fix(fix) == MANDATE_TEMPLATE
    assert template_of(root) == expected(root)
    after = home_of(root)
    assert not after.template_missing
    assert forge_fact(Catalog("en"), after) == "installed"
    ready = after.check("template")
    assert ready is not None and ready.status is Status.OK


def app_request() -> MandateRequest:
    return MandateRequest(
        type="feature",
        what="Refactor del intérprete",
        why=phased_mandate(),
        tests="pytest tests/test_consult.py",
        out_of_scope="frontend",
    )


def app_options() -> MandateOptions:
    return MandateOptions(profile="balanced", route=RouteOptions(mode="fixed"))


def test_the_app_preview_plans_the_template_and_the_launch_writes_it_and_says_so(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    root = real_project(tmp_path)
    before = tree(root)
    preview = ContainerServices(root).preview_mandate(app_request(), 0, app_options())
    assert tree(root) == before
    assert preview.template_note is not None
    assert (preview.template_note.status, preview.template_note.text) == (Status.INFO, PLANNED)
    assert "**backend-senior**" in preview.prompt
    fake_runner.streams["claude -p"] = FakeStream([RESULT])
    events: list[ProgressEvent] = []
    ContainerServices(root).run_mandate(
        app_request(), 0, app_options(), lambda event: None, events.append
    )
    told = [event.text for event in events if isinstance(event, Note)]
    assert told.count(WRITTEN) == 1
    assert PLANNED not in told
    assert template_of(root) == expected(root)
