from __future__ import annotations

import math
import re
import unicodedata
from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from cuanta.domain.config import DEFAULT_SCOUT_THRESHOLD
from cuanta.domain.evidence_pack import PACK_TOKENS, pack_instruction
from cuanta.domain.intake import ERROR_LINE, OUT_OF_SCOPE_AT
from cuanta.domain.mandate import INVESTIGATION, NUMBERED_PART, MandateRequest, Shape
from cuanta.domain.messages import Message, keyed, msg
from cuanta.domain.models import ModelEntry, Tier, tier_rank
from cuanta.domain.real_costs import FIX_TYPE, type_key
from cuanta.domain.routing import Role
from cuanta.domain.sandbox import DOC_DIRS

SCOUT_TYPES = frozenset({"feature", FIX_TYPE})
FORCED_SHAPES = frozenset({Shape.PIPELINE, Shape.SCOUT})
DOCS_WORDS = re.compile(
    r"(?<![\w\\/.])(?:docs|documentation|documentacion|documentar|documenta|documente"
    r"|documenten|readme|changelog|guides?|guias?|docstrings?|release notes"
    r"|notas de la version)(?![\w\\/])"
)
DOCS_AGENT = "docs-updater"
DOCS_FIELDS = ("what", "where", "tests", "why")
EVIDENCE_FIELD = "why"
HEADING = re.compile(
    r"^[ \t>]*(?:#{1,6}(?:[ \t]|$)"
    r"|(?:[-*+][ \t]+)?(?:\*\*|__)[^*_\n]+(?:\*\*|__)[ \t]*:?[ \t]*$)"
)
LOG_FRAME = re.compile(r"^[ \t]+(?:at[ \t]|File[ \t]|(?:[A-Za-z]:)?[\w.~\\/-]*[\\/][\w.-]+:\d+)")
CLAUSE_END = re.compile(r"[,;()]|[.!?](?=\s|$)|\s[-–—]+\s")
SENTENCE_STOP = re.compile(r"[.!?](?=\s|$)")
SCOPE_LABEL = re.compile(r"\bout\s+of\s+scope\b|\bfuera\s+de\s+alcance\b")
NEGATION = re.compile(r"no|ni|nunca|jamas|sin|not|never|nor|without|dont|\w+n['’]t")
WITHOUT = re.compile(r"sin|without")
CHANGE_VERB = re.compile(
    r"updat(?:e|es|ing)|touch(?:es|ing)?|modif(?:y|ies|ying)|chang(?:e|es|ing)"
    r"|(?:re)?writ(?:e|es|ing)|add(?:s|ing)?|(?:re)?generat(?:e|es|ing)|creat(?:e|es|ing)"
    r"|edit(?:s|ing|a|as|an|e|es|en|ar|ando)?|invok(?:e|es|ing)|includ(?:e|es|ing)"
    r"|run(?:s|ning)?|call(?:s|ing)?|us(?:e|es|ing|a|as|an|en|ar|ando)"
    r"|actualiz(?:a|as|an|ar|ando|acion|aciones)|actualic(?:e|es|en)"
    r"|toc(?:a|as|an|ar|ando)|toqu(?:e|es|en)"
    r"|modific(?:a|as|an|ar|ando|acion|aciones)|modifiqu(?:e|es|en)"
    r"|cambi(?:a|as|an|e|es|en|ar|ando|o|os)|escrib(?:e|es|en|a|as|an|ir|iendo)"
    r"|agreg(?:a|as|an|ar|ando)|agregu(?:e|es|en)|anad(?:e|es|en|a|as|an|ir|iendo)"
    r"|gener(?:a|as|an|e|es|en|ar|ando)|cre(?:a|as|an|e|es|en|ar|ando)"
    r"|invoc(?:a|as|an|ar|ando)|invoqu(?:e|es|en)|inclu(?:ye|yes|yen|ya|yas|yan|ir|yendo)"
    r"|ejecut(?:a|as|an|e|es|en|ar|ando)|llam(?:a|as|an|e|es|en|ar|ando)"
    r"|lanz(?:a|as|an|ar|ando)|lanc(?:e|es|en)"
)
SCOPE_BREAK = re.compile(
    r"and|y|e|but|pero|sino|then|luego|despues|also|tambien|instead|ademas|plus"
)
ALTERNATIVE = re.compile(r"or|o|u")
NOUN_LEAD = re.compile(
    r"the|a|an|any|its|their|our|this|these|new|more|extra|additional|further"
    r"|el|la|los|las|lo|un|una|unos|unas|su|sus|ningun|ninguna|ninguno|este|esta|estos|estas"
    r"|nuevo|nueva|nuevos|nuevas|mas|adicional|adicionales"
)
NEGATION_WORD = re.compile(r"[\w'’]+")
VERB_WINDOW = 5
NEGATION_GAP = 2
NEGATION_REACH = 120
FORGET = re.compile(r"olvid|forget")
CONDITIONS = frozenset({"si", "if", "unless", "cuando", "when"})
TERM_LIMIT = 60
TERM_TRIM = ".,;:"
URLS = re.compile(
    r"(?:\b(?:docs|documentation|documentacion)[ \t]*:[ \t]*)?"
    r"(?:\b[a-z][a-z0-9+.-]{0,31}://|\bwww\.)\S+"
)
DOCS_AGENT_NAME = re.compile(rf"(?<![\w-]){re.escape(DOCS_AGENT)}(?![\w-])")
DOC_DIR_NAMES = "|".join(re.escape(item.rstrip("/")) for item in DOC_DIRS)
DOCS_PATH = re.compile(
    r"(?<![\w.\\/-])(?:"
    rf"(?:\.[\\/])?(?:{DOC_DIR_NAMES})[\\/][\w.\\/-]*"
    r"|(?:[\w.-]+[\\/])+(?:changelog|readme)(?:\.[a-z0-9]+)*(?![\w-])"
    r"|(?:changelog|readme)(?:\.[a-z0-9]+)+(?![\w-])"
    r")"
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
    AGENT = "agent"
    PATH = "path"
    FLAG_ON = "flag_on"
    FLAG_OFF = "flag_off"


DOCS_RULES = (
    (DocsReason.AGENT, DOCS_AGENT_NAME),
    (DocsReason.PATH, DOCS_PATH),
    (DocsReason.REQUESTED, DOCS_WORDS),
)
MATCHED_REASONS = frozenset(rule for rule, _ in DOCS_RULES)
FLAG_REASONS = frozenset({DocsReason.FLAG_ON, DocsReason.FLAG_OFF})


@dataclass(frozen=True, slots=True)
class DocsChoice:
    on: bool
    reason: DocsReason
    field: str = ""
    term: str = ""

    @property
    def message(self) -> Message:
        if self.term and self.reason in MATCHED_REASONS:
            return msg(
                f"docs.{self.reason.value}_in",
                term=self.term,
                field=keyed("docs.field", self.field),
            )
        return msg(f"docs.{self.reason.value}")


@dataclass(frozen=True, slots=True)
class DocsMatch:
    rule: DocsReason
    field: str
    term: str


@dataclass(frozen=True, slots=True)
class _Marks:
    sentences: tuple[int, ...]
    clauses: tuple[int, ...]
    labels: tuple[int, ...]
    scoped_starts: tuple[int, ...]
    scoped_ends: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ShapeChoice:
    shape: Shape
    forced: bool = False
    share: float | None = None
    threshold: float = DEFAULT_SCOUT_THRESHOLD
    mode: ScoutMode = ScoutMode.NATIVE
    pinned: bool = False
    pure: str = ""

    @property
    def scout(self) -> bool:
        return self.shape is Shape.SCOUT

    @property
    def launch(self) -> bool:
        return self.scout and self.mode is ScoutMode.LAUNCH

    @property
    def message(self) -> Message | None:
        if not self.scout:
            return msg("scout.shape_pure", model=self.pure) if self.pure else None
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
            "pure": self.pure or None,
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


def _searchable(line: str) -> str:
    text = line.lower() if line.isascii() else folded(line)
    return URLS.sub(lambda found: " " * len(found.group()), text)


def _written(line: str, start: int, end: int) -> str:
    if line.isascii():
        return line[start:end]
    offsets = [0]
    for char in line:
        offsets.append(offsets[-1] + len(folded(char)))
    first = bisect_right(offsets, start) - 1
    last = bisect_left(offsets, end)
    while last < len(line) and offsets[last + 1] == offsets[last]:
        last += 1
    return line[first:last]


def _term(text: str) -> str:
    term = text.rstrip(TERM_TRIM)
    return term if len(term) <= TERM_LIMIT else f"{term[: TERM_LIMIT - 1]}…"


def _log_like(line: str) -> bool:
    return ERROR_LINE.search(line) is not None or LOG_FRAME.match(line) is not None


def _labelled(line: str) -> bool:
    return HEADING.match(line) is not None or NUMBERED_PART.match(line) is not None


def _marks(text: str) -> _Marks:
    scoped = tuple(found.span() for found in OUT_OF_SCOPE_AT.finditer(text))
    return _Marks(
        tuple(found.end() for found in SENTENCE_STOP.finditer(text)),
        tuple(found.end() for found in CLAUSE_END.finditer(text)),
        tuple(found.start() for found in SCOPE_LABEL.finditer(text)),
        tuple(start for start, _ in scoped),
        tuple(end for _, end in scoped),
    )


def _last_stop(stops: tuple[int, ...], position: int) -> int:
    index = bisect_right(stops, position) - 1
    return stops[index] if index >= 0 else 0


def _denies(words: list[str], index: int) -> bool:
    if not NEGATION.fullmatch(words[index]):
        return False
    if any(FORGET.match(word) for word in words[index + 1 : index + 3]):
        return False
    return not CONDITIONS.intersection(words[:index])


def _governed(words: list[str], verb: int) -> bool:
    gap = 0
    for index in range(verb - 1, -1, -1):
        word = words[index]
        if _denies(words, index):
            return True
        if SCOPE_BREAK.fullmatch(word):
            return False
        if CHANGE_VERB.fullmatch(word) or ALTERNATIVE.fullmatch(word):
            continue
        gap += 1
        if gap > NEGATION_GAP:
            return False
    return False


def _negated(clause: str) -> bool:
    words = NEGATION_WORD.findall(clause)
    near = len(words) - 1
    while near >= 0 and NOUN_LEAD.fullmatch(words[near]):
        near -= 1
    if near >= 0 and WITHOUT.fullmatch(words[near]):
        return _denies(words, near)
    for index in range(len(words) - 1, max(-1, len(words) - 1 - VERB_WINDOW), -1):
        if CHANGE_VERB.fullmatch(words[index]):
            return _governed(words, index)
    return False


def _refused(text: str, marks: _Marks, start: int, end: int) -> bool:
    sentence = _last_stop(marks.sentences, start)
    if bisect_left(marks.labels, sentence) < bisect_left(marks.labels, start):
        return True
    scoped = bisect_right(marks.scoped_ends, start)
    if scoped < len(marks.scoped_starts) and marks.scoped_starts[scoped] < end:
        return True
    clause = _last_stop(marks.clauses, start)
    opening = max(clause, start - NEGATION_REACH)
    window = text[opening:start]
    return _negated(window.partition(" ")[2] if opening > clause else window)


def _docs_lines(name: str, value: str) -> tuple[tuple[str, str, bool], ...]:
    evidence = name == EVIDENCE_FIELD
    return tuple(
        (line, _searchable(line), not evidence or _labelled(line))
        for line in value.splitlines()
        if not (evidence and _log_like(line))
    )


def docs_request(request: MandateRequest) -> DocsMatch | None:
    for name in DOCS_FIELDS:
        lines = _docs_lines(name, str(getattr(request, name)))
        marked: dict[int, _Marks] = {}
        for rule, pattern in DOCS_RULES:
            for index, (line, text, worded) in enumerate(lines):
                if rule is DocsReason.REQUESTED and not worded:
                    continue
                for found in pattern.finditer(text):
                    marks = marked.get(index)
                    if marks is None:
                        marks = marked[index] = _marks(text)
                    if not _refused(text, marks, found.start(), found.end()):
                        return DocsMatch(rule, name, _term(_written(line, *found.span())))
    return None


def asks_for_docs(request: MandateRequest) -> bool:
    return docs_request(request) is not None


def has_pin(role_models: Iterable[tuple[str, str]], role: Role) -> bool:
    return any(name == role.value for name, _ in role_models)


def docs_setting(chosen: str, default: DocsMode) -> tuple[DocsMode, bool]:
    if not chosen.strip():
        return default, False
    return parse_docs_mode(chosen), True


def docs_choice(
    mode: DocsMode,
    request: MandateRequest,
    trial: bool,
    pinned: bool = False,
    flag: bool = False,
) -> DocsChoice:
    if flag and mode is DocsMode.ON:
        return DocsChoice(True, DocsReason.FLAG_ON)
    if flag and mode is DocsMode.OFF:
        return DocsChoice(False, DocsReason.FLAG_OFF)
    if mode is DocsMode.ON:
        return DocsChoice(True, DocsReason.FORCED_ON)
    found = docs_request(request) if mode is DocsMode.AUTO and not trial else None
    if found is not None:
        return DocsChoice(True, found.rule, found.field, found.term)
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


def pure_shape(entry: ModelEntry | None, threshold: float, mode: ScoutMode) -> ShapeChoice | None:
    if entry is None or tier_rank(entry.tier) < tier_rank(Tier.PREMIUM):
        return None
    return ShapeChoice(
        Shape.PIPELINE, threshold=threshold, mode=mode, pure=entry.resolved or entry.id
    )


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


def docs_refusal(
    docs: str,
    task_type: str,
    *,
    simple: bool = False,
    fast: bool = False,
    classic: bool = False,
    pinned: bool = False,
) -> tuple[Message, str] | None:
    if not docs.strip():
        return None
    mode = parse_docs_mode(docs)
    if classic and mode is not DocsMode.ON:
        return msg("docs.refused_classic"), "drop --docs, or drop --classic"
    if mode is DocsMode.OFF and pinned:
        return msg("docs.refused_pin"), "drop --docs off, or drop --role-model docs=..."
    if mode is not DocsMode.ON:
        return None
    if simple:
        return msg("docs.refused_simple"), "drop --simple, or drop --docs on"
    if task_type == INVESTIGATION:
        return msg("docs.refused_investigation"), "drop --docs on for investigations"
    if fast:
        return msg("docs.refused_fast"), "use --profile balanced with --docs on"
    return None


def scout_agent_prompt(budget: int = PACK_TOKENS) -> str:
    return f"{SCOUT_BODY}\n\n{pack_instruction(budget)}"


def docs_off_line() -> str:
    return f"Docs are off for this run: do not invoke the {DOCS_AGENT}."


def scout_session_block(docs: bool) -> str:
    last = (
        f"4. Invoke the {DOCS_AGENT} last, with the list of changed files."
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
