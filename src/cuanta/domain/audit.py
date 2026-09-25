from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from cuanta.domain.messages import Message, english, msg

DATE_SUFFIX = re.compile(r"-\d{8}$")
BRACKET_SUFFIX = re.compile(r"\[[^\]]*\]$")
VERSION_TAIL = re.compile(r"[-.]\d+([-.]\d+)*$")
MAIN_AGENT = "main"


class AuditStatus(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    NOT_SEEN = "not_seen"
    NOT_RUN = "not_run"


@dataclass(frozen=True, slots=True)
class AuditRow:
    agent: str
    role: str
    planned: str
    actual: tuple[str, ...]
    status: AuditStatus
    cause: Message

    @property
    def ok(self) -> bool:
        return self.status is AuditStatus.MATCH

    @property
    def cause_text(self) -> str:
        return english(self.cause)


def normalized(model: str) -> str:
    name = BRACKET_SUFFIX.sub("", model.strip().lower())
    name = name.split("/")[-1]
    return DATE_SUFFIX.sub("", name)


def family(model: str) -> str:
    return VERSION_TAIL.sub("", normalized(model))


def same_model(planned: str, actual: str) -> bool:
    left, right = normalized(planned), normalized(actual)
    return bool(left) and (left == right or right.startswith(f"{left}-"))


def audit(
    planned: Mapping[str, tuple[str, str]],
    observed: Mapping[str, tuple[str, ...]],
    env_override: str = "",
    invocation_models: Mapping[str, str] | None = None,
    delegated: frozenset[str] | None = None,
) -> tuple[AuditRow, ...]:
    per_call = invocation_models or {}
    unexpected = sorted(set(observed) - set(planned) - {MAIN_AGENT})
    rows: list[AuditRow] = []
    for agent, (role, model) in planned.items():
        actual = tuple(dict.fromkeys(observed.get(agent, ())))
        if not actual and delegated is not None and agent != MAIN_AGENT and agent not in delegated:
            rows.append(AuditRow(agent, role, model, (), AuditStatus.NOT_RUN, msg("audit.not_run")))
            continue
        if not actual:
            cause = (
                msg("audit.name_mismatch", agents=", ".join(unexpected))
                if unexpected
                else msg("audit.not_seen")
            )
            rows.append(AuditRow(agent, role, model, (), AuditStatus.NOT_SEEN, cause))
            continue
        if all(same_model(model, item) for item in actual):
            rows.append(AuditRow(agent, role, model, actual, AuditStatus.MATCH, msg("audit.ok")))
            continue
        if env_override:
            cause = msg("audit.env", variable=env_override)
        elif agent in per_call:
            cause = msg("audit.invocation", model=per_call[agent])
        elif any(family(model) == family(item) for item in actual):
            cause = msg("audit.version", planned=model, actual=", ".join(actual))
        else:
            cause = msg("audit.unknown")
        rows.append(AuditRow(agent, role, model, actual, AuditStatus.MISMATCH, cause))
    return tuple(rows)


def summary(rows: Sequence[AuditRow]) -> Message:
    ran = [row for row in rows if row.status is not AuditStatus.NOT_RUN]
    matched = sum(1 for row in ran if row.ok)
    return msg("audit.summary", matched=matched, total=len(ran))
