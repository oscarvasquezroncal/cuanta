from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuanta.adapters.instinct.jev import KEY_ENV
from cuanta.adapters.system import config_files
from cuanta.bootstrap import Container
from tests.fakes import FakeRunner
from tests.support import invoke


def test_instinct_cli_show_use_probe(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(KEY_ENV, raising=False)
    shown = json.loads(invoke(["instinct", "show", "--json", "--project", str(tmp_path)]).stdout)
    assert shown["backend"] == "heuristic"
    assert shown["source"] == "default"
    refused = invoke(["instinct", "use", "jev", "--json", "--project", str(tmp_path)])
    assert refused.exit_code == 1
    accepted = invoke(["instinct", "use", "jev", "--yes", "--json", "--project", str(tmp_path)])
    assert accepted.exit_code == 0
    assert json.loads(accepted.stdout)["consent"] == ["jev"]
    config = (tmp_path / ".cuanta" / "config.toml").read_text(encoding="utf-8")
    assert 'backend = "jev"' in config
    shown_after = json.loads(
        invoke(["instinct", "show", "--json", "--project", str(tmp_path)]).stdout
    )
    assert shown_after["source"] == "project"
    probe = json.loads(invoke(["instinct", "probe", "--json", "--project", str(tmp_path)]).stdout)
    assert probe["backend"] == "heuristic"
    assert len(probe["answers"]) == 3
    back = invoke(["instinct", "use", "heuristic", "--plain", "--project", str(tmp_path)])
    assert back.exit_code == 0


def test_global_scope_resolves_below_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_dir = tmp_path / "user-config"
    monkeypatch.setattr(config_files, "config_dir", lambda: global_dir)
    monkeypatch.delenv("CUANTA_INSTINCT", raising=False)
    monkeypatch.setattr(Container, "instinct_connection", lambda self: None)
    first = tmp_path / "first"
    second = tmp_path / "second"
    used = invoke(
        ["instinct", "use", "jev", "--global", "--yes", "--json", "--project", str(first)]
    )
    assert used.exit_code == 0
    assert json.loads(used.stdout)["scope"] == "global"
    assert not (first / ".cuanta" / "config.toml").exists()
    assert 'backend = "jev"' in (global_dir / "config.toml").read_text(encoding="utf-8")
    shown = json.loads(invoke(["instinct", "show", "--json", "--project", str(second)]).stdout)
    assert (shown["backend"], shown["source"]) == ("jev", "global")
    invoke(["instinct", "use", "heuristic", "--json", "--project", str(second)])
    overridden = json.loads(invoke(["instinct", "show", "--json", "--project", str(second)]).stdout)
    assert (overridden["backend"], overridden["source"]) == ("heuristic", "project")


@pytest.mark.parametrize(
    ("key", "base", "warning"),
    [
        ("apikey_example", "https://openrouter.ai", "apikey_ key with OpenRouter URL"),
        ("sk-or-example", "https://api.typesafe.ai", "sk-or- key with TypeSafe URL"),
    ],
)
def test_show_warns_for_key_url_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    base: str,
    warning: str,
) -> None:
    monkeypatch.setenv(KEY_ENV, key)
    monkeypatch.setenv("TYPESAFE_BASE_URL", base)
    monkeypatch.setattr(Container, "instinct_connection", lambda self: None)
    config_files.set_value(tmp_path / ".cuanta" / "config.toml", "instinct.backend", "jev")
    shown = json.loads(invoke(["instinct", "show", "--json", "--project", str(tmp_path)]).stdout)
    assert warning in shown["warning"]
