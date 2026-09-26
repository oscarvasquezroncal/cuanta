from __future__ import annotations

from pathlib import Path

import pytest

from cuanta.adapters.system.config_files import read_table, set_value
from cuanta.adapters.system.platform import config_dir
from cuanta.bootstrap import load_config
from cuanta.domain.config import Config, layer_from_env, layer_from_table, merge


def test_defaults() -> None:
    config = merge([])
    assert config == Config()
    assert config.port == 4318


def test_precedence_env_over_project_over_global() -> None:
    global_layer = layer_from_table(
        {"engine": "codex", "ui": {"theme": "light"}, "listener": {"port": 5000}}
    )
    project_layer = layer_from_table({"engine": "opencode", "test": {"command": "make test"}})
    env_layer = layer_from_env({"CUANTA_ENGINE": "claude", "CUANTA_PORT": "6000"})
    config = merge([global_layer, project_layer, env_layer])
    assert config.engine == "claude"
    assert config.theme == "light"
    assert config.port == 6000
    assert config.test_command == "make test"


def test_global_remote_consent_survives_unrelated_project_consent() -> None:
    global_layer = layer_from_table({"instinct": {"backend": "jev", "consent": ["jev"]}})
    project_layer = layer_from_table({"instinct": {"consent": ["llm"]}})
    config = merge([global_layer, project_layer])
    assert config.instinct == "jev"
    assert config.remote_consent == ("jev", "llm")


def test_invalid_values_are_ignored() -> None:
    layer = layer_from_table({"listener": {"port": "abc"}, "ui": {"emoji": "maybe"}, "unknown": 1})
    assert layer == {}


def test_lists_and_bools_coerce() -> None:
    layer = layer_from_table({"detect": {"exclude": ["fixtures", "docs"]}, "ui": {"emoji": "off"}})
    assert layer == {"exclusions": ("fixtures", "docs"), "emoji": False}
    assert layer_from_env({"CUANTA_BUDGET_USD": "2.5"}) == {"budget_usd": 2.5}


def test_max_turns_reads_settings_and_env() -> None:
    settings = layer_from_table({"runs": {"max_turns": 30}})
    environment = layer_from_env({"CUANTA_MAX_TURNS": "40"})
    assert settings == {"max_turns": 30}
    assert environment == {"max_turns": 40}
    assert merge([settings, environment]).max_turns == 40
    assert merge([]).max_turns == 0
    assert layer_from_env({"CUANTA_MAX_TURNS": "abc"}) == {}


@pytest.mark.parametrize(
    ("platform", "environ", "expected"),
    [
        ("win32", {"APPDATA": "C:/Roaming"}, Path("C:/Roaming") / "cuanta"),
        ("darwin", {"HOME": "/Users/m"}, Path("/Users/m/Library/Application Support/cuanta")),
        ("linux", {"HOME": "/home/m"}, Path("/home/m/.config/cuanta")),
        ("linux", {"HOME": "/home/m", "XDG_CONFIG_HOME": "/x"}, Path("/x/cuanta")),
    ],
)
def test_config_dir_per_platform(platform: str, environ: dict[str, str], expected: Path) -> None:
    assert config_dir(environ, platform) == expected


def test_set_value_preserves_existing_content(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('engine = "codex"\n\n[ui]\ntheme = "light"\n', encoding="utf-8")
    set_value(path, "instinct.backend", "jev")
    text = path.read_text(encoding="utf-8")
    assert 'engine = "codex"' in text
    assert read_table(path)["instinct"] == {"backend": "jev"}


def test_load_config_reads_project_file(tmp_path: Path) -> None:
    set_value(tmp_path / ".cuanta" / "config.toml", "test.runner", "pytest")
    assert load_config(tmp_path).test_runner == "pytest"


def test_corrupt_toml_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("engine = = =", encoding="utf-8")
    assert read_table(path) == {}


def test_model_tier_overrides_merge_per_model_across_layers() -> None:
    from cuanta.domain.config import layer_from_table, merge

    global_layer = layer_from_table(
        {"models": {"tiers": {"codex:gpt-5.6-luna": "standard", "opus": "frontier"}}}
    )
    project_layer = layer_from_table({"models": {"tiers": {"opus": "premium"}}})
    config = merge([global_layer, project_layer])
    assert dict(config.model_tiers) == {"codex:gpt-5.6-luna": "standard", "opus": "premium"}


def test_routing_table_merges_per_key() -> None:
    from cuanta.domain.config import layer_from_table, merge

    global_layer = layer_from_table(
        {"routing": {"mode": "auto", "engines": ["claude", "codex"], "roles": {"docs": "economy"}}}
    )
    project_layer = layer_from_table({"routing": {"roles": {"docs": "standard"}}})
    routing = dict(merge([global_layer, project_layer]).routing)
    assert routing == {"mode": "auto", "engines": ("claude", "codex"), "roles.docs": "standard"}


def test_legacy_expert_mandate_maps_to_the_one_page_layout() -> None:
    assert layer_from_table({"ui": {"expert_mandate": True}})["mandate_layout"] == "one_page"
    assert layer_from_table({"ui": {"expert_mandate": False}})["mandate_layout"] == "guided"
    chosen = layer_from_table({"ui": {"expert_mandate": True, "mandate_layout": "guided"}})
    assert chosen["mandate_layout"] == "guided"
    assert "mandate_layout" not in layer_from_table({"ui": {"mandate_layout": "wide"}})


def test_writing_the_layout_drops_the_legacy_key(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[ui]\nexpert_mandate = true\ntheme = "dark"\n', encoding="utf-8")
    set_value(path, "ui.mandate_layout", "one_page")
    table = read_table(path)
    ui = table["ui"]
    assert isinstance(ui, dict)
    assert ui == {"theme": "dark", "mandate_layout": "one_page"}
