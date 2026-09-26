from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from cuanta.domain.affected import TERM, is_test_file, normalized, related_tests
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import Message, msg

ERROR_HINTS = re.compile(
    r"(error|exception|traceback|failed|fails|failing|assert|expected|returns|stack|panic|"
    r"\bline \d+|\.py:\d+|status \d{3}|exit code|timeout|crash)",
    re.IGNORECASE,
)
PLACE_HINTS = re.compile(r"([\w./-]+\.(py|ts|tsx|js|go|rs|java|rb|cs|kt)\b|[\w-]+/[\w./-]+)")
EXPECTED_HINTS = re.compile(
    r"\b(should|expected|expect|must|so that|instead of|return|returns|make .* pass)\b",
    re.IGNORECASE,
)
SPLIT_HINTS = re.compile(r"\b(and also|also|as well as|plus|then also)\b|;", re.IGNORECASE)
ACTION_VERBS = frozenset(
    {
        "add",
        "fix",
        "remove",
        "rename",
        "refactor",
        "update",
        "migrate",
        "create",
        "delete",
        "implement",
        "change",
        "replace",
        "write",
        "move",
        "split",
        "support",
    }
)
DO_NOT_BREAK = re.compile(r"^#+\s*(\d+\.\s*)?do not break\b", re.IGNORECASE)
SUGGESTION_LIMIT = 5
MIN_EVIDENCE = 20


class Gap(StrEnum):
    EVIDENCE = "evidence"
    PLACE = "place"
    EXPECTED = "expected"
    SPLIT = "split"


class ChipAction(StrEnum):
    LAST_FAILURE = "last_failure"
    PICK_FILE = "pick_file"
    SPLIT = "split"
    EXAMPLE = "example"


GAP_ACTIONS: Mapping[Gap, ChipAction] = {
    Gap.EVIDENCE: ChipAction.LAST_FAILURE,
    Gap.PLACE: ChipAction.PICK_FILE,
    Gap.EXPECTED: ChipAction.EXAMPLE,
    Gap.SPLIT: ChipAction.SPLIT,
}


@dataclass(frozen=True, slots=True)
class Chip:
    gap: Gap
    message: Message
    action: ChipAction
    confidence: float


@dataclass(frozen=True, slots=True)
class Clarity:
    score: float
    chips: tuple[Chip, ...]
    backend: str
    cost_usd: float | None = 0.0

    @property
    def level(self) -> str:
        if self.score >= 1.5:
            return "clear"
        return "fair" if self.score >= 0.75 else "unclear"


CLARITY_GATE = 0.6
VAGUE_WORDS = 4


@dataclass(frozen=True, slots=True)
class Suggestions:
    files: tuple[str, ...]
    tests: tuple[str, ...]
    out_of_scope: tuple[str, ...]
    example: MandateRequest | None


EXAMPLES: Mapping[str, MandateRequest] = {
    "bug": MandateRequest(
        type="bug",
        what="Fix the cart total so discounts apply before tax",
        why="tests/test_cart.py::test_total fails: expected 10.80, got 11.88",
        where="src/shop/cart.py",
        tests="tests/test_cart.py::test_total passes and the rest stay green",
        out_of_scope="the public API of Cart and the tax tables",
    ),
    "feature": MandateRequest(
        type="feature",
        what="Add CSV export to the orders page",
        why="Support asks for a way to hand orders to accounting",
        where="src/shop/orders.py and src/shop/export.py",
        tests="a new test proves the CSV header and one row",
        out_of_scope="the existing JSON export",
    ),
    "refactor": MandateRequest(
        type="refactor",
        what="Split PaymentService into a gateway client and a retry policy",
        why="PaymentService is 900 lines and every change breaks retries",
        where="src/shop/payments/",
        tests="the payment tests pass unchanged",
        out_of_scope="behaviour: no change a user can see",
    ),
    "investigation": MandateRequest(
        type="investigation",
        what="Find why checkout is slow on the first request after deploy",
        why="p95 latency is 4 s for the first request, 300 ms afterwards",
        where="src/shop/checkout.py, src/shop/db.py",
        tests="a written finding with the cause and a proposed fix",
        out_of_scope="changing production settings",
    ),
}


def content_key(request: MandateRequest) -> str:
    parts = (request.type, request.what, request.why, request.where, request.tests)
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def has_evidence(request: MandateRequest) -> bool:
    kind = request.type
    source = {"feature": request.tests, "refactor": request.constraints}.get(kind, request.why)
    text = source.strip()
    if kind in {"feature", "refactor", "investigation"}:
        return len(text) >= MIN_EVIDENCE
    return len(text) >= MIN_EVIDENCE and bool(ERROR_HINTS.search(text))


def has_place(request: MandateRequest) -> bool:
    return bool(request.where.strip()) or bool(PLACE_HINTS.search(request.what))


def has_expected(request: MandateRequest) -> bool:
    return bool(request.tests.strip()) or bool(EXPECTED_HINTS.search(request.what))


def bundled(request: MandateRequest) -> bool:
    text = request.what.lower()
    verbs = [word for word in re.findall(r"[a-z]+", text) if word in ACTION_VERBS]
    return bool(SPLIT_HINTS.search(text)) or len(set(verbs)) >= 3 or text.count(" and ") >= 2


PRESENCE = {
    Gap.EVIDENCE: has_evidence,
    Gap.PLACE: has_place,
    Gap.EXPECTED: has_expected,
}


def heuristic_gaps(request: MandateRequest) -> dict[Gap, float]:
    found = {gap: 0.1 if check(request) else 0.9 for gap, check in PRESENCE.items()}
    found[Gap.SPLIT] = 0.85 if bundled(request) else 0.1
    return found


def chips_from(missing: Mapping[Gap, float], threshold: float = 0.5) -> tuple[Chip, ...]:
    return tuple(
        Chip(gap, msg(f"chip.{gap.value}"), GAP_ACTIONS[gap], probability)
        for gap, probability in missing.items()
        if probability >= threshold
    )


def heuristic_clarity(request: MandateRequest) -> float:
    missing = sum(1 for probability in heuristic_gaps(request).values() if probability >= 0.5)
    if not request.what.strip():
        return 0.0
    missing += 1 if len(request.what.split()) < VAGUE_WORDS else 0
    return max(0.0, 2.0 - 0.6 * missing)


def terms_of(request: MandateRequest) -> set[str]:
    text = f"{request.what} {request.where} {request.why}"
    return {term.lower() for term in TERM.findall(text) if len(term) > 3}


def where_suggestions(
    request: MandateRequest,
    files: Iterable[str],
    symbols: Mapping[str, str],
    limit: int = SUGGESTION_LIMIT,
) -> tuple[str, ...]:
    terms = terms_of(request)
    if not terms:
        return ()
    scored: dict[str, int] = {}
    for path in files:
        name = normalized(path)
        if is_test_file(name):
            continue
        parts = {part.lower() for part in re.split(r"[/._-]", name) if part}
        hits = len(terms & parts)
        if hits:
            scored[name] = scored.get(name, 0) + hits * 2
    for symbol, path in symbols.items():
        if symbol.lower() in terms:
            name = normalized(path)
            scored[name] = scored.get(name, 0) + 1
    ranked = sorted(scored, key=lambda name: (-scored[name], name))
    return tuple(ranked[:limit])


def nearby_tests(
    places: Sequence[str],
    files: Iterable[str],
    neighbours: Mapping[str, frozenset[str]],
    limit: int = SUGGESTION_LIMIT,
) -> tuple[str, ...]:
    return related_tests(places, files, neighbours)[:limit]


def do_not_break(rulebook: str | None, limit: int = SUGGESTION_LIMIT) -> tuple[str, ...]:
    if not rulebook:
        return ()
    lines = rulebook.splitlines()
    start = next((index for index, line in enumerate(lines) if DO_NOT_BREAK.match(line)), -1)
    if start < 0:
        return ()
    found: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("#"):
            break
        match = re.match(r"^\s*[-*]\s+(.*)$", line)
        if match:
            text = re.sub(r"`([^`]*)`", r"\1", match.group(1)).strip()
            found.append(text.split(" — ")[0].split(" (")[0].rstrip(".")[:90])
    return tuple(found[:limit])
