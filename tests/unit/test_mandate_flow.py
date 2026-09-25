from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from cuanta.application.mandate_flow import (
    MandateFlow,
    MissingRequestFields,
    display_command,
    models_for,
    validate,
)
from cuanta.domain.errors import DomainFailure
from cuanta.domain.mandate import MandateRequest
from cuanta.tui.i18n import Catalog
from cuanta.tui.views.mandate import parse_budget, prefill_request
from tests.tui.fakes import HAIRBALLS

if TYPE_CHECKING:
    from cuanta.ports.engine import Engine

COMPLETE = MandateRequest("bug", "fix totals", "AssertionError", out_of_scope="payments")


def test_display_command_hides_prompt_and_quotes_spaces() -> None:
    parts = ("claude", "-p", "long prompt text", "--model", "claude sonnet")
    assert (
        display_command(parts, "long prompt text") == 'claude -p "<prompt>" --model "claude sonnet"'
    )


def test_validate_names_missing_fields_and_bad_type() -> None:
    with pytest.raises(MissingRequestFields, match="What should change") as caught:
        validate(MandateRequest(type="bug"))
    assert caught.value.fields == ("what", "why", "out_of_scope")
    assert "What shows the problem?" in caught.value.localized(Catalog("en").message)
    assert "¿Qué muestra el problema?" in caught.value.localized(Catalog("es").message)
    with pytest.raises(DomainFailure, match="unknown type"):
        validate(MandateRequest("chore", "x", "y", out_of_scope="z"))
    validate(COMPLETE)


def test_missing_investigation_questions_uses_the_spanish_ui_label() -> None:
    request = MandateRequest("investigation", "Audit the landing", out_of_scope="no changes")
    with pytest.raises(MissingRequestFields) as caught:
        validate(request)
    assert caught.value.fields == ("why",)
    assert caught.value.localized(Catalog("es").message) == (
        "Faltan campos obligatorios: Preguntas que debe responder."
    )


def test_models_are_filtered_per_engine() -> None:
    models = ("claude-sonnet-5", "gpt-5", "o3")
    assert models_for("claude", models) == ("claude-sonnet-5",)
    assert models_for("codex", models) == ("gpt-5", "o3")
    assert models_for("opencode", models) == models


def test_budget_parsing() -> None:
    assert parse_budget("") == 0.0
    assert parse_budget("$2.50") == 2.5
    assert parse_budget("-1") is None
    assert parse_budget("two") is None


def test_prefill_uses_the_hairball() -> None:
    request = prefill_request(HAIRBALLS[0], "fix sig-a1b2")
    assert request.type == "bug"
    assert request.where == "src/shop/cart.py:42"
    assert "[total]" in request.why
    assert "tests/test_cart.py::test_total" in request.why


class StubEngine:
    def __init__(self) -> None:
        self.cancelled = 0

    def cancel(self) -> None:
        self.cancelled += 1


def test_stop_only_cancels_an_active_run() -> None:
    flow = MandateFlow.__new__(MandateFlow)
    flow._active = None
    assert not flow.stop()
    engine = StubEngine()
    flow._active = cast("Engine", engine)
    assert flow.stop()
    assert engine.cancelled == 1
