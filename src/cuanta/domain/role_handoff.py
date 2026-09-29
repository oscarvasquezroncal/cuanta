from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

from cuanta.domain.depth import DEFAULT_DEPTH, Depth

HANDOFF_BUDGETS: Mapping[str, int] = {Depth.QUICK: 1_200, Depth.NORMAL: 2_000, Depth.DEEP: 3_000}
CHARS_PER_TOKEN = 4
SUMMARY_LIMIT = 700
ITEM_LIMIT = 240
SCAN_LIMIT = 40_000
ATTEMPT_LIMIT = 400
OMISSION_RESERVE = 96
ANCHOR = re.compile(
    r"^(?P<path>[^\s:]+(?::[\\/][^\s:]*)?):(?P<start>\d+)(?:\s*[-:]\s*(?P<end>\d+))?"
)
RANGE = re.compile(r"^\s*(?P<start>\d+)\s*(?:[-:]\s*(?P<end>\d+))?\s*$")
FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class HandoffStatus(StrEnum):
    DONE = "done"
    PARTIAL = "partial"
    BLOCKED = "blocked"


class HandoffSource(StrEnum):
    JSON = "json"
    FALLBACK = "fallback"
    SALVAGE = "salvage"


@dataclass(frozen=True, slots=True)
class Fact:
    path: str
    start: int
    end: int
    claim: str = ""
    line_hash: str = ""
    stale: bool = False

    @property
    def anchor(self) -> str:
        return f"{self.path}:{self.start}-{self.end}"


@dataclass(frozen=True, slots=True)
class VerifyResult:
    command: str
    exit_code: int | None
    seconds: float = 0.0
    errors: tuple[str, ...] = ()
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass(frozen=True, slots=True)
class HandoffPlan:
    edit: tuple[str, ...] = ()
    read: tuple[str, ...] = ()
    verify: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RoleHandoff:
    role: str
    engine: str = ""
    model: str = ""
    status: HandoffStatus = HandoffStatus.PARTIAL
    source: HandoffSource = HandoffSource.FALLBACK
    summary: str = ""
    decisions: tuple[str, ...] = ()
    facts: tuple[Fact, ...] = ()
    plan: HandoffPlan = field(default_factory=HandoffPlan)
    files_changed: tuple[str, ...] = ()
    verification: tuple[VerifyResult, ...] = ()
    open_questions: tuple[str, ...] = ()
    next_step: str = ""
    run_id: str = ""
    capsule: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class HandoffChain:
    handoffs: tuple[RoleHandoff, ...] = ()
    facts: tuple[Fact, ...] = ()
    plan: HandoffPlan = field(default_factory=HandoffPlan)
    decisions: tuple[str, ...] = ()
    files_changed: tuple[str, ...] = ()
    verification: tuple[VerifyResult, ...] = ()
    open_questions: tuple[str, ...] = ()
    next_step: str = ""

    @property
    def covered(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(fact.path for fact in self.facts if not fact.stale))


def handoff_budget(depth: str) -> int:
    return HANDOFF_BUDGETS.get(depth or DEFAULT_DEPTH, HANDOFF_BUDGETS[DEFAULT_DEPTH])


def estimate_tokens(text: str) -> int:
    return (len(text.encode("utf-8")) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def last_json_object(text: str) -> Mapping[str, object] | None:
    tail = text[-SCAN_LIMIT:]
    for block in reversed(FENCE.findall(tail)):
        found = _object(block)
        if found is not None:
            return found
    decoder = json.JSONDecoder()
    position = len(tail)
    attempts = 0
    while attempts < ATTEMPT_LIMIT:
        position = tail.rfind("{", 0, position)
        if position < 0:
            return None
        attempts += 1
        try:
            value, end = decoder.raw_decode(tail, position)
        except ValueError:
            continue
        if isinstance(value, dict) and _trailing(tail[end:]):
            return value
    return None


def _trailing(text: str) -> bool:
    return not text.strip().strip("`").strip()


def _object(text: str) -> Mapping[str, object] | None:
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _clip(value: object, limit: int = ITEM_LIMIT) -> str:
    text = " ".join(str(value).split()) if value is not None else ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _texts(value: object, limit: int = ITEM_LIMIT) -> tuple[str, ...]:
    if isinstance(value, str):
        items: Iterable[object] = (value,)
    elif isinstance(value, list | tuple):
        items = value
    else:
        return ()
    cleaned = (_clip(item, limit) for item in items if isinstance(item, str | int | float))
    return tuple(dict.fromkeys(item for item in cleaned if item))


def _paths(value: object) -> tuple[str, ...]:
    return tuple(path.replace("\\", "/").removeprefix("./") for path in _texts(value, 400))


def _number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def parse_anchor(text: str) -> tuple[str, int, int] | None:
    match = ANCHOR.match(text.strip().replace("\\", "/"))
    if match is None:
        return None
    start = int(match["start"])
    end = int(match["end"]) if match["end"] else start
    if start < 1 or end < start:
        return None
    return match["path"].removeprefix("./"), start, end


def _fact(item: object) -> Fact | None:
    if isinstance(item, str):
        anchor = parse_anchor(item)
        if anchor is None:
            return None
        claim = ANCHOR.sub("", item.strip().replace("\\", "/"), count=1).lstrip(" -—:").strip()
        return Fact(anchor[0], anchor[1], anchor[2], _clip(claim))
    if not isinstance(item, dict):
        return None
    claim = _clip(item.get("claim") or item.get("fact") or item.get("text") or "")
    anchor_text = item.get("anchor")
    if isinstance(anchor_text, str) and (anchor := parse_anchor(anchor_text)) is not None:
        return Fact(anchor[0], anchor[1], anchor[2], claim)
    path = item.get("path") or item.get("file")
    if not isinstance(path, str) or not path.strip():
        return None
    start, end = _number(item.get("start")), _number(item.get("end"))
    lines = item.get("lines")
    if start is None and isinstance(lines, str) and (found := RANGE.match(lines)) is not None:
        start = int(found["start"])
        end = int(found["end"]) if found["end"] else start
    if start is None:
        anchor = parse_anchor(path)
        if anchor is None:
            return None
        return Fact(anchor[0], anchor[1], anchor[2], claim)
    end = end if end is not None and end >= start else start
    if start < 1:
        return None
    return Fact(path.strip().replace("\\", "/").removeprefix("./"), start, end, claim)


def _facts(value: object) -> tuple[Fact, ...]:
    if not isinstance(value, list):
        return ()
    found = (_fact(item) for item in value)
    return tuple(item for item in found if item is not None)


def anchored_facts(value: object) -> tuple[Fact, ...]:
    return _facts(value)


def handoff_texts(value: object, limit: int = ITEM_LIMIT) -> tuple[str, ...]:
    return _texts(value, limit)


def handoff_paths(value: object) -> tuple[str, ...]:
    return _paths(value)


def handoff_status(value: object, fallback: HandoffStatus) -> HandoffStatus:
    return _status(value, fallback)


def clip_text(value: object, limit: int = ITEM_LIMIT) -> str:
    return _clip(value, limit)


def _plan(value: object) -> HandoffPlan:
    if not isinstance(value, dict):
        return HandoffPlan()
    return HandoffPlan(
        _paths(value.get("edit")),
        _paths(value.get("read")),
        _texts(value.get("verify"), 400),
    )


def _status(value: object, fallback: HandoffStatus) -> HandoffStatus:
    if isinstance(value, str) and value.strip().lower() in {item.value for item in HandoffStatus}:
        return HandoffStatus(value.strip().lower())
    return fallback


def _summary(text: str) -> str:
    paragraphs = [part.strip() for part in text.strip().split("\n\n") if part.strip()]
    tail = paragraphs[-1] if paragraphs else ""
    return _clip(tail, SUMMARY_LIMIT)


def build_handoff(
    text: str,
    role: str,
    *,
    engine: str = "",
    model: str = "",
    changed: Sequence[str] = (),
    reads: Sequence[tuple[str, int, int]] = (),
    verification: Sequence[VerifyResult] = (),
    run_id: str = "",
    capsule: str = "",
    stopped: str = "",
) -> RoleHandoff:
    data = last_json_object(text)
    read_facts = tuple(Fact(path, start, end, f"read by {role}") for path, start, end in reads)
    base = RoleHandoff(
        role,
        engine,
        model,
        files_changed=tuple(dict.fromkeys(changed)),
        verification=tuple(verification),
        run_id=run_id,
        capsule=capsule,
    )
    if data is None:
        handoff = replace(
            base,
            status=HandoffStatus.PARTIAL,
            source=HandoffSource.FALLBACK,
            summary=_summary(text),
            facts=read_facts,
        )
    else:
        plan = data.get("plan")
        handoff = replace(
            base,
            status=_status(data.get("status"), HandoffStatus.DONE),
            source=HandoffSource.JSON,
            summary=_clip(data.get("summary") or _summary(text), SUMMARY_LIMIT),
            decisions=_texts(data.get("decisions")),
            facts=_facts(data.get("facts")) or read_facts,
            plan=_plan(plan if isinstance(plan, dict) else data),
            open_questions=_texts(data.get("open_questions") or data.get("questions")),
            next_step=_clip(data.get("next_step") or data.get("next") or ""),
            reason=_clip(data.get("blocked_reason") or data.get("reason") or ""),
        )
    if stopped:
        handoff = replace(
            handoff,
            status=HandoffStatus.PARTIAL,
            source=HandoffSource.SALVAGE,
            reason=stopped,
            facts=handoff.facts or read_facts,
        )
    return handoff


def line_digest(lines: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]


def refresh_facts(
    facts: Sequence[Fact], lines_of: Callable[[str], Sequence[str] | None]
) -> tuple[Fact, ...]:
    cache: dict[str, Sequence[str] | None] = {}
    fresh: list[Fact] = []
    for fact in facts:
        if fact.path not in cache:
            cache[fact.path] = lines_of(fact.path)
        lines = cache[fact.path]
        if lines is None or fact.end > len(lines):
            fresh.append(replace(fact, stale=True))
            continue
        digest = line_digest(lines[fact.start - 1 : fact.end])
        if fact.line_hash and fact.line_hash != digest:
            fresh.append(replace(fact, stale=True))
        else:
            fresh.append(replace(fact, line_hash=digest, stale=False))
    return tuple(fresh)


def merge_chain(handoffs: Sequence[RoleHandoff]) -> HandoffChain:
    facts: dict[tuple[str, int, int], Fact] = {}
    edit: dict[str, None] = {}
    read: dict[str, None] = {}
    verify: dict[str, None] = {}
    decisions: list[str] = []
    changed: dict[str, None] = {}
    results: dict[str, VerifyResult] = {}
    for handoff in handoffs:
        for fact in handoff.facts:
            facts.pop((fact.path, fact.start, fact.end), None)
            facts[fact.path, fact.start, fact.end] = fact
        edit.update(dict.fromkeys(handoff.plan.edit))
        read.update(dict.fromkeys(handoff.plan.read))
        verify.update(dict.fromkeys(handoff.plan.verify))
        decisions.extend(f"{handoff.role}: {item}" for item in handoff.decisions)
        changed.update(dict.fromkeys(handoff.files_changed))
        for result in handoff.verification:
            results.pop(result.command, None)
            results[result.command] = result
    last = handoffs[-1] if handoffs else None
    return HandoffChain(
        tuple(handoffs),
        tuple(facts.values()),
        HandoffPlan(tuple(edit), tuple(path for path in read if path not in edit), tuple(verify)),
        tuple(dict.fromkeys(decisions)),
        tuple(changed),
        tuple(results.values()),
        last.open_questions if last is not None else (),
        last.next_step if last is not None else "",
    )


def refresh_chain(
    chain: HandoffChain, lines_of: Callable[[str], Sequence[str] | None]
) -> HandoffChain:
    return replace(chain, facts=refresh_facts(chain.facts, lines_of))


def _verification_line(result: VerifyResult) -> str:
    if result.timed_out:
        state = "timed out"
    elif result.exit_code is None:
        state = "did not start"
    else:
        state = f"exit {result.exit_code}"
    head = f"- `{result.command}`: {state}, {result.seconds:.1f}s"
    return "\n".join((head, *(f"  {line}" for line in result.errors)))


def render_chain(chain: HandoffChain, budget_tokens: int) -> str:
    if not chain.handoffs:
        return ""
    required: list[str] = ["Roles so far:"]
    for handoff in chain.handoffs:
        who = "/".join(part for part in (handoff.engine, handoff.model) if part)
        state = handoff.status.value + (f" ({handoff.reason})" if handoff.reason else "")
        full = f"; full text: cuanta cat {handoff.capsule}" if handoff.capsule else ""
        required.append(f"- {handoff.role} [{who}] {state}: {handoff.summary}{full}")
    if chain.next_step:
        required.append(f"Next step: {chain.next_step}")
    failed = [item for item in chain.verification if not item.passed]
    sections: list[tuple[str, list[str]]] = [
        ("Verification failures:", [_verification_line(item) for item in failed]),
        ("Plan, edit:", [f"- {path}" for path in chain.plan.edit]),
        ("Plan, verify:", [f"- {command}" for command in chain.plan.verify]),
        (
            "Anchored facts (open these ranges; do not re-read the whole file):",
            [
                f"- {fact.anchor} {fact.claim}".rstrip()
                for fact in reversed(chain.facts)
                if not fact.stale
            ],
        ),
        ("Decisions:", [f"- {item}" for item in chain.decisions]),
        ("Open questions:", [f"- {item}" for item in chain.open_questions]),
        (
            "Stale facts (the file changed; re-read these ranges before relying on them):",
            [f"- {fact.anchor} {fact.claim}".rstrip() for fact in chain.facts if fact.stale],
        ),
        (
            "Files changed so far (from cuanta's snapshots):",
            [f"- {path}" for path in chain.files_changed],
        ),
        ("Plan, read:", [f"- {path}" for path in chain.plan.read]),
        (
            "Verification passed:",
            [_verification_line(item) for item in chain.verification if item.passed],
        ),
    ]
    limit = max(budget_tokens, 1) * CHARS_PER_TOKEN - OMISSION_RESERVE
    lines = list(required)
    used = sum(_size(line) for line in lines)
    omitted = 0
    for title, items in sections:
        if not items:
            continue
        if used + _size(title) > limit:
            omitted += len(items)
            continue
        lines.append(title)
        used += _size(title)
        for item in items:
            if used + _size(item) > limit:
                omitted += 1
                continue
            lines.append(item)
            used += _size(item)
    if omitted:
        lines.append(f"({omitted} more items omitted to fit the handoff budget; see the capsules.)")
    return "\n".join(lines)


def _size(line: str) -> int:
    return len(line.encode("utf-8")) + 1


def schema_instruction() -> str:
    return (
        "End your answer with one JSON object, your handoff for the next role: "
        '{"summary": "...", "decisions": ["..."], '
        '"facts": [{"path": "src/file.ts", "start": 12, "end": 30, "claim": "..."}], '
        '"plan": {"edit": ["path"], "read": ["path"], "verify": ["command"]}, '
        '"open_questions": ["..."], "next_step": "...", "status": "done|partial|blocked"}. '
        "Facts must cite line ranges you actually read."
    )
