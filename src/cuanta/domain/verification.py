from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass

from cuanta.domain.errors import DomainFailure
from cuanta.domain.messages import Message, msg

VERIFY_MODES = ("auto", "affected", "full", "off")


@dataclass(frozen=True, slots=True)
class VerificationPolicy:
    mode: str = "auto"
    timeout_s: float = 600.0

    def __post_init__(self) -> None:
        if self.mode not in VERIFY_MODES or self.timeout_s < 0:
            raise DomainFailure("verification needs auto, affected, full or off and timeout_s >= 0")

    def description(self, investigation: bool = False) -> Message:
        if self.mode == "off":
            return msg("verify.off")
        if investigation and self.mode in {"auto", "affected"}:
            return msg("verify.readonly")
        kind = msg("verify.full" if self.mode == "full" else "verify.affected")
        wall = (
            msg("verify.unbounded")
            if not self.timeout_s
            else msg("verify.wall", time=duration(self.timeout_s))
        )
        return msg("verify.policy", kind=kind, wall=wall)


@dataclass(frozen=True, slots=True)
class VerificationResult:
    status: str
    reason: Message
    command: str = ""
    runner: str = ""
    seconds: float = 0.0


DEFAULT_VERIFICATION = VerificationPolicy()


def duration(seconds: float) -> str:
    minutes, remainder = divmod(int(seconds), 60)
    return f"{minutes}:{remainder:02d}"


def pytest_parallel(manifest: str, runner: str) -> tuple[str, ...]:
    try:
        data = tomllib.loads(manifest)
    except ValueError:
        return ()
    project = data.get("project", {})
    groups = data.get("dependency-groups", {})
    if not isinstance(project, dict) or not isinstance(groups, dict):
        return ()
    requirements = project.get("dependencies", [])
    optional = project.get("optional-dependencies", {})
    dependencies = list(requirements) if isinstance(requirements, list) else []
    extras = optional.values() if isinstance(optional, dict) else ()
    for values in (*extras, *groups.values()):
        if isinstance(values, list):
            dependencies.extend(values)
    declared = any(
        isinstance(value, str) and re.match(r"pytest[-_]xdist(?:\[|[<>=!~ ;]|$)", value)
        for value in dependencies
    )
    return ("-n", "auto") if runner == "pytest" and declared else ()


def timing_message(meta: Mapping[str, object]) -> Message | None:
    preparation, agent = meta.get("preparation_seconds"), meta.get("agent_seconds")
    if not isinstance(preparation, int | float) or not isinstance(agent, int | float):
        return None
    verification = meta.get("verification")
    seconds = verification.get("seconds") if isinstance(verification, Mapping) else None
    status = verification.get("status") if isinstance(verification, Mapping) else None
    result = (
        duration(seconds)
        if isinstance(seconds, int | float) and status != "skipped"
        else msg("verify.in_session")
        if isinstance(meta.get("implementation"), Mapping) and verification is None
        else msg("verify.skipped")
    )
    return msg(
        "verify.times",
        preparation=duration(preparation),
        agent=duration(agent),
        verification=result,
    )
