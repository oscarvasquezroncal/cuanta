from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from tests.support import assert_golden, invoke


def test_meow_plain_golden() -> None:
    result = invoke(["meow", "--plain"])
    assert result.exit_code == 0
    assert_golden("meow_plain.txt", result.stdout)


def test_meow_pretty_golden_without_emoji() -> None:
    result = invoke(["meow", "--no-emoji", "--theme", "dark"], pretty=True)
    assert result.exit_code == 0
    assert_golden("meow_pretty.txt", result.stdout)


def test_meow_json_is_single_document() -> None:
    result = invoke(["--json", "meow"])
    assert result.exit_code == 0
    document = json.loads(result.stdout)
    assert document["name"] == "cuanta"
    assert document["tagline"] == "every token, accounted for"
    assert document["palette"]["brand"] == {"token": "ginger", "color": "#F4A87C"}


def test_meow_light_theme_uses_light_tokens() -> None:
    result = invoke(["meow", "--json", "--theme", "light"])
    document = json.loads(result.stdout)
    assert document["theme"] == "light"
    assert document["palette"]["brand"]["color"] == "#C8693A"


def test_meow_pretty_emits_color_codes() -> None:
    result = invoke(["meow", "--theme", "dark"], pretty=True)
    assert "\x1b[" in result.stdout


def test_no_color_forces_plain() -> None:
    result = invoke(["meow"], env={"NO_COLOR": "1"}, pretty=True)
    assert "\x1b[" not in result.stdout


def test_theme_auto_reads_colorfgbg() -> None:
    result = invoke(["meow", "--json"], env={"COLORFGBG": "0;15"})
    assert json.loads(result.stdout)["theme"] == "light"


@pytest.mark.perf
def test_help_renders_fast() -> None:
    program = "\n".join(
        (
            "import sys",
            "import time",
            "started = time.perf_counter()",
            "from cuanta.cli.app import main",
            "sys.argv = ['cuanta', '--help']",
            "try:",
            "    main()",
            "except SystemExit as error:",
            "    if error.code not in (None, 0):",
            "        raise",
            "print(f'{time.perf_counter() - started:.9f}', file=sys.stderr)",
        )
    )
    command = [
        sys.executable,
        "-c",
        program,
    ]
    subprocess.run(command, capture_output=True, check=False)
    samples = []
    for _ in range(3):
        completed = subprocess.run(command, capture_output=True, check=False, text=True)
        assert completed.returncode == 0
        assert "Usage:" in completed.stdout
        samples.append(float(completed.stderr.strip()))
    budget = float(os.environ.get("CUANTA_HELP_BUDGET_S", "0.20"))
    assert min(samples) < budget
