from __future__ import annotations

from typing import Protocol

from cuanta.domain.models import ModelEntry


class ModelCatalog(Protocol):
    @property
    def engine(self) -> str: ...

    def available(self) -> bool: ...

    def list(self) -> tuple[ModelEntry, ...]: ...
