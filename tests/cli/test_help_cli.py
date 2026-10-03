from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from tests.support import invoke, strip_ansi

ENGLISH = (
    "Open the app",
    "Run a mandate from a file",
    "A feature, a fix or an audit in one line",
    "See a result",
    "See costs",
    "Set limits",
)
SPANISH = (
    "Abrir la app",
    "Ejecutar un mandato desde un archivo",
    "Una funcionalidad, un arreglo o una auditoría en una línea",
    "Ver un resultado",
    "Ver los costos",
    "Fijar límites",
)
NEUTRAL = {"CUANTA_LANG": None, "LC_ALL": "en_US.UTF-8", "LC_MESSAGES": None, "LANG": None}
NARROW = {**NEUTRAL, "COLUMNS": "80"}


def shown(root: Path, *args: str, env: Mapping[str, str | None] | None = None) -> str:
    result = invoke([*args, "--plain", "--project", str(root)], env={**NEUTRAL, **(env or {})})
    assert result.exit_code == 0, result.stdout
    return result.stdout


def configure(root: Path, language: str) -> None:
    (root / ".cuanta").mkdir(exist_ok=True)
    (root / ".cuanta" / "config.toml").write_text(
        f'[ui]\nlanguage = "{language}"\n', encoding="utf-8"
    )


def screen(root: Path, language: str, pretty: bool) -> list[str]:
    style = [] if pretty else ["--plain"]
    result = invoke(
        ["help", "--lang", language, *style, "--project", str(root)], env=NARROW, pretty=pretty
    )
    assert result.exit_code == 0, result.stdout
    return [line.rstrip() for line in strip_ansi(result.stdout).splitlines()]


@pytest.mark.parametrize("language", ["en", "es"])
def test_the_help_screen_fits_an_eighty_by_twenty_four_console(
    tmp_path: Path, language: str
) -> None:
    plain = screen(tmp_path, language, pretty=False)
    pretty = screen(tmp_path, language, pretty=True)
    for lines in (plain, pretty):
        assert len(lines) <= 24, lines
        assert max(len(line) for line in lines) <= 80, max(lines, key=len)
    assert len(pretty) == len(plain), pretty


@pytest.mark.parametrize("command", ["help", "ayuda"])
def test_help_shows_the_six_things_people_do_on_one_screen(tmp_path: Path, command: str) -> None:
    text = shown(tmp_path, command, "--lang", "en")
    positions = [text.index(label) for label in ENGLISH]
    assert positions == sorted(positions)
    assert "cuanta run mandate.md" in text
    assert "cuanta mandate -f mandate.md" in text
    assert 'cuanta feat "' in text and 'cuanta fix "' in text and 'cuanta audit "' in text
    assert "cuanta runs show" in text
    assert "cuanta costs" in text
    assert "--max-budget-usd" in text and "--max-turns" in text and "--max-wall" in text
    assert "[limits]" in text and "Limits switch" in text
    assert "cuanta <command> --help" in text


def test_ayuda_speaks_spanish_when_asked(tmp_path: Path) -> None:
    text = shown(tmp_path, "ayuda", "--lang", "es")
    positions = [text.index(label) for label in SPANISH]
    assert positions == sorted(positions)
    assert "cuanta run mandato.md" in text
    assert "interruptor Límites" in text
    assert "cuanta <comando> --help" in text
    assert not any(label in text for label in ENGLISH)


def test_the_language_follows_the_flag_then_the_config_then_the_system(tmp_path: Path) -> None:
    assert SPANISH[0] in shown(tmp_path, "help", env={"LC_ALL": "es_PE.UTF-8"})
    assert ENGLISH[0] in shown(tmp_path, "ayuda", env={"LC_ALL": "en_US.UTF-8"})
    assert SPANISH[0] in shown(tmp_path, "help", env={"CUANTA_LANG": "es"})
    configure(tmp_path, "es")
    assert SPANISH[0] in shown(tmp_path, "help")
    assert ENGLISH[0] in shown(tmp_path, "help", "--lang", "en")


def test_help_as_json_lists_the_six_items(tmp_path: Path) -> None:
    result = invoke(["help", "--lang", "es", "--json", "--project", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data["language"] == "es"
    labels = [str(item["label"]) for item in data["items"]]
    assert len(labels) == len(SPANISH)
    assert all(label.startswith(prefix) for label, prefix in zip(labels, SPANISH, strict=True))
    assert all(item["command"].startswith("cuanta") for item in data["items"])
    assert [item["key"] for item in data["items"]] == [
        "app",
        "file",
        "oneline",
        "result",
        "costs",
        "limits",
    ]


def test_typer_help_still_works_and_lists_the_new_commands(tmp_path: Path) -> None:
    root = invoke(["--help"])
    assert root.exit_code == 0
    assert "Usage:" in root.stdout
    narrow = invoke(["--help"], env={"COLUMNS": "80"})
    runs = next(line for line in narrow.stdout.splitlines() if line.lstrip().startswith("runs "))
    assert runs.split(maxsplit=1)[1] == "Runs cuanta launched, with their stored reports."
    for name in ("run", "feat", "fix", "audit", "help", "ayuda", "mandate"):
        assert f" {name} " in root.stdout, name
    run = invoke(["run", "--help"])
    assert run.exit_code == 0
    assert "FILE" in run.stdout
    for option in ("--from", "--max-wall", "--profile", "--docs", "--sandbox", "--dry-run"):
        assert option in run.stdout, option
    feat = invoke(["feat", "--help"])
    assert feat.exit_code == 0
    assert "TEXT" in feat.stdout and "--max-budget-usd" in feat.stdout
    mandate = invoke(["mandate", "--help"])
    assert "-f, --from" in mandate.stdout and "-t, --type" in mandate.stdout
