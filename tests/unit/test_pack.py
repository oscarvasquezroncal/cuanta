from __future__ import annotations

import math
from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from cuanta.domain.pack import ContextPack, Layer, PackItem, compile_pack
from cuanta.domain.spectrum import BYTES_PER_TOKEN, estimated_tokens


def _item(
    key: str,
    text: str,
    layer: Layer = Layer.L1,
    value: float = 1.0,
    path: str = "",
    line: int = 0,
    end_line: int = 0,
) -> PackItem:
    return PackItem(key, layer, text, value, f"evidence for {key}", path, line, end_line)


def _bounded(pack: ContextPack) -> None:
    assert pack.tokens == estimated_tokens(len(pack.text.encode("utf-8")))
    assert len(pack.text.encode("utf-8")) <= pack.budget * BYTES_PER_TOKEN
    assert pack.tokens <= pack.budget


def test_pack_is_byte_stable_for_input_order_and_preserves_cache_key() -> None:
    items = (
        _item("request", "Cambiar café", Layer.VOLATILE),
        _item("window", "return café", Layer.L2, 3.0, "src/café.py", 11, 11),
        _item("card", "Café role and current facts", value=2.0),
        _item("policy", "Preserve protected files", Layer.L0),
    )
    first = compile_pack(items, 200, "index/request/depth")
    second = compile_pack(tuple(reversed(items)), 200, "index/request/depth")
    assert first == second
    assert first.text.encode("utf-8") == second.text.encode("utf-8")
    assert first.cache_key == "index/request/depth"
    assert first.text.index("[L0") < first.text.index("[L1")
    assert first.text.index("[L1") < first.text.index("[L2")
    assert first.text.index("[L2") < first.text.index("[volatile")
    assert first.excerpts == "[L2 window src/café.py:11]\nreturn café"
    assert len(first.items) == len(first.decisions) == len(items)
    assert all(decision.included for decision in first.decisions)
    _bounded(first)


def test_greedy_value_per_token_skips_large_item_and_fills_remaining_budget() -> None:
    items = (
        _item("expensive", "x" * 150, value=5.0),
        _item("valuable", "x" * 20, value=4.0),
        _item("tiny", "x", value=0.1),
        _item("request", "fix", Layer.VOLATILE),
        _item("policy", "safe", Layer.L0),
    )
    pack = compile_pack(items, 38)
    assert {item.key for item in pack.items} == {"policy", "request", "valuable", "tiny"}
    omitted = next(decision for decision in pack.decisions if decision.key == "expensive")
    assert not omitted.included
    assert omitted.reason == "omitted by budget; evidence for expensive"
    assert omitted.tokens == estimated_tokens(len(b"[L1 expensive]\n") + 150)
    _bounded(pack)


def test_equal_ratios_have_deterministic_layer_and_key_tie_breaks() -> None:
    first = _item("alpha", "same")
    second = _item("omega", "same")
    pack = compile_pack((second, first), 5)
    reversed_pack = compile_pack((first, second), 5)
    assert pack == reversed_pack
    assert tuple(item.key for item in pack.items) == ("alpha",)
    assert tuple(decision.key for decision in pack.decisions) == ("alpha", "omega")
    _bounded(pack)


def test_request_and_policy_take_priority_over_high_value_optional_items() -> None:
    items = (
        _item("policy", "Do not change secrets", Layer.L0),
        _item("request", "Change " + "é" * 200, Layer.VOLATILE),
        _item("card", "useful", value=1_000_000.0),
    )
    pack = compile_pack(items, 35)
    assert tuple(item.key for item in pack.items) == ("policy", "request")
    assert "Do not change secrets" in pack.stable_prefix
    assert pack.volatile.endswith("[request tail truncated]")
    clipped = next(item for item in pack.items if item.key == "request")
    original = next(item for item in items if item.key == "request")
    assert clipped.text != original.text
    assert next(decision for decision in pack.decisions if decision.key == "request").reason == (
        "request tail truncated; evidence for request"
    )
    _bounded(pack)


def test_request_omission_is_explicit_when_its_marker_cannot_fit() -> None:
    policy = _item("policy", "Protected **", Layer.L0)
    request = _item("request", "change " * 100, Layer.VOLATILE)
    budget = math.ceil(len(b"[L0 policy]\nProtected **") / BYTES_PER_TOKEN)
    pack = compile_pack((policy, request), budget)
    assert pack.items == (policy,)
    assert pack.volatile == ""
    decision = next(decision for decision in pack.decisions if decision.key == "request")
    assert not decision.included
    assert "cannot fit truncation marker" in decision.reason
    _bounded(pack)


def test_oversized_policy_is_rejected_instead_of_silently_clipped() -> None:
    with pytest.raises(ValueError, match="Required L0 policy exceeds"):
        compile_pack((_item("policy", "Protected **", Layer.L0),), 1)


def test_headers_unicode_and_separators_are_charged_to_the_budget() -> None:
    item = _item("rôle", "漢字 café", path="src/漢字.py")
    header = "[L1 rôle src/漢字.py]\n漢字 café"
    minimum = math.ceil(len(header.encode("utf-8")) / BYTES_PER_TOKEN)
    included = compile_pack((item,), minimum)
    omitted = compile_pack((item,), minimum - 1)
    assert included.items == (item,)
    assert omitted.items == ()
    assert omitted.text == ""
    assert omitted.tokens == 0
    _bounded(included)
    _bounded(omitted)


def test_stable_prefix_does_not_contain_volatile_request_or_l2_windows() -> None:
    base = (
        _item("policy", "Project invariants", Layer.L0),
        _item("card", "Current handling card"),
        _item("window", "x = 1\nx = 2", Layer.L2, 2.0, "src/x.py", 10, 11),
    )
    first = compile_pack((*base, _item("request", "fix one", Layer.VOLATILE)), 200)
    second = compile_pack((*base, _item("request", "fix two", Layer.VOLATILE)), 200)
    assert first.stable_prefix.encode() == second.stable_prefix.encode()
    assert "fix one" not in first.stable_prefix
    assert "x = 1" not in first.stable_prefix
    assert first.volatile != second.volatile
    assert first.excerpts == second.excerpts


def test_empty_items_keep_omission_reasons_without_consuming_budget() -> None:
    items = tuple(
        _item(layer.value, "", layer, path="src/x.py", line=1, end_line=1) for layer in Layer
    )
    pack = compile_pack(items, 1)
    assert pack.text == ""
    assert pack.items == ()
    assert all(
        not decision.included and decision.reason.startswith("empty") for decision in pack.decisions
    )
    _bounded(pack)


@pytest.mark.parametrize("value", [-1.0, math.nan, math.inf, -math.inf, True])
def test_invalid_item_values_are_rejected(value: float) -> None:
    with pytest.raises(ValueError, match="finite and nonnegative"):
        _item("bad", "text", value=value)


@pytest.mark.parametrize("budget", [0, -1, True, 1.5, math.inf, math.nan])
def test_invalid_budgets_are_rejected(budget: object) -> None:
    with pytest.raises(ValueError, match="positive integer token budget"):
        compile_pack((), cast(int, budget))


@pytest.mark.parametrize(
    ("path", "line", "end_line"),
    [
        ("src/x.py", -1, 1),
        ("src/x.py", 10, 9),
        ("src/x.py", 0, 1),
        ("", 1, 1),
        ("src/x.py", True, 2),
    ],
)
def test_invalid_line_bounds_are_rejected(path: str, line: int, end_line: int) -> None:
    with pytest.raises(ValueError, match="line bounds"):
        _item("bad", "text", path=path, line=line, end_line=end_line)


@pytest.mark.parametrize(
    ("path", "line", "end_line", "text"),
    [("", 0, 0, "one"), ("src/x.py", 0, 0, "one"), ("src/x.py", 1, 1, "one\ntwo")],
)
def test_l2_windows_require_bounded_source_lines(
    path: str, line: int, end_line: int, text: str
) -> None:
    with pytest.raises(ValueError, match="bounded path and line windows"):
        _item("window", text, Layer.L2, path=path, line=line, end_line=end_line)


def test_duplicate_keys_and_header_injection_are_rejected() -> None:
    with pytest.raises(ValueError, match="unique"):
        compile_pack((_item("same", "one"), _item("same", "two")), 100)
    with pytest.raises(ValueError, match="single-line identities"):
        _item("bad\nkey", "text")
    with pytest.raises(ValueError, match="paths must be single-line"):
        _item("bad", "text", path="src/x.py\nother")
    with pytest.raises(ValueError, match="Layer values"):
        _item("bad", "text", cast(Layer, "L1"))


def test_pack_items_decisions_and_result_are_immutable() -> None:
    item = _item("card", "text")
    pack = compile_pack((item,), 100)
    for subject, attribute, value in (
        (item, "text", "changed"),
        (pack, "budget", 1),
        (pack.decisions[0], "included", False),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(subject, attribute, value)
