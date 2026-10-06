from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import Result

from cuanta.bootstrap import Container
from cuanta.domain.change_plan import EditTarget
from cuanta.domain.config import Config
from cuanta.domain.engine import COMMAND_LINE_LIMIT, command_line_length
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.mandate_file import parse_mandate_file
from cuanta.ports.system import Completed
from tests.cli.conftest import CLAUDE_HELP
from tests.cli.test_engine_guarantees import forge
from tests.fakes import FakeRunner, FakeStream, copy_repo
from tests.real_run import phased_mandate
from tests.support import invoke
from tests.unit.test_mandate_file import FILLED_REQUEST, template_copy, unfenced_copy

REAL_TITLE = "Mandato — refactor del intérprete de consultas y del endpoint de consulta"
TYPE_NOTE = "type not stated: feature; use -t/--type"
RESULT = (
    '{"type":"result","subtype":"success","total_cost_usd":0.01,"is_error":false,'
    '"result":"## SUMMARY\\nok"}'
)
CLIPPED = ("[...]", "characters in total", "cuanta cat cap:")
LABELLED = """TYPE: bug
WHAT: Fix the cart total
WHY / EVIDENCE: AssertionError: 30 != 31
  at src/cart.py:41
WHERE: src/cart.py
CONSTRAINTS: keep the public API
EXPECTED TESTS: tests/test_cart.py::test_total
OUT OF SCOPE: billing
"""
LABELLED_ES = """# Refactor del carrito

TIPO: refactorización
QUÉ: separar el cálculo del total
POR QUÉ: el total suma dos veces el envío
FUERA DE ALCANCE: el checkout
"""
SHORTCUTS = [("feat", "feature"), ("fix", "bug"), ("audit", "investigation")]
ADDED = "Added on the command line:"
WHY_NOTE = "--why: added after the file's evidence"
WHAT_NOTE = "--what: the file's what moves to the top of the evidence"
EVERY_LABEL = (
    "fields from the file's labels: type, what, why, where, constraints, tests, out of scope"
)
SPANISH_LABELS = "fields from the file's labels: type, what, why, out of scope"
REAL_DOCS = {"on": True, "reason": "agent", "field": "why", "term": "docs-updater"}


def field(label: str, value: str) -> str:
    return f"{label:<16} {value}"


def written(root: Path, text: str, name: str = "mandato.md") -> Path:
    path = root / name
    path.write_text(text, encoding="utf-8")
    return path


def run_cli(root: Path, *args: str, pretty: bool = False, answer: str | None = None) -> Result:
    return invoke([*args, "--project", str(root)], pretty=pretty, input_text=answer)


def dry(root: Path, *args: str) -> dict[str, object]:
    result = run_cli(root, *args, "--dry-run", "--json")
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert isinstance(data, dict)
    return data


def refusal(root: Path, *args: str) -> str:
    result = run_cli(root, *args, "--dry-run", "--json")
    assert result.exit_code == 1, result.stdout
    error = json.loads(result.stdout)["error"]
    assert isinstance(error, dict)
    return f"{error['message']} · {error['hint']}"


def evidence_lines(text: str) -> list[str]:
    return [line.strip() for line in text.partition("\n")[2].splitlines() if line.strip()]


def prompt_of(data: dict[str, object]) -> str:
    prompt = data["prompt"]
    assert isinstance(prompt, str)
    return prompt


def launches(runner: FakeRunner) -> list[tuple[str, ...]]:
    return [call for call in runner.calls if call[:2] == ("claude", "-p")]


@pytest.mark.parametrize("configured", [False, True])
def test_spanish_session_requests_a_spanish_report_with_stable_headings(
    tmp_path: Path, fake_runner: FakeRunner, configured: bool
) -> None:
    forge(tmp_path)
    if configured:
        (tmp_path / ".cuanta").mkdir(exist_ok=True)
        (tmp_path / ".cuanta/config.toml").write_text('[ui]\nlanguage = "es"\n', encoding="utf-8")
    arguments = [] if configured else ["--lang", "es"]
    data = dry(tmp_path, "audit", "Explain src/cart.py", *arguments)
    prompt = prompt_of(data)
    assert "Write the report in Spanish" in prompt
    assert "Do not translate paths, code names or quotations" in prompt
    assert "## SUMMARY" in prompt and "## FINDINGS" in prompt
    assert "same language as the request" not in prompt
    assert "Explain src/cart.py" in prompt


@pytest.mark.parametrize("command", ["audit", "feat"])
def test_command_answer_visibility_and_report_file(
    tmp_path: Path,
    fake_runner: FakeRunner,
    command: str,
) -> None:
    forge(tmp_path)
    fake_runner.streams["claude -p"] = FakeStream([RESULT])
    result = run_cli(
        tmp_path,
        command,
        "Explain src/cart.py",
        "--profile",
        "balanced",
        "--route",
        "fixed",
        "--verify",
        "off",
        "--lang",
        "es",
    )
    assert result.exit_code == 0, result.stdout
    assert ("## SUMMARY" in result.stdout) is (command == "audit")
    report = next((tmp_path / ".cuanta/runs").glob("*/report.md"))
    assert "## SUMMARY\nok" in report.read_text(encoding="utf-8")
    assert report.as_posix().split("/.cuanta/")[-1] in result.stdout.replace("\\", "/")
    assert f"cuanta runs show {report.parent.name}" in result.stdout


@pytest.mark.parametrize(
    "command", [("mandate", "-f"), ("mandate", "--from"), ("run",), ("run", "-f")]
)
def test_the_real_run_mandate_runs_from_its_file_with_nothing_else(
    tmp_path: Path, fake_runner: FakeRunner, command: tuple[str, ...]
) -> None:
    forge(tmp_path)
    path = written(tmp_path, phased_mandate())
    data = dry(tmp_path, *command, str(path))
    prompt = prompt_of(data)
    assert field("TYPE:", "feature") in prompt
    assert field("WHAT:", REAL_TITLE) in prompt
    assert field("WHY / EVIDENCE:", "## Contexto") in prompt
    assert all(line in prompt for line in evidence_lines(phased_mandate()))
    assert not any(marker in prompt for marker in CLIPPED)
    assert field("EXPECTED TESTS:", "none stated") in prompt
    assert field("OUT OF SCOPE:", "none stated") in prompt
    assert data["request_notes"] == [TYPE_NOTE]
    team = data["team"]
    assert isinstance(team, list) and TYPE_NOTE in team
    assert not fake_runner.stdins


def test_an_interactive_console_asks_nothing_for_a_mandate_file(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    path = written(tmp_path, phased_mandate())
    result = run_cli(tmp_path, "run", str(path), "--dry-run", pretty=True)
    assert result.exit_code == 0, result.stdout
    text = " ".join(result.stdout.split())
    assert TYPE_NOTE in text
    assert REAL_TITLE in text
    assert not fake_runner.stdins


def test_a_labelled_file_is_split_into_its_fields(tmp_path: Path, fake_runner: FakeRunner) -> None:
    forge(tmp_path)
    prompt = prompt_of(dry(tmp_path, "mandate", "-f", str(written(tmp_path, LABELLED))))
    assert field("TYPE:", "bug") in prompt
    assert field("WHAT:", "Fix the cart total") in prompt
    assert field("WHY / EVIDENCE:", "AssertionError: 30 != 31") in prompt
    assert f"{' ' * 17}at src/cart.py:41" in prompt
    assert field("WHERE:", "src/cart.py") in prompt
    assert field("CONSTRAINTS:", "keep the public API") in prompt
    assert field("EXPECTED TESTS:", "tests/test_cart.py::test_total") in prompt
    assert field("OUT OF SCOPE:", "billing") in prompt
    labelled = dry(tmp_path, "mandate", "-f", str(written(tmp_path, LABELLED, "labelled.md")))
    assert labelled["request_notes"] == [EVERY_LABEL]
    spanish = dry(tmp_path, "run", str(written(tmp_path, LABELLED_ES, "es.md")))
    shown = prompt_of(spanish)
    assert field("TYPE:", "refactor") in shown
    assert field("WHAT:", "separar el cálculo del total") in shown
    assert field("OUT OF SCOPE:", "el checkout") in shown
    assert "el total suma dos veces el envío" in shown
    assert spanish["request_notes"] == [SPANISH_LABELS]
    team = spanish["team"]
    assert isinstance(team, list) and SPANISH_LABELS in team


def test_an_unknown_type_in_the_file_is_refused_with_the_type_option(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    path = written(tmp_path, "TYPE: chore\nWHAT: tidy the imports\n", "chore.md")
    assert refusal(tmp_path, "run", str(path)) == (
        "unknown type chore in the file's TYPE line · "
        "use one of bug, feature, investigation, refactor, or pass -t/--type"
    )
    pinned = prompt_of(dry(tmp_path, "run", str(path), "-t", "refactor"))
    assert field("TYPE:", "refactor") in pinned
    assert field("WHAT:", "tidy the imports") in pinned
    assert not fake_runner.stdins


@pytest.mark.parametrize(
    ("text", "what"),
    [
        ("TYPE: docs\nUpdate the README with the new --from flag.\n", "Update the README with"),
        ("# Update the README\n\nTYPE: docs\n\nMention the new --from flag.\n", "Update the"),
    ],
    ids=["first_line", "after_title"],
)
def test_a_file_that_opens_with_an_unknown_type_is_refused(
    tmp_path: Path, fake_runner: FakeRunner, text: str, what: str
) -> None:
    forge(tmp_path)
    path = written(tmp_path, text, "docs.md")
    assert refusal(tmp_path, "run", str(path)) == (
        "unknown type docs in the file's TYPE line · "
        "use one of bug, feature, investigation, refactor, or pass -t/--type"
    )
    pinned = prompt_of(dry(tmp_path, "run", str(path), "-t", "feature"))
    assert field("TYPE:", "feature") in pinned
    assert field("WHAT:", what) in pinned
    assert not fake_runner.stdins


def test_the_command_line_overrides_the_file(tmp_path: Path, fake_runner: FakeRunner) -> None:
    forge(tmp_path)
    path = written(tmp_path, phased_mandate())
    data = dry(
        tmp_path,
        "mandate",
        "-f",
        str(path),
        "-t",
        "bug",
        "--tests",
        "pytest tests/test_consult.py",
        "--out-of-scope",
        "frontend",
        "--where",
        "app/services/interpreter.py",
    )
    prompt = prompt_of(data)
    assert field("TYPE:", "bug") in prompt
    assert field("WHAT:", REAL_TITLE) in prompt
    assert field("EXPECTED TESTS:", "pytest tests/test_consult.py") in prompt
    assert field("OUT OF SCOPE:", "frontend") in prompt
    assert field("WHERE:", "app/services/interpreter.py") in prompt
    assert data["request_notes"] == []
    labelled = written(tmp_path, LABELLED, "labelled.md")
    overridden = prompt_of(
        dry(tmp_path, "run", str(labelled), "--type", "feature", "--what", "Show the total")
    )
    assert field("TYPE:", "feature") in overridden
    assert field("WHAT:", "Show the total") in overridden
    assert field("OUT OF SCOPE:", "billing") in overridden


def test_a_fifty_kilobyte_mandate_reaches_the_engine_whole_on_stdin(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    body = "\n".join(
        f"línea {index}: revisa `app/services/interpreter.py`" for index in range(1_300)
    )
    text = f"# Mandato grande\n\n{body}\n"
    assert len(text.encode("utf-8")) > 50_000
    path = written(tmp_path, text)
    prompt = prompt_of(dry(tmp_path, "mandate", "-f", str(path)))
    assert field("WHAT:", "Mandato grande") in prompt
    assert all(f"línea {index}:" in prompt for index in (0, 650, 1_299))
    assert not any(marker in prompt for marker in CLIPPED)
    fake_runner.streams["claude -p"] = FakeStream([RESULT])
    result = run_cli(tmp_path, "run", str(path), "--profile", "balanced", "--route", "fixed")
    assert result.exit_code == 0, result.stdout
    sent = [stdin for stdin in fake_runner.stdins if stdin]
    assert sent and "línea 1299:" in sent[0] and "línea 0:" in sent[0]
    for call in launches(fake_runner):
        assert "línea 1299" not in " ".join(call)
        assert command_line_length(call) < COMMAND_LINE_LIMIT


def test_queue_add_takes_a_mandate_file_and_runs_it_whole(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    path = written(tmp_path, phased_mandate())
    added = run_cli(tmp_path, "queue", "add", "-f", str(path), "--simple", "--json")
    assert added.exit_code == 0, added.stdout
    data = json.loads(added.stdout)
    entry = data["added"]
    assert (entry["type"], entry["request"]) == ("feature", REAL_TITLE)
    assert entry["args"] == ["-f", str(path), "--simple"]
    assert data["request_notes"] == [TYPE_NOTE]
    listed = json.loads(run_cli(tmp_path, "queue", "list", "--json").stdout)
    assert listed["queue"][0]["request"] == REAL_TITLE
    fake_runner.queued["claude -p"] = [FakeStream([RESULT])]
    ran = run_cli(tmp_path, "queue", "run", "--yes", "--json")
    assert ran.exit_code == 0, ran.stdout + ran.stderr
    sent = [stdin for stdin in fake_runner.stdins if stdin]
    last = evidence_lines(phased_mandate())[-1]
    assert sent and last in sent[0] and REAL_TITLE in sent[0]
    refused = run_cli(tmp_path, "queue", "add", "-f", str(tmp_path / "absent.md"), "--json")
    assert refused.exit_code == 1
    assert "mandate file not found" in json.loads(refused.stdout)["error"]["message"]


@pytest.mark.parametrize(("command", "kind"), SHORTCUTS)
def test_one_argument_names_the_type_and_the_what(
    tmp_path: Path, fake_runner: FakeRunner, command: str, kind: str
) -> None:
    forge(tmp_path)
    data = dry(tmp_path, command, "Explica el total del carrito")
    prompt = prompt_of(data)
    assert field("TYPE:", kind) in prompt
    assert field("WHAT:", "Explica el total del carrito") in prompt
    assert data["request_notes"] == []
    words = prompt_of(dry(tmp_path, command, "explica", "el", "carrito"))
    assert field("WHAT:", "explica el carrito") in words
    labelled = written(tmp_path, LABELLED_ES)
    from_file = prompt_of(dry(tmp_path, command, "--from", str(labelled)))
    assert field("TYPE:", kind) in from_file
    assert field("WHAT:", "separar el cálculo del total") in from_file
    pinned = prompt_of(dry(tmp_path, command, "-f", str(labelled), "--type", "refactor"))
    assert field("TYPE:", "refactor") in pinned
    titled = prompt_of(dry(tmp_path, command, "Otro título", "-f", str(labelled)))
    assert field("WHAT:", "Otro título") in titled
    assert "el total suma dos veces el envío" in titled


def test_the_short_commands_take_every_mandate_option(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    data = dry(
        tmp_path,
        "feat",
        "Botón de WhatsApp en cada producto",
        "--profile",
        "balanced",
        "--pure",
        "--model",
        "claude-opus-5-5",
        "--variant",
        "ultracode",
        "--depth",
        "deep",
        "--max-turns",
        "7",
        "--max-budget-usd",
        "2",
        "--max-wall",
        "10",
        "--docs",
        "off",
        "--sandbox",
    )
    assert data["sandbox"] is True
    assert data["model"] == "claude-opus-5-5"
    assert data["limits"] == {"budget_usd": 2.0, "max_turns": 7, "wall_min": 10.0}
    assert data["docs"] == {"on": False, "reason": "flag_off"}
    path = written(tmp_path, phased_mandate())
    run_data = dry(tmp_path, "run", str(path), "--max-turns", "9", "--docs", "on")
    assert run_data["limits"] == {"budget_usd": None, "max_turns": 9, "wall_min": None}


def test_tests_out_of_scope_and_why_are_optional_and_never_asked(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    for kind in ("feature", "bug", "refactor"):
        prompt = prompt_of(dry(tmp_path, "mandate", "--type", kind, "--what", "Add a badge"))
        assert field("WHY / EVIDENCE:", "none stated") in prompt
        assert field("OUT OF SCOPE:", "none stated") in prompt
        assert field("EXPECTED TESTS:", "none stated") in prompt
    asked = run_cli(
        tmp_path, "mandate", "--type", "feature", "--what", "Add a badge", "--dry-run", pretty=True
    )
    assert asked.exit_code == 0, asked.stdout
    assert "none stated" in asked.stdout
    typed = run_cli(
        tmp_path, "mandate", "--what", "Add a badge", "--dry-run", pretty=True, answer="bug\n"
    )
    assert typed.exit_code == 0, typed.stdout
    assert "TYPE" in typed.stdout
    assert "missing required fields: --type" in refusal(tmp_path, "mandate", "--what", "x")
    assert "missing required fields: --type, --what" in refusal(tmp_path, "mandate")


def test_mistakes_with_the_file_or_the_text_are_refused_before_any_launch(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    path = written(tmp_path, phased_mandate())
    empty = written(tmp_path, "  \n\n", "empty.md")
    log = written(tmp_path, "boom", "error.log")
    cases = (
        (("mandate", "-f", str(tmp_path / "absent.md")), "mandate file not found"),
        (("run", str(tmp_path / "folder")), "mandate file not found"),
        (("mandate", "-f", str(empty)), "the mandate file is empty"),
        (("mandate", "-f", str(path), "--evidence", str(log)), "--from and --evidence"),
        (("run",), "cuanta run needs a mandate file"),
        (("run", str(path), str(path)), "cuanta run takes one mandate file"),
        (("run", str(path), "-f", str(path)), "a mandate file was given as an argument and with"),
        (
            ("mandate", "-t", "bug", "--what", "w", "--evidence", str(tmp_path / "absent.log")),
            "evidence file not found",
        ),
        (("feat",), "cuanta feat needs the request"),
        (("fix", "Arregla", "--what", "otra cosa"), "the request was given twice"),
    )
    (tmp_path / "folder").mkdir()
    for args, message in cases:
        assert message in refusal(tmp_path, *args), args
    assert not fake_runner.stdins


def test_why_and_what_on_the_command_line_keep_the_whole_file(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    path = written(tmp_path, phased_mandate())
    whole = prompt_of(dry(tmp_path, "run", str(path)))
    data = dry(tmp_path, "run", str(path), "--why", "ver la fase 3")
    prompt = prompt_of(data)
    assert all(line in prompt for line in evidence_lines(phased_mandate()))
    assert prompt.index("## Fase 5") < prompt.index(ADDED) < prompt.index("ver la fase 3")
    assert len(prompt) > len(whole)
    assert data["request_notes"] == [TYPE_NOTE, WHY_NOTE]
    renamed = dry(tmp_path, "run", str(path), "--what", "corrige el intérprete")
    shown = prompt_of(renamed)
    assert field("WHAT:", "corrige el intérprete") in shown
    assert field("WHY / EVIDENCE:", REAL_TITLE) in shown
    assert all(line in shown for line in evidence_lines(phased_mandate()))
    assert renamed["request_notes"] == [TYPE_NOTE, WHAT_NOTE]
    labelled = written(tmp_path, LABELLED, "labelled.md")
    both = dry(tmp_path, "run", str(labelled), "-t", "feature", "--tests", "pytest", "--why", "x")
    told = f"--type, --tests: the command line wins over the file; {WHY_NOTE}"
    assert both["request_notes"] == [
        "fields from the file's labels: what, why, where, constraints, out of scope",
        told,
    ]
    evidence = prompt_of(both)
    assert field("WHY / EVIDENCE:", "AssertionError: 30 != 31") in evidence
    assert f"{' ' * 17}{ADDED}" in evidence
    log = written(tmp_path, "boom", "error.log")
    assert refusal(tmp_path, "run", str(path), "--evidence", str(log)) == (
        "--from and --evidence both give the evidence · "
        "put the log in the mandate file, or add a short note with --why"
    )


def fast_ready_backend(tmp_path: Path, fake_runner: FakeRunner) -> Path:
    root = copy_repo("python_backend", tmp_path)
    forge(root)
    fake_runner.responses["claude --help"] = Completed(0, CLAUDE_HELP + " --input-format", "")
    return root


@pytest.mark.parametrize("kind", ["refactor", "bug"])
def test_a_template_copied_without_its_fences_runs_like_the_fenced_copy(
    tmp_path: Path, fake_runner: FakeRunner, kind: str
) -> None:
    root = fast_ready_backend(tmp_path, fake_runner)
    request = FILLED_REQUEST.replace("TYPE:            bug", f"TYPE:            {kind}")
    fenced = dry(root, "run", str(written(tmp_path, template_copy(request), "fenced.md")))
    shown = dry(root, "run", str(written(tmp_path, unfenced_copy(request), "unfenced.md")))
    assert shown["docs"] == fenced["docs"]
    assert prompt_of(shown) == prompt_of(fenced)
    assert '"read_only":false' in prompt_of(shown)
    assert not any("read-only" in line for line in listed(shown["pack_notes"]))
    assert not fake_runner.stdins


def test_a_file_with_no_type_keeps_the_team_and_the_docs_role_when_fast_is_ready(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    root = fast_ready_backend(tmp_path, fake_runner)
    path = written(tmp_path, phased_mandate())
    data = dry(root, "run", str(path))
    shape = data["shape"]
    assert isinstance(shape, dict) and shape["shape"] != "single"
    assert data["docs"] == REAL_DOCS
    assert data["agents_file"]
    assert data["request_notes"] == [TYPE_NOTE]
    stated = dry(root, "run", str(path), "-t", "feature")
    fast = stated["shape"]
    assert isinstance(fast, dict) and fast["shape"] == "single"
    assert stated["docs"] is None
    forced = dry(root, "run", str(path), "--profile", "fast")
    chosen = forced["shape"]
    assert isinstance(chosen, dict) and chosen["shape"] == "single"
    queued = run_cli(root, "queue", "add", "-f", str(path), "--json")
    assert queued.exit_code == 0, queued.stdout
    assert not fake_runner.stdins
    fake_runner.queued["claude -p"] = [FakeStream([RESULT])]
    run_cli(root, "queue", "run", "--yes", "--json")
    launched = launches(fake_runner)
    assert len(launched) == 1 and "--agents" in launched[0]
    assert "Do not delegate" not in " ".join(launched[0])


def test_a_fast_run_lists_one_session_and_no_pure_role_lines(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    root = fast_ready_backend(tmp_path, fake_runner)
    path = written(tmp_path, phased_mandate())
    data = dry(root, "run", str(path), "-t", "feature")
    team = data["team"]
    assert isinstance(team, list)
    assert "team · single model for claude: claude-opus-5-5" in team
    assert not any("--pure" in line for line in team)
    assert not any(str(line).startswith("team · analyst") for line in team)
    pure = dry(root, "run", str(path), "-t", "feature", "--pure")
    assert "team · single model for claude: claude-opus-5-5" in listed(pure["team"])


def listed(value: object) -> list[str]:
    assert isinstance(value, list)
    return [str(item) for item in value]


ENCODINGS = [("utf-16", True), ("cp1252", False)]


@pytest.mark.parametrize(("encoding", "readable"), ENCODINGS)
def test_a_file_saved_by_windows_tools_is_read_or_refused_never_garbled(
    tmp_path: Path, fake_runner: FakeRunner, encoding: str, readable: bool
) -> None:
    forge(tmp_path)
    text = "TIPO: arreglo\nQUÉ: el total suma dos veces el envío\nPOR QUÉ: la prueba falla\n"
    path = tmp_path / "mandato.md"
    path.write_bytes(text.replace("\n", "\r\n").encode(encoding))
    log = tmp_path / "error.log"
    log.write_bytes("AssertionError: el envío\r\n".encode(encoding))
    commands: tuple[tuple[str, ...], ...] = (("run", str(path)), ("mandate", "-f", str(path)))
    evidence = ("mandate", "-t", "bug", "--what", "w", "--evidence", str(log))
    if readable:
        for command in commands:
            prompt = prompt_of(dry(tmp_path, *command))
            assert field("TYPE:", "bug") in prompt
            assert field("WHAT:", "el total suma dos veces el envío") in prompt
            assert "\x00" not in prompt and "\ufffd" not in prompt
        assert "AssertionError: el envío" in prompt_of(dry(tmp_path, *evidence))
        return
    for command in (*commands, evidence):
        message = refusal(tmp_path, *command)
        assert message.endswith("is not UTF-8 text · save it as UTF-8"), command
    added = run_cli(tmp_path, "queue", "add", "-f", str(path), "--json")
    assert added.exit_code == 1
    assert json.loads(added.stdout)["error"]["hint"] == "save it as UTF-8"
    assert not fake_runner.stdins


def test_a_mandate_file_inside_the_project_is_never_its_own_target(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    root = copy_repo("python_backend", tmp_path)
    forge(root)
    inside = written(root, phased_mandate())
    prompt = prompt_of(dry(root, "run", str(inside), "--profile", "balanced"))
    assert "card:mandato.md" not in prompt and "window:mandato.md" not in prompt
    assert "anchor:app/services/interpreter.py:287-289" in prompt
    log = written(root, "Traceback in app/services/interpreter.py:287\n", "evidencia.md")
    named = ("mandate", "-t", "bug", "--what", "sigue evidencia.md", "-p", "balanced")
    assert "card:evidencia.md" in prompt_of(dry(root, *named, "--why", "Traceback"))
    assert "card:evidencia.md" not in prompt_of(dry(root, *named, "--evidence", str(log)))
    container = Container.for_project(root)
    try:
        request = parse_mandate_file(phased_mandate()).request
        container.use_request_files((inside, tmp_path / "elsewhere.md"))
        assert container.request_files == ("mandato.md",)
        plan = container.change_plan(request)
        assert "mandato.md" not in {target.path for target in plan.edit}
        assert "mandato.md" not in plan.read
    finally:
        container.close()
    assert not fake_runner.stdins


def test_an_evidence_file_the_request_names_or_anchors_stays_in_the_change_plan(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    root = copy_repo("python_backend", tmp_path)
    forge(root)
    source = root / "app" / "services" / "interpreter.py"
    what = "the IndexError at app/services/interpreter.py:20"
    data = dry(root, "fix", what, "-p", "balanced", "--evidence", str(source))
    assert data["pack_notes"] == []
    assert "anchor:app/services/interpreter.py" not in prompt_of(data)
    assert "window:app/services/interpreter.py" not in prompt_of(data)
    readme = written(root, "# Install\n\npip install consult\n", "README.md")
    container = Container.for_project(root)
    try:
        container.use_request_files((source,))
        anchored = container.change_plan(MandateRequest(type="bug", what=what))
        assert (
            EditTarget(
                "app/services/interpreter.py",
                1.0,
                "explicit request anchor app/services/interpreter.py:20",
            )
            in anchored.edit
        )
        container.use_request_files((readme,))
        named = container.change_plan(
            MandateRequest(type="bug", what="the install steps in README.md are wrong")
        )
        assert EditTarget("README.md", 1.0, "explicit request path") in named.edit
        unnamed = container.change_plan(MandateRequest(type="bug", what="pip install fails"))
        assert "README.md" not in {target.path for target in unnamed.edit}
        assert "README.md" not in unnamed.read
    finally:
        container.close()
    plain = Container(root, Config(index_enabled=False), runner=fake_runner)
    try:
        plain.use_request_files((readme,))
        listed_only = plain.change_plan(
            MandateRequest(type="bug", what="the install steps in README.md are wrong")
        )
        assert EditTarget("README.md", 1.0, "explicit request path") in listed_only.edit
        ranked = plain.change_plan(MandateRequest(type="bug", what="pip install fails"))
        assert "README.md" not in {target.path for target in ranked.edit}
    finally:
        plain.close()
    assert not fake_runner.stdins


def test_the_type_option_takes_the_words_a_file_takes(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    assert field("TYPE:", "bug") in prompt_of(dry(tmp_path, "mandate", "-t", "fix", "--what", "x"))
    path = written(tmp_path, phased_mandate())
    assert field("TYPE:", "refactor") in prompt_of(
        dry(tmp_path, "run", str(path), "-t", "Refactor")
    )
    assert field("TYPE:", "bug") in prompt_of(dry(tmp_path, "feat", "x", "-t", "Bugfix"))
    added = run_cli(tmp_path, "queue", "add", "-t", "fix", "--what", "x", "--simple", "--json")
    assert added.exit_code == 0, added.stdout
    assert json.loads(added.stdout)["added"]["type"] == "bug"


def test_a_short_command_never_sends_a_file_type_that_is_not_a_type_to_cuanta_run(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    path = written(tmp_path, "TIPO: tarea\nQUÉ: añadir el badge del carrito\n", "tipo.md")
    for command, kind in (("feat", "feature"), ("fix", "bug")):
        data = dry(tmp_path, command, "-f", str(path))
        assert field("TYPE:", kind) in prompt_of(data)
        notes = listed(data["request_notes"])
        told = f"the file says tarea, which is not a type; cuanta {command} runs it as {kind}"
        assert told in notes
        assert not any("cuanta run FILE keeps" in line for line in notes)
    assert not fake_runner.stdins


def test_a_short_command_says_when_it_runs_a_file_as_another_type(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    path = written(tmp_path, "TYPE: bug\nWHAT: Fix the cart total\n", "bug.md")
    told = (
        "the file says bug; cuanta feat runs it as feature; cuanta run FILE keeps the file's type"
    )
    data = dry(tmp_path, "feat", "-f", str(path))
    assert field("TYPE:", "feature") in prompt_of(data)
    assert told in listed(data["request_notes"])
    kept = dry(tmp_path, "run", str(path))
    assert field("TYPE:", "bug") in prompt_of(kept)
    assert not any("cuanta feat" in line for line in listed(kept["request_notes"]))
    pinned = dry(tmp_path, "feat", "-f", str(path), "-t", "bug")
    assert field("TYPE:", "bug") in prompt_of(pinned)
    assert not any("runs it as" in line for line in listed(pinned["request_notes"]))
    same = dry(tmp_path, "fix", "-f", str(path))
    assert not any("runs it as" in line for line in listed(same["request_notes"]))


def test_a_path_from_the_home_folder_is_expanded(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    project = tmp_path / "project"
    project.mkdir()
    forge(project)
    written(home, "# Arreglar el carrito\n\nEl total suma dos veces.\n", "a.md")
    written(home, "AssertionError: 30 != 31\n", "log.txt")
    shown = prompt_of(dry(project, "run", "~/a.md"))
    assert field("WHAT:", "Arreglar el carrito") in shown
    logged = prompt_of(
        dry(project, "mandate", "-t", "bug", "--what", "w", "--evidence", "~/log.txt")
    )
    assert field("WHY / EVIDENCE:", "AssertionError: 30 != 31") in logged
    added = run_cli(project, "queue", "add", "-f", "~/a.md", "--simple", "--json")
    assert added.exit_code == 0, added.stdout
    stored = json.loads(added.stdout)["added"]["args"]
    assert Path(stored[1]).samefile(home / "a.md")


def test_a_short_command_whose_text_names_a_mandate_file_says_how_to_run_the_file(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    forge(tmp_path)
    written(tmp_path, phased_mandate())
    monkeypatch.chdir(tmp_path)
    data = dry(tmp_path, "fix", "mandato.md")
    assert field("TYPE:", "bug") in prompt_of(data)
    assert field("WHAT:", "mandato.md") in prompt_of(data)
    told = "mandato.md is a file: cuanta fix -f mandato.md runs it as the mandate"
    assert listed(data["request_notes"]) == [told]
    for words in (("the typo in mandato.md",), ("missing.md",), ("--what", "mandato.md")):
        assert listed(dry(tmp_path, "fix", *words)["request_notes"]) == []
    filed = dry(tmp_path, "feat", "-f", "mandato.md")
    assert not any("is a file" in line for line in listed(filed["request_notes"]))
    fake_runner.queued["claude -p"] = [FakeStream([RESULT])]
    launched = run_cli(tmp_path, "audit", "mandato.md", "--simple", "--yes")
    assert "mandato.md is a file: cuanta audit -f mandato.md runs it as the mandate" in (
        launched.stdout
    )


def test_a_short_command_without_text_names_the_quoting_forms(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    assert refusal(tmp_path, "fix") == (
        'cuanta fix needs the request · cuanta fix "<what to do>", '
        '--what "<text>" when it starts with -, or --from <file>'
    )


def test_queue_add_keeps_absolute_paths_so_any_folder_can_list_and_run_it(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()
    written(notes, "# Arreglar el carrito\n\nEl total suma dos veces.\n", "m1.md")
    written(notes, "AssertionError: 30 != 31\n", "log.txt")
    monkeypatch.chdir(notes)
    first = invoke(["queue", "add", "-f", "m1.md", "--simple", "--json", "--project", ".."])
    assert first.exit_code == 0, first.stdout
    stored = json.loads(first.stdout)["added"]["args"]
    assert stored[0] == "-f" and stored[2:] == ["--simple"]
    assert Path(stored[1]).is_absolute() and Path(stored[1]).samefile(notes / "m1.md")
    second = invoke(
        [
            "queue",
            "add",
            "-t",
            "bug",
            "--what",
            "Arregla el total",
            "--evidence=log.txt",
            "--simple",
            "--json",
            "--project",
            "..",
        ]
    )
    assert second.exit_code == 0, second.stdout
    shown = json.loads(second.stdout)["added"]["args"]
    evidence = next(item for item in shown if item.startswith("--evidence="))
    assert Path(evidence.removeprefix("--evidence=")).samefile(notes / "log.txt")
    monkeypatch.chdir(tmp_path)
    listed = invoke(["queue", "list", "--json", "--project", "."])
    assert listed.exit_code == 0, listed.stdout
    entries = json.loads(listed.stdout)["queue"]
    assert [entry.get("invalid") for entry in entries] == [None, None]
    fake_runner.queued["claude -p"] = [FakeStream([RESULT]), FakeStream([RESULT])]
    ran = invoke(["queue", "run", "--yes", "--json", "--project", "."])
    assert ran.exit_code == 0, ran.stdout
    assert json.loads(ran.stdout)["left"] == 0
