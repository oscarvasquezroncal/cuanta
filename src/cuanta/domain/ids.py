from __future__ import annotations

import re

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
RUN_ID_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def encode_base32(value: int, length: int) -> str:
    characters = []
    for _ in range(length):
        value, remainder = divmod(value, 32)
        characters.append(CROCKFORD[remainder])
    return "".join(reversed(characters))


def make_run_id(timestamp_ms: int, entropy: bytes) -> str:
    if len(entropy) < 10:
        raise ValueError("entropy must be at least 10 bytes")
    random_part = int.from_bytes(entropy[:10], "big")
    return encode_base32(timestamp_ms & ((1 << 48) - 1), 10) + encode_base32(random_part, 16)


def is_run_id(value: str) -> bool:
    return bool(RUN_ID_PATTERN.match(value))


def run_id_timestamp_ms(run_id: str) -> int:
    value = 0
    for character in run_id[:10]:
        value = value * 32 + CROCKFORD.index(character)
    return value


def traceparent(trace_id: bytes, span_id: bytes) -> str:
    return f"00-{trace_id.hex()}-{span_id.hex()}-01"


def trace_id_of(traceparent_value: str) -> str:
    parts = traceparent_value.split("-")
    return parts[1] if len(parts) == 4 else ""
