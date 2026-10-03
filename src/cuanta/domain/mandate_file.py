from __future__ import annotations

import codecs
import re
import textwrap
from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass, fields
from pathlib import PurePath

from cuanta.domain.errors import DomainFailure
from cuanta.domain.mandate import (
    LABELS,
    NUMBERED_PART,
    REQUEST_MARKER,
    MandateRequest,
    MandateType,
    fence_after,
)
from cuanta.domain.messages import Message, english, msg

SPOKEN_LABELS: dict[str, tuple[str, ...]] = {
    "type": ("tipo",),
    "what": ("qué", "que"),
    "why": (
        "why",
        "evidence",
        "por qué / evidencia",
        "por que / evidencia",
        "por qué",
        "por que",
        "porqué",
        "evidencia",
        "evidencias",
    ),
    "where": ("dónde", "donde"),
    "constraints": ("restricciones", "restricción", "restriccion"),
    "tests": ("tests", "test", "pruebas esperadas", "pruebas", "prueba"),
    "out_of_scope": ("fuera de alcance", "fuera del alcance"),
}
LABEL_WORDS: dict[str, tuple[str, ...]] = {
    name: (label.rstrip(":").lower(), *SPOKEN_LABELS[name]) for name, label in LABELS.items()
}
TYPE_SPELLINGS: dict[MandateType, tuple[str, ...]] = {
    MandateType.FEATURE: (
        "feature",
        "feat",
        "funcionalidad",
        "característica",
        "caracteristica",
        "mejora",
        "enhancement",
    ),
    MandateType.BUG: (
        "bug",
        "bugfix",
        "hotfix",
        "fix",
        "error",
        "fallo",
        "arreglo",
        "arreglar",
        "corrección",
        "correccion",
        "corregir",
    ),
    MandateType.REFACTOR: (
        "refactor",
        "refactoring",
        "refactorización",
        "refactorizacion",
        "refactorizar",
    ),
    MandateType.INVESTIGATION: (
        "investigation",
        "investigate",
        "investigación",
        "investigacion",
        "investigar",
        "audit",
        "auditar",
        "auditoría",
        "auditoria",
    ),
}
TYPE_WORDS: dict[str, str] = {
    word: kind.value for kind, words in TYPE_SPELLINGS.items() for word in words
}
KNOWN_TYPES = frozenset(kind.value for kind in MandateType)
TEMPLATE_PLACEHOLDERS = (
    "<the change, concretely>",
    "<PASTE the error, the failing output, the log — never describe it>",
    '<file / module / area, if known — "unknown" is a valid answer>',
    "<invariants, flag directions, what must stay byte-identical>",
    '<what proof you want; "regression fixture for the pasted error" is a good default>',
    "<what must NOT be touched — always fill this>",
    "<PASTE the traceback — never describe it>",
    '<file / module, or "unknown">',
    "<invariants, flag directions, what stays byte-identical>",
    "<regression fixture for the pasted error>",
)
EVIDENCE = "why"
SINGLE_LINE = frozenset({"type"})
TRIGGERS = frozenset({"what", EVIDENCE})
REQUEST_LINE = re.compile(r"[ \t]*" + re.escape(REQUEST_MARKER) + r"[ \t]*")
CONTRACT_HEADING = re.compile(r"[ ]{0,3}##[ \t]+EXECUTION CONTRACT\b")
MANDATE_TITLE = re.compile(r"[ ]{0,3}#[ \t]+MANDATE\b")
ATX_HEADING = re.compile(r"^[ ]{0,3}(?P<marks>#{1,6})[ \t]+(?P<text>.*?)(?:[ \t]+#+)?[ \t]*$")
UNDERLINE = re.compile(r"[ ]{0,3}(?P<mark>=+|-+)[ \t]*")
MARKED_LINE = re.compile(r"[ \t]*(?:>|[-*+][ \t]|\d{1,3}[.)][ \t])")
LINE_MARKS = re.compile(r"^[ \t>]*(?:[-*+][ \t]+|\d{1,3}[.)][ \t]+)?")
BOLD = re.compile(r"(\*\*|__)(?P<inner>.+)\1")
WORD = re.compile(r"[^\W\d_]+")
ESCAPED = re.compile(r"\\(.)")
RAW_SPACE = re.compile(r"(?<!\\)\s")
LONG_STORY = 1_200
DOCUMENT_SUFFIXES = (".md", ".markdown", ".txt")
QUOTES = ('"', "'")
POSIX_START = ("/", "~")
WINDOWS_HOME = "~\\"
ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/|~)")
COMMAND_LINE_WHY = "Added on the command line:"
OVERRIDE_OPTIONS = {
    "type": "--type",
    "where": "--where",
    "constraints": "--constraints",
    "tests": "--tests",
    "out_of_scope": "--out-of-scope",
}
BYTE_ORDER_MARKS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)

Segment = tuple[str, list[str]]


def _spoken(label: str) -> str:
    joined = re.sub(r"[ \t]*/[ \t]*", " / ", label.lower())
    return re.sub(r"[ \t_-]+", " ", joined).strip()


def _pattern(word: str) -> str:
    sides = (
        r"[ \t_-]+".join(re.escape(token) for token in side.split()) for side in word.split("/")
    )
    return r"[ \t]*/[ \t]*".join(sides)


def _placeholder_key(value: str) -> str:
    return " ".join(value.split()).lower()


FIELD_OF: dict[str, str] = {
    _spoken(word): name for name, words in LABEL_WORDS.items() for word in words
}
LABEL_LINE = re.compile(
    r"^[ ]{0,3}(?:#{1,6}[ \t]+)?(?:[-*+][ \t]+)?(?:\*\*|__)?(?P<label>"
    + "|".join(_pattern(word) for word in sorted(FIELD_OF, key=len, reverse=True))
    + r")(?:\*\*|__)?[ \t]*:(?:\*\*|__)?[ \t]*(?P<value>.*)$",
    re.IGNORECASE,
)
PLACEHOLDER_KEYS = frozenset(_placeholder_key(text) for text in TEMPLATE_PLACEHOLDERS)
WRITTEN_TYPE = re.compile(r"^[ ]{0,3}(?:[-*+][ \t]+)?(?:\*\*|__)?(?:TYPE|TIPO)(?:\*\*|__)?[ \t]*:")


@dataclass(frozen=True, slots=True)
class MandateFile:
    request: MandateRequest
    labelled: bool = False
    labelled_fields: tuple[str, ...] = ()

    @property
    def named(self) -> bool:
        return "what" in self.labelled_fields


@dataclass(frozen=True, slots=True)
class LonePath:
    path: str
    story_if_missing: bool = False


@dataclass(frozen=True, slots=True)
class _TitleLine:
    block: int
    index: int
    level: int = 0
    underlined: bool = False


class NotText(DomainFailure):
    def __init__(self, path: str) -> None:
        self.path = path
        self.reason = msg("mandate_file.not_text", path=path)
        self.advice = msg("mandate_file.not_text_hint")
        super().__init__(english(self.reason), english(self.advice))


def decoded_text(data: bytes, path: str | PurePath) -> str:
    codec = next((name for mark, name in BYTE_ORDER_MARKS if data.startswith(mark)), "utf-8")
    try:
        text = data.decode(codec)
    except UnicodeDecodeError as error:
        raise NotText(str(path)) from error
    if "\x00" in text:
        raise NotText(str(path))
    return text


def _cleaned(value: str) -> str:
    return value.strip().strip("*_`.").strip().lower()


def _alternatives(cleaned: str) -> bool:
    options = [option.strip() for option in cleaned.split("|")]
    return len(options) > 1 and all(option in TYPE_WORDS for option in options)


def mandate_type(value: str) -> str:
    cleaned = _cleaned(value)
    if not cleaned or _alternatives(cleaned):
        return ""
    for word in WORD.findall(cleaned):
        if word in TYPE_WORDS:
            return TYPE_WORDS[word]
    return cleaned


def _typed(value: str) -> bool:
    return mandate_type(value) in KNOWN_TYPES or _alternatives(_cleaned(value))


def _trimmed(lines: list[str]) -> str:
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[start:end])


def _stated(name: str, value: str) -> bool:
    if name == "type":
        return bool(mandate_type(value))
    return bool(value.strip()) and _placeholder_key(value) not in PLACEHOLDER_KEYS


def _value(lines: list[str]) -> str:
    head, *rest = lines
    body = textwrap.dedent("\n".join(rest)).split("\n") if rest else []
    return _trimmed([head.strip(), *body])


def _label(line: str) -> tuple[str, str]:
    found = LABEL_LINE.match(line)
    if found is None:
        return "", ""
    return FIELD_OF.get(_spoken(found["label"]), ""), found["value"]


def _ends(segment: Segment, line: str) -> bool:
    name, block = segment
    if name in ("", EVIDENCE):
        return False
    if ATX_HEADING.match(line) is not None:
        return True
    paragraph = len(block) > 1 and not block[-1].strip()
    return paragraph and bool(line.strip()) and not line[0].isspace()


def _segments(lines: list[str]) -> list[Segment]:
    segments: list[Segment] = [("", [])]
    fence = ""
    for line in lines:
        after = fence_after(fence, line)
        if not fence:
            name, value = ("", "") if after else _label(line)
            if name:
                segments.append((name, [value]))
                if name in SINGLE_LINE:
                    segments.append(("", []))
                continue
            if _ends(segments[-1], line):
                segments.append(("", []))
        fence = after
        segments[-1][1].append(line)
    return segments


def _closed(region: list[str]) -> list[str]:
    fence, opened = "", 0
    for index, line in enumerate(region):
        after = fence_after(fence, line)
        if after and not fence:
            opened = index
        fence = after
    if fence and not any(line.strip() for line in region[opened + 1 :]):
        return region[:opened]
    return region


def _last_label(lines: list[str], start: int) -> int:
    last = -1
    for index in range(start, len(lines)):
        if ATX_HEADING.match(lines[index]) is not None:
            break
        if _label(lines[index])[0]:
            last = index
    return last


def _block_end(lines: list[str], start: int, outer: str) -> int:
    last = _last_label(lines, start)
    nested, first = "", -1
    for index in range(start, len(lines)):
        line = lines[index]
        if nested:
            nested = fence_after(nested, line)
        elif fence_after(outer, line):
            nested = fence_after("", line)
        elif index > last:
            return index
        else:
            first = index if first < 0 else first
            nested = fence_after("", line)
    return first if first >= 0 else len(lines)


def _without_contract(lines: list[str]) -> list[str]:
    contract = next(
        (index for index, line in enumerate(lines) if CONTRACT_HEADING.match(line)), None
    )
    if contract is None:
        return lines
    titles = [index for index in range(contract) if MANDATE_TITLE.match(lines[index])]
    return lines[: titles[-1] if titles else contract]


def _request_block(lines: list[str]) -> tuple[list[str], list[str], list[str]] | None:
    fence = ""
    for index, line in enumerate(lines):
        if REQUEST_LINE.fullmatch(line) is not None:
            if not fence:
                return _without_contract(lines[:index]), _closed(lines[index + 1 :]), []
            end = _block_end(lines, index + 1, fence)
            return [], lines[index + 1 : end], lines[end + 1 :]
        fence = fence_after(fence, line)
    return None


def _edges_trimmed(lines: list[str]) -> list[str]:
    shown = [index for index, line in enumerate(lines) if line.strip()]
    return lines[shown[0] : shown[-1] + 1] if shown else []


def _title_only(lines: list[str]) -> bool:
    if len(lines) == 2:
        return _underline_level(lines, 0) > 0
    return len(lines) == 1 and ATX_HEADING.match(lines[0]) is not None


def _type_line(lines: list[str]) -> str:
    return next((line for line in lines if _label(line)[0] == "type"), "")


def _leads_with_type(segments: list[Segment], lines: list[str]) -> bool:
    if len(segments) < 2 or segments[1][0] != "type":
        return False
    head = _edges_trimmed(segments[0][1])
    if not head:
        return True
    return _title_only(head) and WRITTEN_TYPE.match(_type_line(lines)) is not None


def _triggered(segments: list[Segment], lines: list[str]) -> bool:
    return _leads_with_type(segments, lines) or any(
        name in TRIGGERS or (name == "type" and _typed(block[0])) for name, block in segments
    )


def _title(line: str) -> str:
    heading = ATX_HEADING.match(line)
    text = (heading["text"] if heading is not None else LINE_MARKS.sub("", line, count=1)).strip()
    bold = BOLD.fullmatch(text)
    return (bold["inner"] if bold is not None else text).strip()


def _underline_level(lines: list[str], index: int) -> int:
    if index + 1 >= len(lines) or MARKED_LINE.match(lines[index]) is not None:
        return 0
    found = UNDERLINE.fullmatch(lines[index + 1])
    if found is None:
        return 0
    return 1 if found["mark"].startswith("=") else 2


def _title_lines(blocks: list[list[str]]) -> Iterator[_TitleLine]:
    for number, lines in enumerate(blocks):
        fence = ""
        for index, line in enumerate(lines):
            after = fence_after(fence, line)
            inside = bool(fence or after)
            fence = after
            if inside or not any(char.isalnum() for char in _title(line)):
                continue
            heading = ATX_HEADING.match(line)
            if heading is not None:
                yield _TitleLine(number, index, len(heading["marks"]))
            elif level := _underline_level(lines, index):
                yield _TitleLine(number, index, level, underlined=True)
            else:
                yield _TitleLine(number, index)


def _title_at(blocks: list[list[str]]) -> _TitleLine | None:
    lines = list(_title_lines(blocks))
    if not lines:
        return None
    first = lines[0]
    headings = [line for line in lines if line.level]
    if first.level or not headings:
        return first
    top = headings[0]
    numbered = NUMBERED_PART.match(blocks[top.block][top.index]) is not None
    if numbered or any(other.level <= top.level for other in headings[1:]):
        return first
    return top


def _take_title(blocks: list[list[str]]) -> str:
    found = _title_at(blocks)
    if found is None:
        return ""
    lines = blocks[found.block]
    title = _title(lines.pop(found.index))
    if found.underlined:
        lines.pop(found.index)
    return title


def title_and_rest(text: str) -> tuple[str, str]:
    lines = text.split("\n")
    title = _take_title([lines])
    return title, _trimmed(lines)


def _evidence(segments: list[Segment]) -> str:
    chunks: list[str] = []
    for name, block in segments:
        if not name:
            chunks.append(_trimmed(block))
        elif name == EVIDENCE and _stated(name, value := _value(block)):
            chunks.append(value)
    return "\n\n".join(chunk for chunk in chunks if chunk)


def _labelled_file(segments: list[Segment], above: list[str], below: list[str]) -> MandateFile:
    values: dict[str, list[str]] = {}
    for name, block in segments:
        if name and _stated(name, value := _value(block)):
            values.setdefault(name, []).append(value)
    found = {name: "\n\n".join(chunks) for name, chunks in values.items()}
    what = found.get("what", "") or _take_title([block for name, block in segments if not name])
    request = MandateRequest(
        type=mandate_type(found.get("type", "")),
        what=what,
        why=_evidence([("", above), *segments, ("", below)]),
        where=found.get("where", ""),
        constraints=found.get("constraints", ""),
        tests=found.get("tests", ""),
        out_of_scope=found.get("out_of_scope", ""),
    )
    return MandateFile(request, True, tuple(name for name in LABELS if name in values))


def _lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").removeprefix("﻿").split("\n")


def parse_mandate_file(text: str) -> MandateFile:
    lines = _lines(text)
    block = _request_block(lines)
    if block is not None:
        above, region, below = block
        segments = _segments(region)
        if any(name for name, _ in segments):
            return _labelled_file(segments, above, below)
    segments = _segments(lines)
    if _triggered(segments, lines):
        return _labelled_file(segments, [], [])
    what = _take_title([lines])
    return MandateFile(MandateRequest(what=what, why=_trimmed(lines)))


def trimmed(text: str) -> str:
    return _trimmed(text.split("\n"))


def _titled(text: str) -> bool:
    first = next(_title_lines([_lines(text)]), None)
    return first is not None and first.level > 0 and (not first.underlined or first.level == 1)


def whole_mandate(text: str) -> MandateFile | None:
    parsed = parse_mandate_file(text)
    if parsed.named or len(text.strip()) > LONG_STORY or _titled(text):
        return parsed
    return None


def _quoted(text: str) -> bool:
    return len(text) >= 2 and text[0] == text[-1] and text[0] in QUOTES


def unquoted(text: str) -> str:
    cleaned = text.strip()
    return cleaned[1:-1].strip() if _quoted(cleaned) else cleaned


def _posix_typed(cleaned: str) -> bool:
    posix = cleaned.startswith(POSIX_START) and not cleaned.startswith(WINDOWS_HOME)
    return posix and not _quoted(cleaned)


def typed_path(text: str) -> str:
    cleaned = text.strip()
    if _posix_typed(cleaned):
        return ESCAPED.sub(r"\1", cleaned)
    return unquoted(cleaned)


def lone_path(text: str) -> LonePath | None:
    cleaned = text.strip()
    path = typed_path(cleaned)
    if not path or "\n" in path or "\r" in path or "://" in path:
        return None
    if not path.lower().endswith(DOCUMENT_SUFFIXES):
        return None
    if any(char.isspace() for char in path) and ABSOLUTE_PATH.match(path) is None:
        return None
    sentence = _posix_typed(cleaned)
    return LonePath(path, sentence and RAW_SPACE.search(cleaned) is not None)


def mandate_path(text: str) -> str:
    found = lone_path(text)
    return found.path if found is not None else ""


def _differs(stated: str, given: str) -> bool:
    return bool(given.strip()) and bool(stated.strip()) and given.strip() != stated.strip()


def overridden(parsed: MandateRequest, given: MandateRequest) -> tuple[str, ...]:
    return tuple(
        item.name
        for item in fields(MandateRequest)
        if item.name != EVIDENCE and _differs(getattr(parsed, item.name), getattr(given, item.name))
    )


def _merged_evidence(parsed: MandateRequest, given: MandateRequest) -> str:
    parts = [parsed.what.strip()] if _differs(parsed.what, given.what) else []
    if parsed.why.strip():
        parts.append(parsed.why)
    if given.why.strip():
        parts.append(f"{COMMAND_LINE_WHY}\n{given.why.strip()}" if parts else given.why)
    return "\n\n".join(parts)


def with_overrides(parsed: MandateRequest, given: MandateRequest) -> MandateRequest:
    values = {
        item.name: getattr(given, item.name)
        if getattr(given, item.name).strip()
        else getattr(parsed, item.name)
        for item in fields(MandateRequest)
    }
    values[EVIDENCE] = _merged_evidence(parsed, given)
    return MandateRequest(**values)


def joined(parts: Sequence[Message]) -> Message | None:
    if not parts:
        return None
    rest = joined(parts[1:])
    return parts[0] if rest is None else msg("mandate_file.join", first=parts[0], rest=rest)


def command_line_note(parsed: MandateRequest, given: MandateRequest) -> Message | None:
    changed = overridden(parsed, given)
    options = [OVERRIDE_OPTIONS[name] for name in changed if name in OVERRIDE_OPTIONS]
    parts = [msg("mandate_file.over_file", options=", ".join(options))] if options else []
    if "what" in changed:
        parts.append(msg("mandate_file.what_moved"))
    if given.why.strip() and (parsed.why.strip() or "what" in changed):
        parts.append(msg("mandate_file.why_added"))
    return joined(parts)


def labelled_note(parsed: MandateFile, replaced: Collection[str]) -> Message | None:
    kept = [name.replace("_", " ") for name in parsed.labelled_fields if name not in replaced]
    return msg("mandate_file.labelled", fields=", ".join(kept)) if kept else None
