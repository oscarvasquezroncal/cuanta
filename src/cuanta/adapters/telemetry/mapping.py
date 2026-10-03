from __future__ import annotations

import json
import math
from typing import Any

from cuanta.domain.redaction import storage_json

INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1


def _int64(value: int) -> int:
    return value if INT64_MIN <= value <= INT64_MAX else 0


def _whole(value: float) -> int:
    return _int64(int(value)) if math.isfinite(value) else 0


def as_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return _int64(value)
    if isinstance(value, float):
        return _whole(value)
    if isinstance(value, str):
        try:
            return _whole(float(value))
        except ValueError:
            return 0
    return 0


def as_float(value: Any) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def as_cost(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        amount = float(value)
    except ValueError:
        return None
    return amount if math.isfinite(amount) and amount >= 0 else None


def as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


def as_text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def first(attrs: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in attrs and attrs[key] not in (None, ""):
            return attrs[key]
    return None


def parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


PROMPT_KEYS = frozenset({"prompt", "user_prompt", "prompt.text", "response"})
IDENTITY_KEYS = frozenset(
    {"user.email", "user.account_uuid", "user.account_id", "user.id", "organization.id"}
)


def raw_json(record: dict[str, Any], keep_prompts: bool) -> str:
    cleaned = dict(record)
    dropped = IDENTITY_KEYS if keep_prompts else IDENTITY_KEYS | PROMPT_KEYS
    attributes = cleaned.get("attributes")
    if isinstance(attributes, list):
        cleaned["attributes"] = [
            item
            for item in attributes
            if not (isinstance(item, dict) and item.get("key") in dropped)
        ]
    elif isinstance(attributes, dict):
        cleaned["attributes"] = {
            key: value for key, value in attributes.items() if key not in dropped
        }
    for key in dropped & set(cleaned):
        cleaned.pop(key)
    return storage_json(cleaned)
