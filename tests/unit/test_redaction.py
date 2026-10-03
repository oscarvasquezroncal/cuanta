from __future__ import annotations

import json

from cuanta.domain.redaction import REDACTED, redact_for_storage, redact_structure, storage_json


class Shown:
    def __str__(self) -> str:
        return "owner ops@example.invalid"


def test_secret_keys_redact_their_values_and_token_counts_stay() -> None:
    redacted = redact_structure(
        {
            "api_key": "abc",
            "Authorization": "Basic abcdef",
            "client_secret": 1234567,
            "token": 12.5,
            "x-api-key": ["first", "second"],
            "flag_token": True,
            "empty_token": None,
            "input_tokens": 8,
            "cache_read_tokens": 25_559,
            "secret_name": "kept",
        }
    )
    assert redacted == {
        "api_key": REDACTED,
        "Authorization": REDACTED,
        "client_secret": REDACTED,
        "token": REDACTED,
        "x-api-key": [REDACTED, REDACTED],
        "flag_token": True,
        "empty_token": None,
        "input_tokens": 8,
        "cache_read_tokens": 25_559,
        "secret_name": "kept",
    }


def test_json_inside_a_string_is_walked_and_dumped_compactly() -> None:
    inner = json.dumps({"command": "curl -d token=abcdef123 x", "path": "café/ops@example.invalid"})
    assert redact_structure(inner) == (
        '{"command":"curl -d token=[redacted] x","path":"café/[email]"}'
    )
    assert redact_structure(" [1, true, null] ") == "[1,true,null]"
    assert redact_structure('"plain"') == '"plain"'


def test_unparsable_or_too_deep_json_text_falls_back_to_text_redaction() -> None:
    assert redact_structure('{"command": "token=abcdef123') == '{"command": "token=[redacted]'
    deep = "[" * 100_000
    assert redact_structure(deep) == deep
    nested = "[" * 5_000 + "]" * 5_000
    assert redact_structure(nested) == nested


def test_keys_and_unknown_values_are_redacted_as_text() -> None:
    assert redact_structure({"ops@example.invalid": Shown(), 3: (1, "a")}) == {
        "[email]": "owner [email]",
        "3": [1, "a"],
    }


def test_non_ascii_local_parts_are_still_emails() -> None:
    assert redact_for_storage("mail José@example.invalid now") == "mail [email] now"
    assert redact_for_storage("line one\nops@example.invalid") == "line one\n[email]"


def test_storage_json_is_always_valid_json() -> None:
    value = {
        "tool_input": json.dumps({"command": "export ANTHROPIC_API_KEY=abc123def456"}),
        "error": 'said "token=abcdef123456"\nops@example.invalid',
        "secret": 99,
    }
    stored = storage_json(value)
    assert json.loads(stored) == {
        "tool_input": '{"command":"export ANTHROPIC_API_KEY=[redacted]"}',
        "error": 'said "token=[redacted]"\n[email]',
        "secret": REDACTED,
    }
    assert stored.startswith('{"tool_input":"{\\"command\\":\\"export ')
    assert stored.endswith(',"secret":"[redacted]"}')


def _strict(text: str) -> object:
    def reject(token: str) -> object:
        raise ValueError(f"not strict JSON: {token}")

    return json.loads(text, parse_constant=reject)


def test_non_finite_floats_are_stored_as_strings() -> None:
    value = {
        "value": float("nan"),
        "values": [float("inf"), float("-inf"), 1.5],
        "nested": json.dumps({"ratio": float("nan")}),
        "token": float("inf"),
    }
    stored = storage_json(value)
    assert _strict(stored) == {
        "value": "NaN",
        "values": ["Infinity", "-Infinity", 1.5],
        "nested": '{"ratio":"NaN"}',
        "token": REDACTED,
    }
    assert redact_structure(float("nan")) == "NaN"
    assert redact_structure(2.5) == 2.5
