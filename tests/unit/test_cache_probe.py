from __future__ import annotations

import json
from dataclasses import replace

import pytest

from cuanta.adapters.engines.claude_code import build_command
from cuanta.adapters.engines.claude_stream import parse_line
from cuanta.adapters.system.clock import FixedClock, SystemClock
from cuanta.application.cache_probe import first_step
from cuanta.domain.cache_probe import (
    CacheReading,
    Verdict,
    Warmth,
    access_of,
    decide,
    parse_gaps,
    plan_ceiling,
    worst_run_cost,
)
from cuanta.domain.engine import EngineRequest, ModelUsage, SessionStarted, StepUsage
from cuanta.domain.errors import DomainFailure
from cuanta.domain.models import Access
from cuanta.domain.pricing import Price


def reading(
    index: int,
    gap_s: float,
    read: int,
    written: int,
    write_1h: int = 0,
    write_5m: int = 0,
) -> CacheReading:
    return CacheReading(
        index=index,
        gap_s=gap_s,
        run_id=f"run-{index}",
        ok=True,
        has_usage=True,
        cache_read=read,
        cache_write=written,
        write_5m=write_5m,
        write_1h=write_1h,
        fresh_input=0,
        cost_usd=0.01,
        engine_version="2.1.282",
        api_key_source="none",
        local_date="2026-09-25",
    )


def verdict(seed: CacheReading, *items: CacheReading) -> tuple[Verdict, int, tuple[Warmth, ...]]:
    result = decide(seed, items, (), 0.04, "claude-haiku-4-5", "2026-09-25", ("Read", "Glob"))
    return result.verdict, result.ttl_s, result.warmth


def test_existing_subscription_probe_brackets_one_hour_ttl() -> None:
    seed = reading(0, 0, 0, 8456, write_1h=8456)
    one_minute = reading(1, 60, 7116, 1338, write_1h=1338)
    six_minutes = reading(2, 360, 7116, 1341, write_1h=1341)
    sixty_one_minutes = reading(3, 3660, 0, 8456, write_1h=8456)
    result = decide(
        seed,
        (one_minute, six_minutes, sixty_one_minutes),
        (),
        0.0452762,
        "claude-haiku-4-5",
        "2026-09-25",
        ("Read", "Glob"),
    )
    assert result.verdict is Verdict.MEASURED
    assert (result.lower_s, result.upper_s, result.ttl_s) == (360, 3660, 3600)
    assert result.warmth == (Warmth.WARM, Warmth.WARM, Warmth.COLD)
    assert result.auth is Access.SUBSCRIPTION
    assert result.saveable


def test_five_minute_ttl_is_measured_between_one_and_six_minutes() -> None:
    seed = reading(0, 0, 0, 8000, write_5m=8000)
    assert verdict(seed, reading(1, 60, 7000, 1000), reading(2, 360, 0, 8000))[:2] == (
        Verdict.MEASURED,
        300,
    )


def test_warm_only_checks_save_the_longest_observed_gap() -> None:
    seed = reading(0, 0, 0, 8456, write_1h=8456)
    result = verdict(seed, reading(1, 60, 7116, 1338), reading(2, 360, 6405, 2051))
    assert result == (Verdict.LOWER_BOUND, 360, (Warmth.WARM, Warmth.WARM))


def test_zero_reference_or_uncacheable_seed_is_not_saved() -> None:
    seed = reading(0, 0, 0, 8456, write_1h=8456)
    assert verdict(seed, reading(1, 60, 0, 8456))[0] is Verdict.UNSTABLE
    assert verdict(replace(seed, cache_write=0), reading(1, 60, 0, 0))[0] is Verdict.UNCACHEABLE


def test_failed_or_changed_readings_are_unknown() -> None:
    seed = reading(0, 0, 0, 8456, write_1h=8456)
    warm = reading(1, 60, 7116, 1338)
    changed = replace(reading(2, 360, 0, 8456), engine_version="2.2.0")
    failed = replace(reading(3, 3660, 0, 0), ok=False, has_usage=False)
    assert verdict(seed, warm, changed, failed)[2] == (
        Warmth.WARM,
        Warmth.UNKNOWN,
        Warmth.UNKNOWN,
    )
    assert (
        verdict(seed, warm, replace(changed, engine_version="2.1.282", local_date="2026-09-26"))[2][
            -1
        ]
        is Warmth.UNKNOWN
    )


def test_parse_gaps_requires_an_early_reference() -> None:
    assert parse_gaps("6m,1m,60") == (60, 360)
    assert parse_gaps("1m,1h") == (60, 3600)
    for bad in ("", "0", "x", "1m,", "6m,61m"):
        with pytest.raises(DomainFailure):
            parse_gaps(bad)


def test_auth_mapping_and_one_hour_worst_cost() -> None:
    assert access_of("none") is Access.SUBSCRIPTION
    assert access_of("environment") is Access.API
    assert access_of("") is Access.UNKNOWN
    price = Price(input=1.0, output=5.0, cache_write=1.25, cache_read=0.1)
    assert worst_run_cost(price, 8000) == pytest.approx((16000 + 80) / 1_000_000)
    assert worst_run_cost(None, 8000) is None


def test_probe_plan_reserves_full_per_run_cap_for_each_gap() -> None:
    price = Price(input=0.5, output=5.0, cache_write=1.25, cache_read=0.1)
    plan = plan_ceiling(price, (60, 360), 0.10, 0.05)
    assert plan.worst_run_usd is not None and plan.worst_run_usd < plan.per_run_usd
    assert plan.worst_case_usd == pytest.approx(0.15)
    assert plan.affordable_gaps_s == (60,)


def test_clock_sleep_moves_virtual_time_and_accepts_zero() -> None:
    fixed = FixedClock()
    before = fixed.monotonic()
    fixed.sleep(60)
    assert fixed.monotonic() - before >= 60
    before_system = SystemClock().monotonic()
    SystemClock().sleep(0)
    assert SystemClock().monotonic() >= before_system


def test_first_main_thread_usage_and_stream_metadata_are_kept() -> None:
    events = [
        StepUsage(ModelUsage("model", cache_read_tokens=999), parent_tool_use_id="tool"),
        StepUsage(ModelUsage("model", cache_read_tokens=7116), message_id="first"),
        StepUsage(ModelUsage("model", cache_read_tokens=0), message_id="second"),
    ]
    assert first_step(events) == events[1]
    init = parse_line(
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "session",
                "model": "model",
                "apiKeySource": "none",
                "claude_code_version": "2.1.282",
            }
        )
    )[0]
    assert isinstance(init, SessionStarted)
    assert (init.api_key_source, init.engine_version) == ("none", "2.1.282")
    usage = parse_line(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "id": "first",
                    "model": "model",
                    "usage": {
                        "cache_read_input_tokens": 7116,
                        "cache_creation_input_tokens": 1338,
                        "cache_creation": {
                            "ephemeral_5m_input_tokens": 0,
                            "ephemeral_1h_input_tokens": 1338,
                        },
                    },
                },
            }
        )
    )[0]
    assert isinstance(usage, StepUsage)
    assert (usage.write_5m_tokens, usage.write_1h_tokens) == (0, 1338)


def test_stable_prefix_flags_preserve_lean_settings_tail() -> None:
    request = EngineRequest(
        prompt="OK",
        cwd=".",
        env={},
        tools=("Read", "Glob"),
        stable_prefix=True,
        persist_session=False,
        mcp_config="mcp.json",
        settings_file="settings.json",
    )
    command = build_command(("claude",), request)
    assert command.index("--exclude-dynamic-system-prompt-sections") < command.index(
        "--strict-mcp-config"
    )
    assert command.index("--no-session-persistence") < command.index("--strict-mcp-config")
    assert command[-5:] == [
        "--strict-mcp-config",
        "--mcp-config",
        "mcp.json",
        "--settings",
        "settings.json",
    ]
