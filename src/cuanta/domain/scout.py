from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from cuanta.domain.config import DEFAULT_SCOUT_THRESHOLD
from cuanta.domain.evidence_pack import PACK_TOKENS, pack_instruction
from cuanta.domain.mandate import INVESTIGATION, MandateRequest, Shape
from cuanta.domain.messages import Message, keyed, msg
from cuanta.domain.real_costs import FIX_TYPE, type_key
from cuanta.domain.routing import Role

SCOUT_TYPES = frozenset({"feature", FIX_TYPE})
FORCED_SHAPES = frozenset({Shape.PIPELINE, Shape.SCOUT})
DOCS_WORDS = re.compile(
    r"\b(?:docs|documentation|documentacion|documentar|documenta|documente|documenten"
    r"|readme|changelog|guides?|guias?|docstrings?|release notes|notas de la version)\b"
)
SCOUT_AGENT = "scout"
SCOUT_DESCRIPTION = (
    "Read-only scout: explores what the request needs and returns an evidence pack for the "
    "senior. Use it before the senior in the scout and senior shape."
)
SCOUT_TOOLS = ("Read", "Grep", "Glob")
SCOUT_BODY = (
    "Scout role: you never write code and never change a file.\n"
    "Explore only what the request needs, read-only: use the index tools (Cuanta find, card and "
    "page) and graph queries before Grep or Glob, open line ranges instead of whole files, and "
    "stop as soon as you can name the files the change must edit.\n"
    "Your only output is an evidence pack for the senior, who will not see your transcript: "
    "file:line facts, the few snippets the change needs, risks, the tests that cover the change "
    "and the confirmed edit set.\n"
    "Card flags such as 'generated; do-not-edit' are hints from cuanta's index, not locks: when "
    "the request asks to change a file, put it in the edit set and note the flag as a risk. "
    "Report status blocked only when the request cannot be done at all."
)


class ScoutMode(StrEnum):
    NATIVE = "native"
    LAUNCH = "launch"


class DocsMode(StrEnum):
    AUTO = "auto"
    ON = "on"
    OFF = "off"


class DocsReason(StrEnum):
    REQUESTED = "requested"
    NOT_REQUESTED = "not_requested"
    TRIAL = "trial"
    FORCED_ON = "forced_on"
    FORCED_OFF = "forced_off"
    PINNED = "pinned"


@dataclass(frozen=True, slots=True)
class DocsChoice:
    on: bool
    reason: DocsReason

    @property
    def message(self) -> Message:
        return msg(f"docs.{self.reason.value}")


@dataclass(frozen=True, slots=True)
class ShapeChoice:
    shape: Shape
    forced: bool = False
    share: float | None = None
    threshold: float = DEFAULT_SCOUT_THRESHOLD
    mode: ScoutMode = ScoutMode.NATIVE
    pinned: bool = False

    @property
    def scout(self) -> bool:
        return self.shape is Shape.SCOUT

    @property
    def launch(self) -> bool:
        return self.scout and self.mode is ScoutMode.LAUNCH

    @property
    def message(self) -> Message | None:
        if not self.scout:
            return None
        mode = keyed("scout.mode", self.mode.value)
        if self.pinned:
            return msg("scout.shape_pinned", mode=mode)
        if self.forced or self.share is None:
            return msg("scout.shape_forced", mode=mode)
        return msg(
            "scout.shape_auto",
            share=f"{self.share:.0%}",
            threshold=f"{self.threshold:.0%}",
            mode=mode,
        )

    def payload(self) -> dict[str, object]:
        return {
            "shape": self.shape.value,
            "forced": self.forced,
            "exploration_share": self.share,
            "threshold": self.threshold,
            "scout_mode": self.mode.value if self.scout else None,
            "pinned": self.pinned,
        }


def parse_scout_mode(text: str) -> ScoutMode:
    try:
        return ScoutMode(text.strip().lower())
    except ValueError:
        return ScoutMode.NATIVE


def parse_docs_mode(text: str) -> DocsMode:
    try:
        return DocsMode(text.strip().lower())
    except ValueError:
        return DocsMode.AUTO


def parse_forced_shape(text: str) -> Shape | None:
    try:
        found = Shape(text.strip().lower())
    except ValueError:
        return None
    return found if found in FORCED_SHAPES else None


def folded(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def asks_for_docs(request: MandateRequest) -> bool:
    text = " ".join((request.what, request.where, request.tests))
    return DOCS_WORDS.search(folded(text)) is not None


def has_pin(role_models: Iterable[tuple[str, str]], role: Role) -> bool:
    return any(name == role.value for name, _ in role_models)


def docs_choice(
    mode: DocsMode, request: MandateRequest, trial: bool, pinned: bool = False
) -> DocsChoice:
    if mode is DocsMode.ON:
        return DocsChoice(True, DocsReason.FORCED_ON)
    if mode is DocsMode.AUTO and not trial and asks_for_docs(request):
        return DocsChoice(True, DocsReason.REQUESTED)
    if pinned:
        return DocsChoice(True, DocsReason.PINNED)
    if mode is DocsMode.OFF:
        return DocsChoice(False, DocsReason.FORCED_OFF)
    if trial:
        return DocsChoice(False, DocsReason.TRIAL)
    return DocsChoice(False, DocsReason.NOT_REQUESTED)


def eligible(task_type: str, simple: bool = False) -> bool:
    return not simple and type_key(task_type) in SCOUT_TYPES


def exploration_share(exploration: int, total: int) -> float | None:
    if total <= 0 or exploration < 0:
        return None
    return exploration / total


def default_shape(
    task_type: str, share: float | None, threshold: float = DEFAULT_SCOUT_THRESHOLD
) -> Shape:
    if not eligible(task_type) or share is None or not math.isfinite(share):
        return Shape.PIPELINE
    return Shape.SCOUT if share > threshold else Shape.PIPELINE


def choose_shape(
    task_type: str,
    forced: Shape | None,
    share: float | None,
    threshold: float = DEFAULT_SCOUT_THRESHOLD,
    mode: ScoutMode = ScoutMode.NATIVE,
) -> ShapeChoice:
    if forced is not None:
        return ShapeChoice(forced, True, share, threshold, mode)
    return ShapeChoice(default_shape(task_type, share, threshold), False, share, threshold, mode)


def scout_refused(task_type: str, forced: Shape | None) -> bool:
    return forced is Shape.SCOUT and task_type == INVESTIGATION


def pinned_shape(role_models: Iterable[tuple[str, str]]) -> Shape | None:
    pins = tuple(role_models)
    if has_pin(pins, Role.ANALYST):
        return Shape.PIPELINE
    if has_pin(pins, Role.SCOUT):
        return Shape.SCOUT
    return None


def scout_refusal(
    task_type: str, forced: Shape | None, simple: bool, team: bool, routed: bool = True
) -> tuple[Message, str] | None:
    if forced is not Shape.SCOUT:
        return None
    if task_type == INVESTIGATION:
        return msg("scout.refused"), "use --shape single or pipeline for investigations"
    if simple:
        return msg("scout.refused_simple"), "drop --simple, or drop --shape scout"
    if not team:
        return msg("scout.refused_engine"), "use --engine claude or --engine codex"
    if not routed:
        return msg("scout.refused_routing"), "drop --route off, or drop --shape scout"
    return None


def scout_agent_prompt(budget: int = PACK_TOKENS) -> str:
    return f"{SCOUT_BODY}\n\n{pack_instruction(budget)}"


def docs_off_line() -> str:
    return "Docs are off for this run: do not invoke the docs-updater."


def scout_session_block(docs: bool) -> str:
    last = (
        "4. Invoke the docs-updater last, with the list of changed files."
        if docs
        else f"4. {docs_off_line()}"
    )
    return "\n".join(
        (
            "SCOUT AND SENIOR SHAPE (set by cuanta for this run):",
            f"1. Invoke the `{SCOUT_AGENT}` agent once, first. It explores read-only and returns "
            "an evidence pack as JSON. Do not explore the code yourself, and do not invoke the "
            "architecture-analyst: the scout replaces it.",
            "2. Invoke the senior with only the scout's evidence pack, copied verbatim, and its "
            "edit set. Add no other transcript and none of your own exploration. Tell the senior "
            "to edit only the files in the edit set and to list any other file it must edit, with "
            "the reason, in its report.",
            "3. Invoke the tester with the files the senior changed. Tell it to run "
            "`cuanta test --affected` for the affected tests and not to re-explore the code.",
            last,
        )
    )
