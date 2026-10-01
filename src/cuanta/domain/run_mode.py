from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from cuanta.domain.config import Config
from cuanta.domain.mandate import INVESTIGATION, Shape

CLASSIC = "classic"
V5 = "v5"
RUN_MODES = (CLASSIC, V5)
MODE_KEY = "mode"
CLASSIC_DOCS = "on"
CLASSIC_NOTE = (
    "classic mode: pipeline shape, docs on, read discipline off, governor off "
    "(the USD margin stays)"
)


def classic_config(config: Config) -> Config:
    return replace(
        config,
        docs_mode=CLASSIC_DOCS,
        read_discipline=False,
        pipeline_read_discipline=False,
        governor=False,
        implementation_profile="balanced",
    )


def classic_shape(shape: str, task_type: str, simple: bool) -> str:
    if shape or simple or task_type == INVESTIGATION:
        return shape
    return Shape.PIPELINE.value


def classic_meta(mode: str) -> dict[str, object]:
    return {MODE_KEY: CLASSIC} if mode == CLASSIC else {}


def run_mode(meta: Mapping[str, object]) -> str:
    return CLASSIC if meta.get(MODE_KEY) == CLASSIC else V5
