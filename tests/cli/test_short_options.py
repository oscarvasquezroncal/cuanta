from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.main import get_command

from cuanta.cli.app import app
from cuanta.cli.commands.queue import parse_mandate
from cuanta.cli.group import hoist_globals
from tests.cli.test_engine_guarantees import forge
from tests.fakes import FakeRunner
from tests.real_run import phased_mandate
from tests.support import invoke

LONG = (
    "--from",
    "mandato.md",
    "--type",
    "bug",
    "--model",
    "claude-opus-5-5",
    "--variant",
    "ultracode",
    "--profile",
    "balanced",
    "--sandbox",
)
SHORT = (
    "-f",
    "mandato.md",
    "-t",
    "bug",
    "-m",
    "claude-opus-5-5",
    "-v",
    "ultracode",
    "-p",
    "balanced",
    "-s",
)
LONG_RUN = ("--model", "claude-opus-5-5", "--variant", "ultracode", "--profile", "balanced")
SHORT_RUN = ("-m", "claude-opus-5-5", "-v", "ultracode", "-p", "balanced", "-s")
PANELS = ("What to do:", "How to run it:", "Limits:", "Advanced:")
GROUPED = (
    (
        "--type",
        "--what",
        "--why",
        "--evidence",
        "--from",
        "--where",
        "--constraints",
        "--tests",
        "--out-of-scope",
    ),
    (
        "--engine",
        "--model",
        "--variant",
        "--profile",
        "--depth",
        "--docs",
        "--verify",
        "--sandbox",
        "--dry-run",
    ),
    ("--max-budget-usd", "--max-turns", "--max-wall"),
    (
        "--pure",
        "--from-failure",
        "--hu",
        "--shape",
        "--route",
        "--preset",
        "--role-model",
        "--override-env-model",
        "--session",
        "--keep",
        "--cross-engine",
        "--cross-budget-usd",
        "--simple",
        "--classic",
        "--help",
    ),
)
SHORT_NAMES = ("-f, --from", "-t, --type", "-m, --model", "-v, --variant", "-p, --profile")
SCREEN = 30
WIDTH = 80
QUOTING = (
    "Words that start with - are read as options: quote the request, and put -- before text "
    "that starts with -."
)


def dry(root: Path, *args: str) -> dict[str, object]:
    result = invoke([*args, "--dry-run", "--json", "--project", str(root)])
    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert isinstance(data, dict)
    return data


def effort(data: dict[str, object]) -> str:
    command = data["command"]
    assert isinstance(command, list)
    return str(command[command.index("--effort") + 1])


def same_run(data: dict[str, object]) -> tuple[object, ...]:
    return (data["model"], data["prompt"], data["team"], effort(data))


def long_option(row: str) -> str:
    return next(word.rstrip(",") for word in row.split() if word.startswith("--"))


def sections(text: str) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        if line in PANELS:
            current = line
            grouped[current] = []
        elif not line.strip():
            current = ""
        elif current:
            grouped[current].append(line)
    return grouped


def test_each_short_form_is_its_long_option() -> None:
    short = parse_mandate(SHORT)
    assert short == parse_mandate(LONG)
    assert (short.from_file, short.type, short.model) == (
        Path("mandato.md"),
        "bug",
        "claude-opus-5-5",
    )
    assert (short.variant, short.profile, short.sandbox) == ("ultracode", "balanced", True)


@pytest.mark.parametrize(
    "command",
    [
        ("mandate", "-t", "feature", "--what", "Botón de WhatsApp"),
        ("run", "mandato.md"),
        ("feat", "Botón de WhatsApp"),
        ("fix", "El botón no abre WhatsApp"),
        ("audit", "Cómo se arma el botón"),
    ],
)
def test_the_short_forms_reach_the_run_from_mandate_and_every_alias(
    tmp_path: Path, fake_runner: FakeRunner, command: tuple[str, ...]
) -> None:
    forge(tmp_path)
    (tmp_path / "mandato.md").write_text(phased_mandate(), encoding="utf-8")
    resolved = tuple(str(tmp_path / part) if part == "mandato.md" else part for part in command)
    long = dry(tmp_path, *resolved, *LONG_RUN, "--sandbox")
    short = dry(tmp_path, *resolved, *SHORT_RUN)
    assert same_run(short) == same_run(long)
    assert (short["model"], effort(short), short["sandbox"]) == ("claude-opus-5-5", "xhigh", True)


def test_queue_add_takes_the_short_forms(tmp_path: Path, fake_runner: FakeRunner) -> None:
    arguments = [
        "-t",
        "bug",
        "--what",
        "Fix the total",
        "-m",
        "claude-opus-5-5",
        "-v",
        "high",
        "-p",
        "balanced",
        "-s",
        "--simple",
    ]
    added = invoke(["queue", "add", *arguments, "--json", "--project", str(tmp_path)])
    assert added.exit_code == 0, added.stdout + added.stderr
    entry = json.loads(added.stdout)["added"]
    assert (entry["type"], entry["model"], entry["sandbox"]) == ("bug", "claude-opus-5-5", True)
    assert entry["args"] == arguments
    queued = parse_mandate(entry["args"])
    assert (queued.variant, queued.profile) == ("high", "balanced")


def test_capital_v_is_the_global_short_for_verbose_and_v_is_the_variant(
    tmp_path: Path,
) -> None:
    assert hoist_globals(["mandate", "-v", "high", "-V"]) == ["-V", "mandate", "-v", "high"]
    assert hoist_globals(["queue", "add", "-v", "low"]) == ["queue", "add", "-v", "low"]
    verbose = next(param for param in get_command(app).params if param.name == "verbose")
    assert sorted(verbose.opts) == ["--verbose", "-V"]
    for flag in ("-V", "--verbose"):
        accepted = invoke(["meow", flag, "--plain", "--project", str(tmp_path)])
        assert accepted.exit_code == 0, accepted.stdout + accepted.stderr
    refused = invoke(["meow", "-v", "--plain", "--project", str(tmp_path)])
    assert refused.exit_code == 2
    assert "-v" in refused.stdout + refused.stderr


@pytest.mark.parametrize("command", ["mandate", "run", "feat", "fix", "audit"])
def test_mandate_help_groups_the_options_and_the_common_path_fits_one_screen(
    command: str,
) -> None:
    result = invoke([command, "--help"])
    assert result.exit_code == 0, result.stdout
    text = result.stdout
    lines = text.splitlines()
    assert [line for line in lines if line in PANELS] == list(PANELS)
    grouped = sections(text)
    for panel, names in zip(PANELS, GROUPED, strict=True):
        rows = grouped[panel]
        assert all(row.startswith("  -") for row in rows), (panel, rows)
        assert [long_option(row) for row in rows] == list(names), panel
    last_limit = lines.index(grouped["Limits:"][-1])
    assert last_limit < SCREEN, last_limit
    assert max(len(line) for line in lines) <= WIDTH
    assert all(name in text for name in SHORT_NAMES)
    assert "-s, --sandbox" in text


@pytest.mark.parametrize("command", ["feat", "fix", "audit"])
def test_the_one_line_commands_say_how_to_pass_text_that_looks_like_an_option(
    command: str,
) -> None:
    result = invoke([command, "--help"])
    assert result.exit_code == 0, result.stdout
    flowing = " ".join(result.stdout.split())
    assert "Takes every mandate option." in flowing
    assert "Every option works." not in flowing
    assert QUOTING in flowing
