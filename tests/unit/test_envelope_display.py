from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.application.cache_state import PrefixQuery
from cuanta.domain.cache import CacheClock, PrefixState, Warmth, model_starts, model_warmth
from cuanta.domain.config import Config, layer_from_table, merge
from cuanta.domain.depth import Depth, profile
from cuanta.domain.envelope import (
    EnvelopeInputs,
    RoleInput,
    RoleModel,
    Verdict,
    envelope,
    envelope_features,
    envelope_payload,
    forecast_messages,
    parts_message,
    signed_dollars,
    warmth_message,
)
from cuanta.domain.ledger import LedgerEvent, Run
from cuanta.domain.mandate import PartKind, RequestParts
from cuanta.domain.messages import english, msg
from cuanta.domain.pricing import Price
from cuanta.domain.routing import Provider, Role
from cuanta.tui.i18n import Catalog

MID = Price(1.0, 5.0, 1.25, 0.1)
DEAR = Price(5.0, 25.0, 6.25, 0.5)


def _inputs(cap: float = 10.0, cheaper: bool = False) -> EnvelopeInputs:
    senior = RoleInput(
        Role.SENIOR,
        RoleModel("senior-model", DEAR),
        cheaper=RoleModel("cheap-model", MID) if cheaper else None,
    )
    return EnvelopeInputs(
        task_type="feature",
        depth=profile(Depth.NORMAL, "feature"),
        shape="pipeline",
        provider=Provider.CLAUDE,
        roles=(RoleInput(Role.ANALYST, RoleModel("analyst-model", MID)), senior),
        cap_usd=cap,
        edit_tokens=(2_000,),
        read_tokens=(1_000,) * 4,
        warmth=0.78,
    )


def test_default_multipliers_leave_the_forecast_untouched() -> None:
    base = envelope(_inputs())
    same = envelope(replace(_inputs(), risk=1.0, exploration=1.0))
    assert same == base
    assert "risk_factor" not in envelope_features(_inputs(), base)


def test_risk_scales_the_p50_and_exploration_scales_the_read_tokens() -> None:
    base = envelope(_inputs())
    risky = envelope(replace(_inputs(), risk=1.5))
    assert base.p50_usd is not None and risky.p50_usd is not None
    assert risky.p50_usd == base.p50_usd * 1.5
    assert risky.buckets == base.buckets
    wide = replace(_inputs(), exploration=2.0)
    explored = envelope(wide)
    assert explored.buckets.exploration == base.buckets.exploration * 2
    assert explored.p50_usd is not None and explored.p50_usd > base.p50_usd
    features = envelope_features(wide, explored)
    assert features["exploration_factor"] == 2.0


def test_the_line_names_the_forecast_margin_and_cache_in_both_languages() -> None:
    result = envelope(_inputs(cap=10.0))
    assert result.verdict is Verdict.COMFORTABLE
    assert result.p50_usd is not None and result.p90_usd is not None
    assert result.margin_usd is not None
    [line] = forecast_messages(result, PrefixState.WARM)
    expected = (
        f"Forecast ${result.p50_usd:.2f} (P90 ${result.p90_usd:.2f}), "
        f"margin ${result.margin_usd:.2f}, warm cache (78%)"
    )
    assert english(line) == expected
    spanish = Catalog("es").message(line)
    assert spanish == (
        f"Pronóstico ${result.p50_usd:.2f} (P90 ${result.p90_usd:.2f}), "
        f"margen ${result.margin_usd:.2f}, caché caliente (78%)"
    )
    assert english(warmth_message(PrefixState.COLD, 0.9)) == "cold cache"
    assert Catalog("es").message(warmth_message(PrefixState.UNKNOWN, 0.0)) == "caché desconocida"


def test_uncapped_and_unpriced_forecasts_say_so() -> None:
    uncapped = envelope(_inputs(cap=0.0))
    [line] = forecast_messages(uncapped, PrefixState.COLD)
    assert english(line).endswith("no cap, cold cache")
    assert Catalog("es").message(line).endswith("sin tope, caché fría")
    unpriced = replace(_inputs(), roles=(RoleInput(Role.SENIOR, RoleModel("x", None)),))
    [unknown] = forecast_messages(envelope(unpriced), PrefixState.UNKNOWN)
    assert english(unknown) == "Forecast n/a: a role has no price, cache unknown"


def test_tight_and_infeasible_verdicts_show_the_first_suggestion() -> None:
    base = envelope(_inputs(cheaper=True))
    assert base.p50_usd is not None and base.p90_usd is not None
    tight = envelope(_inputs(cap=base.p90_usd * 0.95, cheaper=True))
    assert tight.verdict is Verdict.TIGHT
    line, warning, attempt = forecast_messages(tight, PrefixState.UNKNOWN)
    assert english(line).startswith("Forecast ")
    assert "margin -$" in english(line)
    assert english(warning) == (
        f"Tight: the P90 is within 15% of the ${tight.cap_usd:.2f} cap or over it."
    )
    assert english(attempt) == f"Try: {english(tight.suggestions[0].message)}"
    assert Catalog("es").message(attempt).startswith("Prueba: ")
    infeasible = envelope(_inputs(cap=base.p50_usd * 0.5, cheaper=True))
    assert infeasible.verdict is Verdict.INFEASIBLE
    _, said, _ = forecast_messages(infeasible, PrefixState.UNKNOWN)
    assert said == msg(
        "envelope.infeasible", p50=f"${base.p50_usd:.2f}", cap=f"${infeasible.cap_usd:.2f}"
    )
    assert Catalog("es").message(said).startswith("Inviable: el P50 ")


def test_the_parts_line_says_the_forecast_covers_one_change_in_both_languages() -> None:
    assert parts_message(RequestParts()) is None
    assert parts_message(RequestParts(PartKind.PHASE, 1)) is None
    assert parts_message(RequestParts(PartKind.STEP, 1)) is None
    phases = parts_message(RequestParts(PartKind.PHASE, 5))
    steps = parts_message(RequestParts(PartKind.STEP, 3))
    assert phases is not None and steps is not None
    assert english(phases) == "This mandate has 5 phases; the forecast covers one change"
    assert Catalog("es").message(phases) == (
        "Este mandato tiene 5 fases; el pronóstico cubre un cambio"
    )
    assert english(steps) == "This mandate has 3 steps; the forecast covers one change"
    assert Catalog("es").message(steps) == (
        "Este mandato tiene 3 pasos; el pronóstico cubre un cambio"
    )


def test_signed_dollars_puts_the_sign_before_the_symbol() -> None:
    assert signed_dollars(-0.126) == "-$0.13"
    assert signed_dollars(0.3) == "$0.30"


def test_the_payload_lists_roles_buckets_and_suggestions() -> None:
    result = envelope(_inputs(cap=0.2, cheaper=True))
    payload = envelope_payload(result)
    assert payload["verdict"] == result.verdict.value
    assert payload["buckets"] == result.buckets.payload()
    roles = payload["roles"]
    assert isinstance(roles, list) and [row["role"] for row in roles] == ["analyst", "senior"]
    suggestions = payload["suggestions"]
    assert isinstance(suggestions, list)
    assert [row["kind"] for row in suggestions] == [item.kind.value for item in result.suggestions]
    assert envelope_payload(envelope(_inputs(cap=0.0)))["cap_usd"] is None


def request(
    run_id: str, ts: str, read: int, written: int, fresh: int, model: str = "claude-sonnet-5"
) -> LedgerEvent:
    return LedgerEvent(
        run_id=run_id,
        kind="api_request",
        model=model,
        ts=ts,
        input_tokens=fresh,
        cache_read_tokens=read,
        cache_write_tokens=written,
    )


def test_warmth_is_per_model_and_learned_from_earlier_warm_starts() -> None:
    starts = (
        *model_starts([request("a", "2026-01-05T09:00:00Z", 0, 8_000, 2_000)]),
        *model_starts(
            [
                request("b", "2026-01-05T09:20:00Z", 6_000, 2_000, 2_000),
                request("b", "2026-01-05T09:25:00Z", 9_000, 500, 500),
            ]
        ),
        *model_starts(
            [request("c", "2026-01-05T09:30:00Z", 0, 9_000, 1_000, "claude-opus-5-5-20260101")]
        ),
        *model_starts([request("d", "2026-01-05T09:40:00Z", 0, 0, 3_000, "claude-haiku-4-5")]),
    )
    assert [(start.model, start.cache.share) for start in starts] == [
        ("claude-sonnet-5", 0.0),
        ("claude-sonnet-5", 0.6),
        ("claude-opus-5-5", 0.0),
    ]
    inside = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    assert model_warmth(starts, 3_600, inside) == {
        "claude-sonnet-5": Warmth(PrefixState.WARM, 0.6),
        "claude-opus-5-5": Warmth(PrefixState.WARM, 0.6),
    }
    later = model_warmth(starts, 3_600, datetime(2026, 1, 5, 10, 27, tzinfo=UTC))
    assert later["claude-sonnet-5"] == Warmth(PrefixState.COLD)
    assert later["claude-opus-5-5"].state is PrefixState.WARM
    early = datetime(2026, 1, 5, 9, 50, tzinfo=UTC)
    assert model_warmth(starts[:1], 3_600, early) == {
        "claude-sonnet-5": Warmth(PrefixState.WARM, 0.0)
    }
    assert model_warmth(starts, 0, inside) == {}


def test_the_cache_clock_trusts_the_ttl_only_for_the_measured_auth_mode() -> None:
    events = [request("r", "2026-01-05T10:00:00Z", 0, 7_800, 2_200)]
    inside = datetime(2026, 1, 5, 10, 3, tzinfo=UTC)
    ledger = MemoryLedger()
    ledger.add_run(Run("r", "mandate", engine="claude"))
    ledger.add_events(events)

    def query(api_key: bool) -> PrefixQuery:
        return PrefixQuery(
            lambda: ledger,
            lambda: True,
            300,
            lambda: int(inside.timestamp() * 1000),
            "subscription",
            lambda: api_key,
        )

    assert query(False).clock("claude") == CacheClock(300, inside)
    assert query(False).clock("codex") == CacheClock(0, inside)
    assert query(True).clock("claude") == CacheClock(0, inside)
    assert query(False).run("claude").state is PrefixState.WARM


def test_the_jev_envelope_key_is_off_by_default() -> None:
    assert Config().instinct_envelope is False
    table = {"instinct": {"envelope": True}}
    assert merge([layer_from_table(table)]).instinct_envelope is True
