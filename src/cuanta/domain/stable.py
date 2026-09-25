from __future__ import annotations

import hashlib
import json

DIGEST_CHARS = 16


def stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def content_name(folder: str, stem: str, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:DIGEST_CHARS]
    return f"{folder}/{stem}-{digest}.json"
