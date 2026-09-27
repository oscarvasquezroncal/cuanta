from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AnswerSpec:
    keywords: tuple[str, ...] = ()
    citations: tuple[str, ...] = ()
