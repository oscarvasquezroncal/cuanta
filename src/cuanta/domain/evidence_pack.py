from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

from cuanta.domain.change_plan import ChangePlan, EditTarget
from cuanta.domain.report import unified_diff
from cuanta.domain.role_handoff import (
    CHARS_PER_TOKEN,
    SUMMARY_LIMIT,
    Fact,
    HandoffPlan,
    HandoffSource,
    HandoffStatus,
    RoleHandoff,
    anchored_facts,
    clip_text,
    estimate_tokens,
    handoff_paths,
    handoff_status,
    handoff_texts,
    last_json_object,
    parse_anchor,
)

PACK_TOKENS = 6_000
TOP_FACTS = 12
FACT_LIMIT = 40
SNIPPET_LIMIT = 12
SNIPPET_LINES = 40
SNIPPET_CHARS = 2_000
RISK_LIMIT = 8
TEST_LIMIT = 12
DIFF_TOKENS = 3_000
PATH_LIMIT = 400
EDIT_REASON = "scout edit set"
DRIVE = re.compile(r"^[A-Za-z]:")

LinesOf = Callable[[str], Sequence[str] | None]


class PackSource(StrEnum):
    JSON = "json"
    FALLBACK = "fallback"


@dataclass(frozen=True, slots=True)
class Snippet:
    path: str
    start: int
    end: int
    text: str

    @property
    def anchor(self) -> str:
        return f"{self.path}:{self.start}-{self.end}"


@dataclass(frozen=True, slots=True)
class EvidencePack:
    summary: str = ""
    facts: tuple[Fact, ...] = ()
    snippets: tuple[Snippet, ...] = ()
    risks: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    edit: tuple[str, ...] = ()
    status: HandoffStatus = HandoffStatus.DONE
    source: PackSource = PackSource.JSON

    @property
    def files(self) -> tuple[str, ...]:
        cited = (*(fact.path for fact in self.facts), *(item.path for item in self.snippets))
        return tuple(dict.fromkeys(cited))


@dataclass(frozen=True, slots=True)
class PackCheck:
    pack: EvidencePack
    tokens: int
    budget: int
    raw_tokens: int
    invalid: int = 0
    limited: int = 0
    dropped_snippets: int = 0
    dropped_facts: int = 0
    edit_from_plan: bool = False

    @property
    def over_budget(self) -> bool:
        return self.tokens > self.budget

    @property
    def trimmed(self) -> bool:
        return self.dropped_snippets > 0 or self.dropped_facts > 0


@dataclass(frozen=True, slots=True)
class OutsideEdits:
    named: tuple[str, ...] = ()
    unnamed: tuple[str, ...] = ()

    @property
    def paths(self) -> tuple[str, ...]:
        return (*self.named, *self.unnamed)


@dataclass(frozen=True, slots=True)
class SeniorScope:
    edit: tuple[str, ...]
    files: tuple[str, ...]
    leaked: tuple[str, ...] | None = None
    outside: OutsideEdits = field(default_factory=OutsideEdits)


def safe_path(path: str) -> bool:
    cleaned = path.strip().replace("\\", "/")
    if not cleaned or cleaned.startswith("/") or DRIVE.match(cleaned):
        return False
    return ".." not in cleaned.split("/")


def _number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _snippet(item: object) -> Snippet | None:
    if not isinstance(item, dict):
        return None
    body = item.get("text") or item.get("code") or item.get("snippet")
    if not isinstance(body, str) or not body.strip():
        return None
    anchor_text = item.get("anchor")
    if isinstance(anchor_text, str) and (anchor := parse_anchor(anchor_text)) is not None:
        return Snippet(anchor[0], anchor[1], anchor[2], body)
    path = item.get("path") or item.get("file")
    start = _number(item.get("start"))
    if not isinstance(path, str) or not path.strip() or start is None or start < 1:
        return None
    end = _number(item.get("end"))
    last = end if end is not None and end >= start else start
    return Snippet(path.strip().replace("\\", "/").removeprefix("./"), start, last, body)


def _snippets(value: object) -> tuple[Snippet, ...]:
    if not isinstance(value, list):
        return ()
    found = (_snippet(item) for item in value)
    return tuple(item for item in found if item is not None)


def _edit_set(data: Mapping[str, object]) -> tuple[str, ...]:
    for key in ("edit_set", "edit"):
        if key in data:
            return handoff_paths(data.get(key))
    plan = data.get("plan")
    return handoff_paths(plan.get("edit")) if isinstance(plan, dict) else ()


def parse_pack(text: str) -> EvidencePack | None:
    data = last_json_object(text)
    if data is None:
        return None
    return EvidencePack(
        summary=clip_text(data.get("summary") or "", SUMMARY_LIMIT),
        facts=anchored_facts(data.get("facts")),
        snippets=_snippets(data.get("snippets")),
        risks=handoff_texts(data.get("risks")),
        tests=handoff_texts(data.get("tests") or data.get("test_links"), PATH_LIMIT),
        edit=_edit_set(data),
        status=handoff_status(data.get("status"), HandoffStatus.DONE),
        source=PackSource.JSON,
    )


def fallback_pack(summary: str, facts: Sequence[Fact], edit: Sequence[str]) -> EvidencePack:
    return EvidencePack(
        summary=clip_text(summary, SUMMARY_LIMIT),
        facts=tuple(facts),
        edit=tuple(dict.fromkeys(edit)),
        status=HandoffStatus.PARTIAL,
        source=PackSource.FALLBACK,
    )


Lines = dict[str, Sequence[str] | None]


def _lines_of(path: str, cache: Lines, lines_of: LinesOf) -> Sequence[str] | None:
    if path not in cache:
        cache[path] = lines_of(path)
    return cache[path]


def _within(path: str, start: int, end: int, cache: Lines, lines_of: LinesOf | None) -> bool:
    if not safe_path(path) or start < 1 or end < start:
        return False
    if lines_of is None:
        return True
    found = _lines_of(path, cache, lines_of)
    return found is not None and 1 <= start <= end <= len(found)


def _actual(item: Snippet, cache: Lines, lines_of: LinesOf | None) -> Snippet:
    found = _lines_of(item.path, cache, lines_of) if lines_of is not None else None
    if found is None:
        return item
    return replace(item, text="\n".join(found[item.start - 1 : item.end]))


def validate_pack(pack: EvidencePack, lines_of: LinesOf | None) -> tuple[EvidencePack, int]:
    cache: Lines = {}
    facts = tuple(
        dict.fromkeys(
            fact for fact in pack.facts if _within(fact.path, fact.start, fact.end, cache, lines_of)
        )
    )
    snippets = tuple(
        _actual(item, cache, lines_of)
        for item in pack.snippets
        if _within(item.path, item.start, item.end, cache, lines_of)
    )
    edit = tuple(dict.fromkeys(path for path in pack.edit if safe_path(path)))
    invalid = (
        len(pack.facts)
        - len(facts)
        + len(pack.snippets)
        - len(snippets)
        + len(pack.edit)
        - len(edit)
    )
    return replace(pack, facts=facts, snippets=snippets, edit=edit), invalid


def _clip_snippet(item: Snippet) -> Snippet:
    lines = item.text.splitlines()
    kept = lines[:SNIPPET_LINES]
    text = "\n".join(kept)
    if len(text) > SNIPPET_CHARS:
        text = text[: SNIPPET_CHARS - 1].rstrip() + "…"
    end = item.start + len(kept) - 1 if len(lines) > SNIPPET_LINES else item.end
    return replace(item, text=text, end=min(item.end, end))


def limit_pack(pack: EvidencePack) -> tuple[EvidencePack, int]:
    limited = (
        max(0, len(pack.facts) - FACT_LIMIT)
        + max(0, len(pack.snippets) - SNIPPET_LIMIT)
        + max(0, len(pack.risks) - RISK_LIMIT)
        + max(0, len(pack.tests) - TEST_LIMIT)
    )
    return (
        replace(
            pack,
            facts=pack.facts[:FACT_LIMIT],
            snippets=tuple(_clip_snippet(item) for item in pack.snippets[:SNIPPET_LIMIT]),
            risks=pack.risks[:RISK_LIMIT],
            tests=pack.tests[:TEST_LIMIT],
        ),
        limited,
    )


def render_pack(pack: EvidencePack) -> str:
    lines: list[str] = []
    if pack.summary:
        lines.append(f"Summary: {pack.summary}")
    lines.append("Edit set (the files you may edit):")
    lines.extend(f"- {path}" for path in pack.edit or ("none",))
    if pack.facts:
        lines.append("Facts (open these ranges; do not re-read whole files):")
        lines.extend(f"- {fact.anchor} {fact.claim}".rstrip() for fact in pack.facts)
    if pack.snippets:
        lines.append("Snippets:")
        for item in pack.snippets:
            lines.append(f"--- {item.anchor}")
            lines.append(item.text)
    if pack.risks:
        lines.append("Risks:")
        lines.extend(f"- {risk}" for risk in pack.risks)
    if pack.tests:
        lines.append("Tests:")
        lines.extend(f"- {test}" for test in pack.tests)
    return "\n".join(lines)


def pack_tokens(pack: EvidencePack) -> int:
    return estimate_tokens(render_pack(pack))


def trim_pack(
    pack: EvidencePack, budget: int = PACK_TOKENS, top_facts: int = TOP_FACTS
) -> tuple[EvidencePack, int, int]:
    current = pack
    dropped_snippets = 0
    dropped_facts = 0
    while current.snippets and pack_tokens(current) > budget:
        current = replace(current, snippets=current.snippets[:-1])
        dropped_snippets += 1
    while len(current.facts) > top_facts and pack_tokens(current) > budget:
        current = replace(current, facts=current.facts[:-1])
        dropped_facts += 1
    return current, dropped_snippets, dropped_facts


def check_pack(
    pack: EvidencePack,
    lines_of: LinesOf | None,
    planned_edit: Sequence[str] = (),
    budget: int = PACK_TOKENS,
) -> PackCheck:
    raw = pack_tokens(pack)
    valid, invalid = validate_pack(pack, lines_of)
    limited, over_limits = limit_pack(valid)
    from_plan = not limited.edit and bool(planned_edit)
    if from_plan:
        limited = replace(limited, edit=tuple(dict.fromkeys(planned_edit)))
    trimmed, snippets, facts = trim_pack(limited, budget)
    return PackCheck(
        pack=trimmed,
        tokens=pack_tokens(trimmed),
        budget=budget,
        raw_tokens=raw,
        invalid=invalid,
        limited=over_limits,
        dropped_snippets=snippets,
        dropped_facts=facts,
        edit_from_plan=from_plan,
    )


def pack_handoff(
    check: PackCheck,
    role: str,
    *,
    engine: str = "",
    model: str = "",
    run_id: str = "",
    capsule: str = "",
    verify: Sequence[str] = (),
    reason: str = "",
) -> RoleHandoff:
    pack = check.pack
    return RoleHandoff(
        role,
        engine,
        model,
        status=pack.status,
        source=HandoffSource.JSON if pack.source is PackSource.JSON else HandoffSource.FALLBACK,
        summary=pack.summary,
        facts=pack.facts,
        plan=HandoffPlan(pack.edit, (), tuple(verify)),
        open_questions=pack.risks,
        run_id=run_id,
        capsule=capsule,
        reason=reason,
    )


def scope_plan(plan: ChangePlan | None, pack: EvidencePack) -> ChangePlan:
    base = plan if plan is not None else ChangePlan()
    edit = tuple(EditTarget(path, 1.0, EDIT_REASON) for path in pack.edit)
    folded = {path.casefold() for path in pack.edit}
    read = tuple(path for path in pack.files if path.casefold() not in folded)
    return replace(base, edit=edit, read=read, read_only=False)


def _folded(paths: Iterable[str]) -> frozenset[str]:
    return frozenset(path.replace("\\", "/").removeprefix("./").casefold() for path in paths)


def outside_edits(
    changed: Sequence[str], edit: Sequence[str], named: Sequence[str]
) -> OutsideEdits:
    allowed = _folded(edit)
    listed = _folded(named)
    outside = [path for path in changed if path.casefold() not in allowed]
    return OutsideEdits(
        tuple(path for path in outside if path.casefold() in listed),
        tuple(path for path in outside if path.casefold() not in listed),
    )


def _path_key(path: str) -> str:
    return path.strip().replace("\\", "/").removeprefix("./").casefold()


def touched_files(changed: Sequence[str], touched: Sequence[str]) -> tuple[str, ...]:
    found: dict[str, None] = {}
    for raw in touched:
        key = _path_key(raw)
        matches = [
            path
            for path in changed
            if key == _path_key(path) or key.endswith("/" + _path_key(path))
        ]
        if matches:
            found[max(matches, key=len)] = None
    return tuple(path for path in changed if path in found)


def leaked_reads(
    reads: Sequence[str], edit: Sequence[str], files: Sequence[str]
) -> tuple[str, ...]:
    allowed = _folded((*edit, *files))
    return tuple(dict.fromkeys(path for path in reads if path.casefold() not in allowed))


def change_digest(
    changed: Sequence[str],
    before: Mapping[str, str | None],
    after: Callable[[str], str | None],
    limit_tokens: int = DIFF_TOKENS,
) -> str:
    limit = limit_tokens * CHARS_PER_TOKEN
    parts: list[str] = []
    listed: list[str] = []
    used = 0
    for path in changed:
        current = after(path)
        if path not in before or current is None:
            listed.append(path)
            continue
        text = unified_diff(before[path] or "", current, path)
        if not text:
            continue
        if used + len(text) > limit:
            listed.append(path)
            continue
        parts.append(text)
        used += len(text)
    if listed:
        parts.append("Changed without a diff here (open them): " + ", ".join(listed))
    return "\n".join(parts)


def pack_instruction(budget: int = PACK_TOKENS) -> str:
    return (
        "End your answer with one JSON object, your evidence pack for the senior, at most about "
        f"{budget:,} tokens: "
        '{"summary": "...", '
        '"facts": [{"path": "src/file.ts", "start": 12, "end": 30, "claim": "..."}], '
        '"snippets": [{"path": "src/file.ts", "start": 12, "end": 20, "text": "the exact lines"}], '
        '"risks": ["..."], "tests": ["tests/file.test.ts"], '
        '"edit_set": ["src/file.ts"], "status": "done|partial|blocked"}. '
        "Facts and snippets must cite line ranges you actually read; keep snippets to the few "
        "lines the change needs. The edit set is the confirmed list of files the senior may edit; "
        "list a new file the change needs by its path."
    )
