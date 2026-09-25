from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from cuanta.domain.instinct import Choice

KINDS = ("bug", "feature", "refactor", "investigation")
QUESTION_LIMIT = 8
ERROR_LIMIT = 12
MENTION_LIMIT = 12
LOW_TYPE_CONFIDENCE = 0.3

INTERROGATIVES_ES = (
    r"para\s+qu[eé]",
    r"por\s+qu[eé]",
    r"c[oó]mo",
    r"d[oó]nde",
    r"cu[aá]ndo",
    r"cu[aá]l(?:es)?",
    r"cu[aá]nto(?:s)?",
    r"qui[eé]n(?:es)?",
    r"qu[eé]",
    r"si",
)
INTERROGATIVES_EN = (
    r"what",
    r"why",
    r"how",
    r"where",
    r"when",
    r"which",
    r"who",
    r"whether",
    r"if",
)
INTERROGATIVE = "|".join((*INTERROGATIVES_ES, *INTERROGATIVES_EN))
LEADS = (
    r"quiero\s+(?:entender|saber|conocer|ver|averiguar)",
    r"necesito\s+(?:entender|saber|conocer|averiguar)",
    r"me\s+gustar[ií]a\s+(?:entender|saber|conocer)",
    r"expl[ií]ca(?:me)?",
    r"dime",
    r"averigua",
    r"investiga",
    r"i\s+(?:want|need|would\s+like)\s+to\s+(?:understand|know|find\s+out|learn|see)",
    r"explain",
    r"tell\s+me",
    r"find\s+out",
    r"figure\s+out",
    r"investigate",
    r"entender|saber|auditar|revisar|analizar|investigar",
    r"understand|know|audit|review|analy[sz]e|investigate",
)
LEAD_AT = re.compile(rf"\b(?:{'|'.join(LEADS)})\b\s*", re.IGNORECASE)
NOUN_LEAD_AT = re.compile(
    r"\b(?:auditor[ií]a|revisi[oó]n|an[aá]lisis)\s+de\b"
    r"|\b(?:audit|review|analysis)\s+of\b",
    re.IGNORECASE,
)
INTERROGATIVE_AT = re.compile(rf"\b(?:{INTERROGATIVE})\b", re.IGNORECASE)
CLAUSE_SPLIT = re.compile(
    rf"\s*(?:,|;|\s+y\s+|\s+e\s+|\s+and\s+|\s+or\s+|\s+o\s+)\s*(?=(?:{INTERROGATIVE})\b)",
    re.IGNORECASE,
)
STARTS_INTERROGATIVE = re.compile(rf"^(?:{INTERROGATIVE})\b", re.IGNORECASE)
SPANISH_START = re.compile(rf"^(?:{'|'.join(INTERROGATIVES_ES)})\b", re.IGNORECASE)
CONDITIONAL_START = re.compile(r"^(?:si|whether|if)\s+", re.IGNORECASE)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")

READ_ONLY_PHRASES = (
    r"no\s+(?:cambies|modifiques|toques|edites|borres)\s+nada",
    r"sin\s+(?:tocar|cambiar|modificar)\s+nada",
    r"solo\s+lectura",
    r"s[oó]lo\s+(?:lee|leer|mirar|revisar)",
    r"read[\s-]?only",
    r"(?:don'?t|do\s+not)\s+(?:change|modify|touch|edit)\s+anything",
    r"no\s+changes",
    r"change\s+nothing",
)
OUT_OF_SCOPE_PHRASES = (
    *READ_ONLY_PHRASES,
    r"no\s+(?:cambies|modifiques|toques|edites|borres)\b",
    r"sin\s+(?:tocar|cambiar|modificar)\b",
    r"(?:don'?t|do\s+not|never)\s+(?:change|modify|touch|edit|delete)\b",
    r"leave\s+\S+(?:\s+\S+)?\s+(?:alone|untouched|as\s+is)",
    r"out\s+of\s+scope",
    r"fuera\s+de\s+alcance",
)
READ_ONLY_AT = re.compile("|".join(READ_ONLY_PHRASES), re.IGNORECASE)
OUT_OF_SCOPE_AT = re.compile("|".join(OUT_OF_SCOPE_PHRASES), re.IGNORECASE)

ERROR_LINE = re.compile(
    r"(?:\bTraceback \(most recent call last\))"
    r"|(?:^\s*File \".+\", line \d+)"
    r"|(?:^\s*at\s+\S.*[:(]\d+)"
    r"|(?:\b[A-Z]\w*(?:Error|Exception)\b(?::|$))"
    r"|(?:\berror\s+TS\d+)"
    r"|(?:^E\s{2,}\S)"
    r"|(?:\bnpm ERR!)"
    r"|(?:^\s*FAILED\s+\S)"
    r"|(?:\bpanic:)"
    r"|(?:\bUncaught\b)",
    re.MULTILINE,
)
PATH_MENTION = re.compile(
    r"(?<![\w/.-])((?:[\w@.-]+/)*[\w@-]+\.(?:tsx?|jsx?|mjs|cjs|py|go|rs|css|scss|md|json|toml|ya?ml|html|vue|svelte))\b"
)
DIR_MENTION = re.compile(r"(?<![\w.-])((?:[\w@-]+/){1,}[\w@-]*)(?=[\s,;:)]|\.(?:\s|$)|$)")
ROUTE_MENTION = re.compile(r"(?:(?<=\s)|^)(/[a-z0-9][\w/-]*)", re.IGNORECASE)
COMPONENT_MENTION = re.compile(r"\b([A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+)\b")
CODE_MENTION = re.compile(r"`([^`\n]{2,80})`")
WORD = re.compile(r"[a-záéíóúñü][a-z0-9áéíóúñü_-]{3,}", re.IGNORECASE)

TYPE_SIGNALS: Mapping[str, tuple[str, ...]] = {
    "bug": (
        r"\berror\b",
        r"\bfalla\w*",
        r"\bfail\w*",
        r"\broto\b",
        r"\brota\b",
        r"\bbroken\b",
        r"\bbug\b",
        r"\bcrash\w*",
        r"\bexcepti\w+",
        r"no\s+funciona",
        r"(?:doesn'?t|does\s+not|isn'?t|is\s+not)\s+work",
        r"\bse\s+rompe\b",
        r"\barregla\w*",
        r"\bfix\b",
        r"\bcorrige\w*",
    ),
    "feature": (
        r"\bagrega\w*",
        r"\ba[nñ]ad\w+",
        r"\bcrea(?:r)?\b",
        r"\bimplementa\w*",
        r"\bnuev[oa]s?\b",
        r"\bpermit\w+",
        r"\badd\b",
        r"\bcreate\b",
        r"\bimplement\w*",
        r"\bnew\b",
        r"\bsupport\b",
        r"\ballow\w*",
        r"\bbuild\b",
    ),
    "refactor": (
        r"\brefactor\w*",
        r"\blimpi\w+",
        r"\breorganiz\w+",
        r"\bsimplific\w+",
        r"\bextra[eé]\w*",
        r"\bextract\w*",
        r"\bclean\s*up\b",
        r"\brestructur\w+",
        r"\brename\w*",
        r"\brenombr\w+",
        r"\bsimplify\b",
        r"\bdedupl\w+",
    ),
    "investigation": (
        r"\bentender\b",
        r"\bunderstand\b",
        r"\bexpl[ií]ca\w*",
        r"\bexplain\w*",
        r"\binvestig\w+",
        r"\banaliz\w+",
        r"\banaly[sz]\w+",
        r"\baudit\w*",
        r"\brevis\w+",
        r"\breview\w*",
        r"c[oó]mo\s+funciona",
        r"how\s+does",
        r"para\s+qu[eé]\s+(?:es|sirve)",
        r"what\s+(?:is|does)",
        r"por\s+qu[eé]",
        r"\bwhy\b",
    ),
}


@dataclass(frozen=True, slots=True)
class IntakeFacts:
    errors: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()
    out_of_scope: tuple[str, ...] = ()
    mentions: tuple[str, ...] = ()
    words: tuple[str, ...] = ()
    read_only: bool = False
    core: str = ""


def sentences(text: str) -> list[str]:
    return [part.strip() for part in SENTENCE_END.split(text) if part.strip()]


def _dedupe(items: Iterable[str]) -> tuple[str, ...]:
    seen: dict[str, str] = {}
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen[key] = item.strip()
    return tuple(seen.values())


def phrase_question(clause: str) -> str:
    text = clause.strip().strip("¿?.!;:, ").strip()
    if not text:
        return ""
    spanish = bool(SPANISH_START.match(text))
    if spanish:
        text = CONDITIONAL_START.sub("", text, count=1)
    body = text[0].upper() + text[1:]
    return f"¿{body}?" if spanish else f"{body}?"


def _clauses(text: str) -> list[str]:
    return [part for part in CLAUSE_SPLIT.split(text) if part.strip()]


def _question_tail(sentence: str) -> tuple[str, str]:
    head, separator, tail = sentence.partition(":")
    if separator and STARTS_INTERROGATIVE.match(tail.strip()):
        noun = NOUN_LEAD_AT.search(head)
        return tail.strip(), head[noun.end() :].strip(" ,:") if noun else ""
    noun = NOUN_LEAD_AT.search(sentence)
    if noun is not None:
        remainder = sentence[noun.end() :]
        first = INTERROGATIVE_AT.search(remainder)
        if first is not None:
            return remainder[first.start() :], remainder[: first.start()].strip(" ,:")
    lead = LEAD_AT.search(sentence)
    if lead is not None:
        return sentence[lead.end() :], ""
    return "", ""


def extract_questions(text: str) -> tuple[str, ...]:
    found: list[str] = []
    for sentence in sentences(text):
        stripped = sentence.strip()
        if OUT_OF_SCOPE_AT.search(stripped) and not stripped.endswith("?"):
            continue
        tail, context = _question_tail(stripped)
        if tail:
            parts = [part for part in _clauses(tail) if STARTS_INTERROGATIVE.match(part.strip())]
            if context and parts and re.fullmatch(r"para\s+qu[eé]\s+(?:es|sirve)", parts[0], re.I):
                parts[0] = f"{parts[0]} {context}"
            found.extend(phrase_question(part) for part in parts)
            continue
        if stripped.endswith("?") or stripped.startswith("¿"):
            found.extend(phrase_question(part) for part in _clauses(stripped))
            continue
        if STARTS_INTERROGATIVE.match(stripped) and not CONDITIONAL_START.match(stripped):
            found.extend(phrase_question(part) for part in _clauses(stripped))
    return _dedupe(item for item in found if item)[:QUESTION_LIMIT]


def extract_errors(text: str) -> tuple[str, ...]:
    lines = [line.rstrip() for line in text.splitlines()]
    found = [line.strip() for line in lines if line.strip() and ERROR_LINE.search(line)]
    return _dedupe(found)[:ERROR_LIMIT]


def extract_out_of_scope(text: str) -> tuple[str, ...]:
    found = [
        sentence.strip().rstrip(".!")
        for sentence in sentences(text)
        if OUT_OF_SCOPE_AT.search(sentence)
    ]
    return _dedupe(found)


def extract_mentions(text: str) -> tuple[str, ...]:
    found: list[str] = []
    found.extend(match.group(1) for match in CODE_MENTION.finditer(text))
    found.extend(match.group(1) for match in PATH_MENTION.finditer(text))
    found.extend(
        match.group(1).rstrip("/")
        for match in DIR_MENTION.finditer(text)
        if "." not in match.group(1)
    )
    found.extend(match.group(1) for match in ROUTE_MENTION.finditer(text))
    found.extend(
        match.group(1)
        for match in COMPONENT_MENTION.finditer(text)
        if not match.group(1).endswith(("Error", "Exception"))
    )
    return _dedupe(item for item in found if len(item) > 1)[:MENTION_LIMIT]


def extract_words(text: str) -> tuple[str, ...]:
    return _dedupe(match.group(0).lower() for match in WORD.finditer(text))


def core_text(text: str) -> str:
    kept = [sentence for sentence in sentences(text) if not OUT_OF_SCOPE_AT.search(sentence)]
    return " ".join(kept).strip() or text.strip()


def extract(text: str) -> IntakeFacts:
    return IntakeFacts(
        errors=extract_errors(text),
        questions=extract_questions(text),
        out_of_scope=extract_out_of_scope(text),
        mentions=extract_mentions(text),
        words=extract_words(text),
        read_only=bool(READ_ONLY_AT.search(text)),
        core=core_text(text),
    )


def type_scores(text: str, facts: IntakeFacts) -> dict[str, int]:
    lowered = text.lower()
    scores = {
        kind: sum(1 for pattern in patterns if re.search(pattern, lowered))
        for kind, patterns in TYPE_SIGNALS.items()
    }
    scores["bug"] += 2 if facts.errors else 0
    scores["investigation"] += 2 if facts.read_only else 0
    scores["investigation"] += 1 if facts.questions and not facts.errors else 0
    return scores


def heuristic_type(text: str, facts: IntakeFacts) -> Choice:
    scores = type_scores(text, facts)
    ranked = sorted(KINDS, key=lambda kind: (-scores[kind], KINDS.index(kind)))
    best, runner_up = ranked[0], ranked[1]
    if scores[best] == 0:
        return Choice("feature", 0.25)
    margin = scores[best] - scores[runner_up]
    return Choice(best, min(0.45 + 0.12 * margin + 0.03 * scores[best], 0.92))


GAP_KEYS: Mapping[str, tuple[str, ...]] = {
    "bug": ("evidence", "expected"),
    "feature": ("acceptance",),
    "refactor": ("invariants",),
    "investigation": ("deliverable",),
}
GAP_SIGNALS: Mapping[str, tuple[str, ...]] = {
    "evidence": (r"\berror\b", r"traceback", r"\bfalla", r"\bfail", r"\blog\b", r"stack"),
    "expected": (r"deber[ií]a", r"should", r"expected", r"esperad", r"en\s+vez\s+de", r"instead"),
    "acceptance": (
        r"criteri",
        r"acceptance",
        r"cuando\s+.+\s+entonces",
        r"given",
        r"prueba",
        r"test",
    ),
    "invariants": (
        r"sin\s+cambiar\s+(?:el\s+)?comportamiento",
        r"without\s+changing",
        r"api\s+p[uú]blica",
        r"public\s+api",
        r"invariant",
        r"mismas?\s+pruebas",
        r"same\s+tests",
    ),
    "deliverable": (
        r"\bresumen\b",
        r"\bsummary\b",
        r"\binforme\b",
        r"\breport\b",
        r"\bdiagrama\b",
        r"\bdiagram\b",
        r"\briesgos\b",
        r"\brisks\b",
    ),
}
GAP_ANSWERS: Mapping[str, tuple[str, ...]] = {
    "evidence": ("attach", "last_failure"),
    "expected": ("no_crash", "show_message", "as_before"),
    "acceptance": ("unit_tests", "integration_test", "manual_check"),
    "invariants": ("public_api", "same_behaviour", "tests_green"),
    "deliverable": ("summary", "report", "diagram", "risks"),
}
GAP_FIELD: Mapping[str, str] = {
    "evidence": "why",
    "expected": "why",
    "acceptance": "tests",
    "invariants": "constraints",
    "deliverable": "tests",
}


def gap_keys(kind: str) -> tuple[str, ...]:
    return GAP_KEYS.get(kind, ())


def gap_prior(gap: str, text: str, facts: IntakeFacts) -> float:
    if gap == "evidence" and facts.errors:
        return 0.95
    lowered = text.lower()
    hits = sum(1 for pattern in GAP_SIGNALS.get(gap, ()) if re.search(pattern, lowered))
    return 0.9 if hits else 0.15


def depth_for_scope(scope: str) -> str:
    return {"trivial": "quick", "complex": "deep"}.get(scope, "normal")


def match_places(
    facts: IntakeFacts, files: Sequence[str], symbols: Mapping[str, str], limit: int = 8
) -> tuple[str, ...]:
    by_stem: dict[str, list[str]] = {}
    for path in files:
        name = path.rsplit("/", 1)[-1]
        stem = name.split(".", 1)[0].lower()
        by_stem.setdefault(stem, []).append(path)
    found: list[str] = []
    lowered_files = {path.lower(): path for path in files}
    for mention in facts.mentions:
        key = mention.strip("/").lower()
        if key in lowered_files:
            found.append(lowered_files[key])
            continue
        suffix = [path for low, path in lowered_files.items() if low.endswith("/" + key)]
        if suffix:
            found.extend(suffix[:2])
            continue
        if mention in symbols:
            found.append(symbols[mention])
            continue
        found.extend(by_stem.get(key, [])[:2])
    for word in facts.words:
        found.extend(by_stem.get(word, [])[:1])
    return _dedupe(found)[:limit]
