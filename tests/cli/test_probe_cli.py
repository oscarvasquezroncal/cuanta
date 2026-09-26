from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from cuanta.ports.system import Completed
from tests.fakes import FakeRunner, FakeStream
from tests.support import invoke


def stream(read: int, written: int, cost: float, write_1h: int | None = None) -> FakeStream:
    records: list[dict[str, object]] = [
        {
            "type": "system",
            "subtype": "init",
            "session_id": "test-session",
            "model": "claude-haiku-4-5",
            "apiKeySource": "none",
            "claude_code_version": "2.1.282",
        },
        {
            "type": "assistant",
            "message": {
                "id": "message-1",
                "model": "claude-haiku-4-5",
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 1,
                    "cache_read_input_tokens": read,
                    "cache_creation_input_tokens": written,
                    "cache_creation": {
                        "ephemeral_5m_input_tokens": 0,
                        "ephemeral_1h_input_tokens": written if write_1h is None else write_1h,
                    },
                },
                "content": [{"type": "text", "text": "OK"}],
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "session_id": "test-session",
            "total_cost_usd": cost,
            "num_turns": 1,
            "result": "OK",
        },
    ]
    return FakeStream([json.dumps(record) for record in records])


def queue(runner: FakeRunner, *streams: FakeStream) -> None:
    runner.queued["claude -p"] = list(streams)


def test_cache_ttl_dry_run_spends_nothing_and_leaves_project_clean(
    tmp_path: Path, fake_runner: FakeRunner, isolated_user_dirs: Path
) -> None:
    output = invoke(["probe", "cache-ttl", "--json", "--project", str(tmp_path)])
    assert output.exit_code == 0, output.stdout
    data = json.loads(output.stdout)
    assert data["ran"] is False
    assert data["gaps_s"] == [60, 360]
    assert not any("-p" in call for call in fake_runner.calls)
    assert not (tmp_path / ".cuanta").exists()
    assert not (isolated_user_dirs / "config" / "config.toml").exists()


def test_cache_ttl_with_yes_saves_observed_lower_bound_outside_project(
    tmp_path: Path, fake_runner: FakeRunner, isolated_user_dirs: Path
) -> None:
    queue(
        fake_runner,
        stream(0, 8456, 0.01),
        stream(7116, 1338, 0.01),
        stream(7116, 1341, 0.01),
    )
    output = invoke(["--yes", "probe", "cache-ttl", "--json", "--project", str(tmp_path)])
    assert output.exit_code == 0, output.stdout
    data = json.loads(output.stdout)
    assert data["ran"] is True
    assert data["verdict"] == "lower_bound"
    assert data["ttl_s"] == 360
    assert data["declared_ttl_s"] == 3600
    assert data["saved"] is True
    assert len(data["runs"]) == 3
    assert data["spent_usd"] == 0.03
    config = tomllib.loads(
        (isolated_user_dirs / "config" / "config.toml").read_text(encoding="utf-8")
    )["cache"]
    assert config["ttl_s"] == 360
    assert config["auth"] == "subscription"
    assert config["engine_version"] == "2.1.282"
    assert config["model"] == "claude-haiku-4-5"
    assert not (tmp_path / ".cuanta").exists()
    calls = [
        (call, cwd)
        for call, cwd in zip(fake_runner.calls, fake_runner.cwds, strict=True)
        if "-p" in call
    ]
    assert len(calls) == 3
    assert all(cwd is not None and not cwd.is_relative_to(tmp_path) for _, cwd in calls)
    assert all(cwd is not None and not cwd.exists() for _, cwd in calls)
    assert all("--exclude-dynamic-system-prompt-sections" in call for call, _ in calls)
    assert all("--no-session-persistence" in call for call, _ in calls)
    assert all(call[call.index("--tools") + 1] == "Read,Glob" for call, _ in calls)
    prompts = [call[call.index("--append-system-prompt") + 1] for call, _ in calls]
    assert len(set(prompts)) == 1
    assert len(set(fake_runner.stdins)) == 1


def test_long_cold_check_saves_one_hour_inside_observed_bracket(
    tmp_path: Path, fake_runner: FakeRunner, isolated_user_dirs: Path
) -> None:
    queue(
        fake_runner,
        stream(0, 8456, 0.01),
        stream(7116, 1338, 0.01),
        stream(7116, 1341, 0.01),
        stream(0, 8456, 0.01),
    )
    output = invoke(["probe", "cache-ttl", "--long", "--yes", "--json", "--project", str(tmp_path)])
    assert output.exit_code == 0, output.stdout
    data = json.loads(output.stdout)
    assert (data["verdict"], data["ttl_s"]) == ("measured", 3600)
    assert (data["ttl_lower_s"], data["ttl_upper_s"]) == (360, 3660)
    assert len(data["runs"]) == 4
    config = tomllib.loads(
        (isolated_user_dirs / "config" / "config.toml").read_text(encoding="utf-8")
    )["cache"]
    assert config["ttl_s"] == 3600


def test_cache_ttl_does_not_save_an_unstable_result(
    tmp_path: Path, fake_runner: FakeRunner, isolated_user_dirs: Path
) -> None:
    queue(fake_runner, stream(0, 8456, 0.01), stream(0, 8456, 0.01))
    output = invoke(["probe", "cache-ttl", "--yes", "--json", "--project", str(tmp_path)])
    assert output.exit_code == 1
    data = json.loads(output.stdout)
    assert data["verdict"] == "unstable"
    assert data["saved"] is False
    assert not (isolated_user_dirs / "config" / "config.toml").exists()


def test_cache_ttl_total_cap_reserves_full_per_run_limit(
    tmp_path: Path, fake_runner: FakeRunner, isolated_user_dirs: Path
) -> None:
    queue(
        fake_runner,
        stream(0, 8456, 0.01),
        stream(7116, 1338, 0.05),
        stream(7116, 1341, 0.05),
    )
    output = invoke(["probe", "cache-ttl", "--yes", "--json", "--project", str(tmp_path)])
    assert output.exit_code == 0, output.stdout
    data = json.loads(output.stdout)
    assert data["spent_usd"] == pytest.approx(0.06)
    assert len(data["runs"]) == 2
    assert data["skipped_gaps_s"] == [360]
    assert len([call for call in fake_runner.calls if "-p" in call]) == 2


def test_cache_ttl_requires_stable_prefix_flags_before_spending(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    fake_runner.responses["claude --help"] = Completed(0, "--tools", "")
    output = invoke(["probe", "cache-ttl", "--yes", "--json", "--project", str(tmp_path)])
    assert output.exit_code != 0
    assert "--exclude-dynamic-system-prompt-sections" in output.stdout
    assert not any("-p" in call for call in fake_runner.calls)


def test_a_probe_over_its_cap_reports_unknown_warmth_without_crashing(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    queue(fake_runner, stream(0, 8456, 0.01), stream(7116, 1338, 0.06))
    output = invoke(["probe", "cache-ttl", "--yes", "--json", "--project", str(tmp_path)])
    assert output.exit_code == 1, output.stdout
    data = json.loads(output.stdout)
    assert data["verdict"] == "inconclusive"
    assert data["saved"] is False
    assert data["spent_usd"] == pytest.approx(0.07)

    assert [run["warmth"] for run in data["runs"]] == ["seed", "unknown"]
