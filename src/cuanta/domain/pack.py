from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum

from cuanta.domain.anchors import AnchorCheck
from cuanta.domain.capsules import Level
from cuanta.domain.spectrum import BYTES_PER_TOKEN, estimated_tokens


class Layer(StrEnum):
    L0 = Level.L0
    L1 = Level.L1
    L2 = Level.L2
    VOLATILE = "volatile"


_ORDER = {Layer.L0: 0, Layer.L1: 1, Layer.L2: 2, Layer.VOLATILE: 3}
_TRUNCATED = "[request tail truncated]"


@dataclass(frozen=True, slots=True)
class PackItem:
    key: str
    layer: Layer
    text: str
    value: float
    reason: str
    path: str = ""
    line: int = 0
    end_line: int = 0
    write: bool = False
    priority: int = 0

    def __post_init__(self) -> None:
        if not self.key.strip() or any(character in self.key for character in "\n\r\x00"):
            raise ValueError("Pack item keys must be nonempty single-line identities")
        if not isinstance(self.layer, Layer):
            raise ValueError("Pack item layers must be Layer values")
        if isinstance(self.value, bool) or not math.isfinite(self.value) or self.value < 0:
            raise ValueError("Pack item values must be finite and nonnegative")
        if any(character in self.path for character in "\n\r\x00"):
            raise ValueError("Pack item paths must be single-line identities")
        if (
            isinstance(self.line, bool)
            or isinstance(self.end_line, bool)
            or not isinstance(self.line, int)
            or not isinstance(self.end_line, int)
            or self.line < 0
            or self.end_line < self.line
            or (self.line == 0 and self.end_line != 0)
            or (self.line > 0 and not self.path)
        ):
            raise ValueError("Pack item line bounds must describe a valid path window")
        if self.layer == Layer.L2 and (
            not self.path
            or self.line == 0
            or len(self.text.splitlines()) > self.end_line - self.line + 1
        ):
            raise ValueError("L2 excerpts require bounded path and line windows")
        if (
            isinstance(self.priority, bool)
            or not isinstance(self.priority, int)
            or self.priority < 0
        ):
            raise ValueError("Pack item priorities must be nonnegative integers")


@dataclass(frozen=True, slots=True)
class PackDecision:
    key: str
    path: str
    layer: Layer
    included: bool
    reason: str
    tokens: int
    line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class ContextPack:
    stable_prefix: str
    excerpts: str
    volatile: str
    items: tuple[PackItem, ...]
    decisions: tuple[PackDecision, ...]
    tokens: int
    budget: int
    cache_key: str = ""
    anchors: tuple[AnchorCheck, ...] = ()

    @property
    def text(self) -> str:
        return "\n\n".join(
            part for part in (self.stable_prefix, self.excerpts, self.volatile) if part
        )


def _bytes(text: str) -> int:
    return len(text.encode("utf-8"))


def _identity(item: PackItem) -> tuple[int, str, str, int, int, str]:
    return _ORDER[item.layer], item.key, item.path, item.line, item.end_line, item.text


def _render(item: PackItem) -> str:
    location = item.path
    if item.line:
        location += f":{item.line}"
        if item.end_line != item.line:
            location += f"-{item.end_line}"
    label = f" {location}" if location else ""
    return f"[{item.layer.value} {item.key}{label}]\n{item.text}"


def _join(items: tuple[PackItem, ...]) -> str:
    return "\n\n".join(_render(item) for item in sorted(items, key=_identity))


def _size(items: tuple[PackItem, ...]) -> int:
    return sum(_bytes(_render(item)) for item in items) + max(0, len(items) - 1) * 2


def _decision(item: PackItem, included: bool, outcome: str) -> PackDecision:
    reason = f"{outcome}; {item.reason}" if item.reason else outcome
    return PackDecision(
        item.key,
        item.path,
        item.layer,
        included,
        reason,
        estimated_tokens(_bytes(_render(item))),
        item.line,
        item.end_line,
    )


def _truncate(item: PackItem, available: int) -> PackItem | None:
    header_size = _bytes(_render(replace(item, text="")))
    allowance = available - header_size - _bytes(_TRUNCATED)
    if allowance < 0:
        return None
    prefix = item.text.encode("utf-8")[: max(0, allowance - 1)].decode("utf-8", errors="ignore")
    text = prefix + "\n" + _TRUNCATED if prefix else _TRUNCATED
    return replace(item, text=text)


def compile_pack(items: tuple[PackItem, ...], budget: int, cache_key: str = "") -> ContextPack:
    if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
        raise ValueError("A context pack requires a positive integer token budget")
    if len({item.key for item in items}) != len(items):
        raise ValueError("Pack item keys must be unique")
    maximum = int(budget * BYTES_PER_TOKEN)
    ordered = tuple(sorted(items, key=_identity))
    selected = tuple(item for item in ordered if item.layer == Layer.L0 and item.text)
    if _size(selected) > maximum:
        raise ValueError("Required L0 policy exceeds the context pack budget")
    decisions = {
        item.key: _decision(item, bool(item.text), "required policy" if item.text else "empty")
        for item in ordered
        if item.layer == Layer.L0
    }
    for item in ordered:
        if item.layer != Layer.VOLATILE:
            continue
        available = maximum - _size(selected) - (2 if selected else 0)
        if not item.text:
            decisions[item.key] = _decision(item, False, "empty")
        elif _bytes(_render(item)) <= available:
            selected += (item,)
            decisions[item.key] = _decision(item, True, "required request")
        elif (clipped := _truncate(item, available)) is not None:
            selected += (clipped,)
            decisions[item.key] = _decision(clipped, True, "request tail truncated")
        else:
            decisions[item.key] = _decision(item, False, "request cannot fit truncation marker")
    optional = sorted(
        (item for item in ordered if item.layer in {Layer.L1, Layer.L2}),
        key=lambda item: (
            -item.priority,
            -item.value / max(1, estimated_tokens(_bytes(_render(item)) + 2)),
            _identity(item),
        ),
    )
    for item in optional:
        if not item.text:
            decisions[item.key] = _decision(item, False, "empty")
        elif _size((*selected, item)) <= maximum:
            selected += (item,)
            decisions[item.key] = _decision(item, True, "selected by value/token")
        else:
            decisions[item.key] = _decision(item, False, "omitted by budget")
    selected = tuple(sorted(selected, key=_identity))
    stable = _join(tuple(item for item in selected if item.layer in {Layer.L0, Layer.L1}))
    excerpts = _join(tuple(item for item in selected if item.layer == Layer.L2))
    volatile = _join(tuple(item for item in selected if item.layer == Layer.VOLATILE))
    text = "\n\n".join(part for part in (stable, excerpts, volatile) if part)
    return ContextPack(
        stable,
        excerpts,
        volatile,
        selected,
        tuple(decisions[item.key] for item in ordered),
        estimated_tokens(_bytes(text)),
        budget,
        cache_key,
    )
