from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from cuanta.domain.instinct import Answer, Ask, Choice, Context, Noul, Receipt, Score
from cuanta.domain.messages import Message


class Instinct(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def remote(self) -> bool: ...

    def available(self) -> tuple[bool, Message]: ...

    def choose(
        self, question: str, options: Sequence[str], context: Context
    ) -> tuple[Choice, Receipt]: ...

    def score(
        self, question: str, low: float, high: float, context: Context
    ) -> tuple[Score, Receipt]: ...

    def noul(self, question: str, context: Context) -> tuple[Noul, Receipt]: ...


@runtime_checkable
class BatchInstinct(Protocol):
    def ask_many(
        self, asks: Sequence[Ask], context: Context
    ) -> tuple[dict[str, Answer], Receipt]: ...
