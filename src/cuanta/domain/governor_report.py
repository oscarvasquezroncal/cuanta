from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from cuanta.domain.governor import ROTATED, ReactionKind, ReactionTaken
from cuanta.domain.ledger import LedgerEvent

HOOKS = "hooks"
BEST_EFFORT = "best_effort"
MODES = frozenset({HOOKS, BEST_EFFORT})
HOOK_SOURCE = "cuanta_hook"
HOOK_KIND = "read_discipline"
SEARCH_TOOLS = frozenset({"Grep"})
SHELL_TOOLS = frozenset({"Bash", "PowerShell"})


@dataclass(frozen=True, slots=True)
class GovernorEntry:
    kind: str
    role: str
    trigger: str
    at_s: float
    run_id: str
    sent: bool
    spent_usd: float | None
    limit_usd: float
    estimated: bool
    saved_usd: float | None
    outcome: str = ""


@dataclass(frozen=True, slots=True)
class BlockedCalls:
    reads: int = 0
    searches: int = 0
    tests: int = 0
    tokens: int = 0

    @property
    def total(self) -> int:
        return self.reads + self.searches + self.tests


@dataclass(frozen=True, slots=True)
class GovernorSummary:
    reactions: tuple[GovernorEntry, ...] = ()
    blocked: BlockedCalls | None = None
    discipline: tuple[tuple[str, str], ...] = ()
    recorded: bool = False
    hooks: bool = False

    @property
    def blocked_total(self) -> int:
        return self.blocked.total if self.blocked is not None else 0

    @property
    def shown(self) -> bool:
        return bool(self.reactions or self.blocked_total or self.discipline)

    @property
    def saved_usd(self) -> float | None:
        known = [entry.saved_usd for entry in self.reactions if entry.saved_usd is not None]
        return sum(known) if known else None

    @property
    def enforced(self) -> tuple[str, ...]:
        return tuple(role for role, mode in self.discipline if mode == HOOKS)

    @property
    def best_effort(self) -> tuple[str, ...]:
        return tuple(role for role, mode in self.discipline if mode == BEST_EFFORT)


def saved_usd(taken: ReactionTaken) -> float | None:
    reaction = taken.reaction
    if not taken.sent:
        return None
    if reaction.kind is ReactionKind.ROTATE:
        return reaction.saving_usd if taken.outcome == ROTATED else None
    if reaction.kind is ReactionKind.CODEX_STOP:
        final = reaction.projection.at_completion_usd
        return None if final is None else max(final - reaction.projection.limit_usd, 0.0)
    return None


def governor_entry(taken: ReactionTaken) -> GovernorEntry:
    reaction = taken.reaction
    projection = reaction.projection
    return GovernorEntry(
        reaction.kind.value,
        reaction.role.value,
        reaction.trigger.value,
        round(reaction.at_s, 3),
        taken.run_id,
        taken.sent,
        projection.spent_usd,
        projection.limit_usd,
        projection.estimated,
        saved_usd(taken),
        taken.outcome,
    )


def entry_payload(entry: GovernorEntry) -> dict[str, object]:
    return {
        "kind": entry.kind,
        "role": entry.role,
        "trigger": entry.trigger,
        "at_s": entry.at_s,
        "run_id": entry.run_id,
        "sent": entry.sent,
        "spent_usd": entry.spent_usd,
        "limit_usd": entry.limit_usd,
        "estimated": entry.estimated,
        "saved_usd": entry.saved_usd,
        "outcome": entry.outcome or None,
    }


def discipline_modes(pairs: Iterable[tuple[str, str]]) -> dict[str, str]:
    return {role: mode for role, mode in pairs if mode in MODES}


def governor_metrics(
    taken: Sequence[ReactionTaken],
    modes: Mapping[str, str] | None = None,
    recorded: bool = False,
    hooks: bool = False,
) -> dict[str, object]:
    reactions = [entry_payload(governor_entry(item)) for item in taken]
    discipline = {role: mode for role, mode in (modes or {}).items() if mode in MODES}
    if not reactions and not discipline and not recorded and not hooks:
        return {}
    record: dict[str, object] = {"reactions": reactions, "read_discipline": discipline}
    if hooks:
        record[HOOKS] = True
    return record


def governor_summary(
    taken: Sequence[ReactionTaken],
    modes: Mapping[str, str] | None = None,
    blocked: BlockedCalls | None = None,
) -> GovernorSummary:
    return GovernorSummary(
        tuple(governor_entry(item) for item in taken),
        blocked,
        tuple((role, mode) for role, mode in (modes or {}).items() if mode in MODES),
    )


def blocked_payload(blocked: BlockedCalls | None) -> dict[str, object] | None:
    if blocked is None:
        return None
    return {
        "reads": blocked.reads,
        "searches": blocked.searches,
        "tests": blocked.tests,
        "total": blocked.total,
        "tokens_estimate": blocked.tokens,
    }


def governor_payload(summary: GovernorSummary) -> dict[str, object]:
    return {
        "reactions": [entry_payload(entry) for entry in summary.reactions],
        "saved_usd": summary.saved_usd,
        "saved_estimated": True,
        "blocked": blocked_payload(summary.blocked),
        "read_discipline": dict(summary.discipline),
    }


def _avoided(raw: str) -> int:
    if not raw:
        return 0
    try:
        data = json.loads(raw)
    except ValueError:
        return 0
    value = data.get("avoided_tokens_estimate") if isinstance(data, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def blocked_calls(events: Iterable[LedgerEvent]) -> BlockedCalls:
    reads = searches = tests = tokens = 0
    seen: set[tuple[str, str]] = set()
    for event in events:
        if event.source != HOOK_SOURCE or event.kind != HOOK_KIND:
            continue
        key = (event.run_id, event.tool_use_id)
        if event.tool_use_id and key in seen:
            continue
        seen.add(key)
        if event.tool_name == "Read":
            reads += 1
            tokens += _avoided(event.raw)
        elif event.tool_name in SEARCH_TOOLS:
            searches += 1
        elif event.tool_name in SHELL_TOOLS:
            tests += 1
    return BlockedCalls(reads, searches, tests, tokens)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _text(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    return value if isinstance(value, str) else ""


def _entry(row: Mapping[str, object]) -> GovernorEntry | None:
    kind = _text(row, "kind")
    role = _text(row, "role")
    if not kind or not role:
        return None
    return GovernorEntry(
        kind,
        role,
        _text(row, "trigger"),
        _number(row.get("at_s")) or 0.0,
        _text(row, "run_id"),
        row.get("sent") is True,
        _number(row.get("spent_usd")),
        _number(row.get("limit_usd")) or 0.0,
        row.get("estimated") is True,
        _number(row.get("saved_usd")),
        _text(row, "outcome"),
    )


def parse_governor(value: object, blocked: BlockedCalls | None = None) -> GovernorSummary:
    data: Mapping[str, object] = value if isinstance(value, dict) else {}
    rows = data.get("reactions")
    entries = (
        tuple(entry for row in rows if isinstance(row, dict) and (entry := _entry(row)) is not None)
        if isinstance(rows, list)
        else ()
    )
    modes = data.get("read_discipline")
    discipline = (
        tuple((str(role), mode) for role, mode in modes.items() if mode in MODES)
        if isinstance(modes, dict)
        else ()
    )
    return GovernorSummary(
        entries, blocked, discipline, isinstance(rows, list), data.get(HOOKS) is True
    )
