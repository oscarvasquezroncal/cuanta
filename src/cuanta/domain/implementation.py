from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from cuanta.domain.engine import (
    BUDGET_LIMIT_SUBTYPE,
    COST_UNKNOWN_SUBTYPE,
    GOVERNOR_STOP_SUBTYPE,
    WALL_LIMIT_SUBTYPE,
    cut_by_turns,
)
from cuanta.domain.mandate import MandateType
from cuanta.domain.role_handoff import VerifyResult, last_json_object
from cuanta.domain.routing import Provider
from cuanta.domain.stable import stable_json

MAX_REPAIRS = 3
MAX_STEPS = 8
LARGE_EDIT_TOKENS = 3_000
MIN_REPAIR_HEADROOM_USD = 0.05
REPAIR_FEEDBACK_BYTES = 8_000
REPAIR_ENTRY_BYTES = 1_000
REPAIR_NOTE_BYTES = 150
CLIPPED = "..."
DIAGNOSTICS_VERSION = "5"
DIAGNOSTIC_LOCATION = re.compile(r"^(.*?):\d+(?::\d+)?(?:\s+|$)")
FAILURE_ORDINAL = re.compile(r"^\d+\) ")
TRAILING_DURATION = re.compile(r"\s[\(\[]?\d+(?:\.\d+)?\s?m?s[\)\]]?$")
LIFECYCLE_LINE = re.compile(
    r"\bnpm (?:ERR!|error)(?:\s|$)|\berror Command failed\b|\bELIFECYCLE\b|\bERR_PNPM_"
    r'|\berror: script "[^"]*" exited with code\b|\bcommand finished with error\b'
    r"|\brun failed: command\b|^Failed:\s+\S+#\S+$"
)
SILENT_FAILURE = "fails without diagnostics"
VERIFICATION_OUTPUTS = frozenset(
    {".nyc_output", ".turbo", "coverage", "htmlcov", "playwright-report", "test-results"}
)
VERIFICATION_REPORTS = frozenset({".coverage", ".eslintcache", "coverage.xml", "junit.xml"})
PACKAGE_MANIFESTS = frozenset({"package.json", "pyproject.toml"})


class ImplementationProfile(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"


AUTO_PROFILE = "auto"
PROFILE_CHOICES = (
    AUTO_PROFILE,
    ImplementationProfile.BALANCED.value,
    ImplementationProfile.FAST.value,
)


@dataclass(frozen=True, slots=True)
class FastChoice:
    model: str
    variant: str


SMALL_FEATURE_CHOICE = FastChoice("claude-opus-5-5", "low")
STEPPED_FEATURE_CHOICE = FastChoice("claude-opus-5-5", "low")
FIX_CHOICE = FastChoice("claude-opus-5-5", "high")


def large_feature(task_type: str, edit_tokens: int) -> bool:
    return task_type == MandateType.FEATURE and edit_tokens >= LARGE_EDIT_TOKENS


def fast_choice(task_type: str, large: bool) -> FastChoice | None:
    if task_type == MandateType.BUG:
        return FIX_CHOICE
    if task_type == MandateType.FEATURE:
        return STEPPED_FEATURE_CHOICE if large else SMALL_FEATURE_CHOICE
    return None


def resolve_profile(
    value: str, default: str, engine: str, task_type: str, team: bool
) -> ImplementationProfile:
    chosen = value or default or AUTO_PROFILE
    if chosen != AUTO_PROFILE:
        return ImplementationProfile(chosen)
    if engine == Provider.CLAUDE and not team and fast_choice(task_type, False) is not None:
        return ImplementationProfile.FAST
    return ImplementationProfile.BALANCED


@dataclass(frozen=True, slots=True)
class VerificationDelta:
    results: tuple[VerifyResult, ...]
    introduced: tuple[str, ...] = ()
    preexisting: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return bool(self.results) and not self.introduced


STOPPED_STATE = "stopped"
UNSETTLED_STATES = frozenset({"running", "repairing"})
SESSION_CLOSED = "session_closed"
USER_STOP = "stopped"
ENGINE_STOPS = frozenset({WALL_LIMIT_SUBTYPE, BUDGET_LIMIT_SUBTYPE, GOVERNOR_STOP_SUBTYPE})


@dataclass(frozen=True, slots=True)
class ImplementationStep:
    title: str
    state: str = "pending"


def settled_state(state: str) -> str:
    return STOPPED_STATE if state in UNSETTLED_STATES else state


@dataclass(frozen=True, slots=True)
class ImplementationReport:
    checks: tuple[VerificationDelta, ...] = ()
    steps: tuple[ImplementationStep, ...] = ()
    repairs: int = 0
    reason: str = ""
    repair_limit: int = MAX_REPAIRS
    time_limit_s: float = 900.0
    elapsed_s: float = 0.0
    engine_subtype: str = ""
    exit_code: int | None = None
    cap_usd: float = 0.0
    max_turns: int = 0
    repair_elapsed_s: float = 0.0

    @property
    def passed(self) -> bool:
        return (
            bool(self.checks)
            and self.checks[-1].passed
            and bool(self.steps)
            and all(step.state == "green" for step in self.steps)
            and not self.reason
        )

    def payload(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "repairs": self.repairs,
            "reason": self.reason,
            "engine_subtype": self.engine_subtype,
            "repair_limit": self.repair_limit,
            "repair_rounds_remaining": max(0, self.repair_limit - self.repairs),
            "time_limit_s": self.time_limit_s,
            "elapsed_s": self.elapsed_s,
            "repair_elapsed_s": self.repair_elapsed_s,
            "exit_code": self.exit_code,
            "cap_usd": self.cap_usd,
            "max_turns": self.max_turns,
            "steps": [{"title": step.title, "state": step.state} for step in self.steps],
            "checks": [
                {
                    "introduced": list(check.introduced),
                    "preexisting": list(check.preexisting),
                    "results": [
                        {
                            "command": result.command,
                            "exit_code": result.exit_code,
                            "seconds": result.seconds,
                            "errors": list(result.errors),
                            "timed_out": result.timed_out,
                        }
                        for result in check.results
                    ],
                }
                for check in self.checks
            ],
        }


def diagnostic_identity(error: str) -> str:
    return DIAGNOSTIC_LOCATION.sub(r"\1 ", FAILURE_ORDINAL.sub("", error, count=1))


def without_duration(text: str) -> str:
    return TRAILING_DURATION.sub("", text)


def verification_delta(
    baseline: Sequence[VerifyResult], current: Sequence[VerifyResult], commands: Sequence[str]
) -> VerificationDelta:
    previous = {result.command: result for result in baseline}
    introduced: list[str] = []
    preexisting: list[str] = []
    completed = {result.command for result in current}
    for command in commands:
        if command not in completed:
            introduced.append(f"{command}: verification did not finish")
    for result in current:
        if result.passed:
            continue
        before = previous.get(result.command)
        reusable = before is not None and before.exit_code is not None and not before.timed_out
        known = Counter(
            diagnostic_identity(error)
            for error in (before.errors if before is not None and reusable else ())
        )
        if result.timed_out or result.exit_code is None:
            introduced.append(f"{result.command}: verification unavailable or timed out")
        elif not result.errors:
            introduced.append(f"{result.command}: failed with exit code {result.exit_code}")
        for error in result.errors:
            identity = diagnostic_identity(error)
            target = preexisting if known[identity] > 0 else introduced
            known[identity] -= 1
            target.append(f"{result.command}: {error}")
    return VerificationDelta(tuple(current), tuple(introduced), tuple(preexisting))


def unavailable_checks(results: Sequence[VerifyResult], commands: Sequence[str]) -> tuple[str, ...]:
    finished = {result.command for result in results}
    missing = tuple(f"{command} (did not run)" for command in commands if command not in finished)
    blocked = tuple(
        f"{result.command} ({_unavailable_reason(result)})"
        for result in results
        if result.exit_code is None or result.timed_out or _silent(result)
    )
    return (*missing, *blocked)


def _silent(result: VerifyResult) -> bool:
    return not result.passed and all(LIFECYCLE_LINE.search(error) for error in result.errors)


def _unavailable_reason(result: VerifyResult) -> str:
    if result.timed_out:
        return "timed out"
    if result.exit_code is not None:
        return SILENT_FAILURE
    return result.errors[0] if result.errors else "could not start"


def engine_stop_reason(subtype: str, terminal_reason: str = "") -> str:
    if subtype == WALL_LIMIT_SUBTYPE:
        return "wall_limit"
    if "budget" in subtype or subtype == GOVERNOR_STOP_SUBTYPE:
        return "cost_limit"
    if cut_by_turns(subtype, terminal_reason):
        return "turn_limit"
    if subtype == COST_UNKNOWN_SUBTYPE:
        return "cost_unknown"
    return "engine_failed"


def baseline_key(snapshot: Mapping[str, str], commands: Sequence[str], context: str = "") -> str:
    return hashlib.sha256(
        stable_json((dict(snapshot), tuple(commands), context)).encode()
    ).hexdigest()


def verification_output(path: str, roots: frozenset[str]) -> bool:
    parent, _, name = path.rpartition("/")
    if name.endswith(".tsbuildinfo") or (name in VERIFICATION_REPORTS and parent in roots):
        return True
    parts = path.split("/")
    return any(
        part in VERIFICATION_OUTPUTS and "/".join(parts[:index]) in roots
        for index, part in enumerate(parts[:-1])
    )


def implementation_fingerprint(snapshot: Mapping[str, str]) -> str:
    paths = {path: path.replace("\\", "/") for path in snapshot}
    roots = frozenset(
        {
            "",
            *(
                parent
                for parent, _, name in (path.rpartition("/") for path in paths.values())
                if name in PACKAGE_MANIFESTS
            ),
        }
    )
    return baseline_key(
        {
            path: digest
            for path, digest in snapshot.items()
            if not verification_output(paths[path], roots)
        },
        (),
    )


def planned_steps(text: str) -> tuple[ImplementationStep, ...]:
    payload = last_json_object(text)
    values = payload.get("implementation_steps") if payload is not None else None
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_STEPS:
        return ()
    titles: list[str] = []
    for value in values:
        title = value.get("title") if isinstance(value, dict) else value
        if not isinstance(title, str) or not title.strip() or len(title) > 500:
            return ()
        titles.append(" ".join(title.split()))
    return tuple(ImplementationStep(title) for title in titles)


def clipped_entry(error: str, maximum_bytes: int) -> str:
    encoded = error.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return error
    kept = encoded[: max(0, maximum_bytes - len(CLIPPED))].decode("utf-8", "ignore")
    return kept + CLIPPED


def repair_feedback(errors: Sequence[str], maximum_bytes: int = REPAIR_FEEDBACK_BYTES) -> str:
    room = maximum_bytes - REPAIR_NOTE_BYTES
    entry_bytes = min(REPAIR_ENTRY_BYTES, room - 1)
    rows: list[str] = []
    used = 0
    for error in errors:
        row = clipped_entry(error, entry_bytes)
        size = len(row.encode("utf-8")) + 1
        if used + size > room:
            continue
        rows.append(row)
        used += size
    omitted = len(errors) - len(rows)
    if omitted:
        rows.append(
            f"[{omitted} further errors retained in the full verification report; all must pass.]"
        )
    return "\n".join(rows)


def implementation_prompt(stepped: bool, fast: bool) -> str:
    common = (
        "Cuanta verifies your work after each response and sends any introduced errors back "
        "as a new turn in this same session. Existing baseline errors are reported separately. "
        "Read several anchored ranges in parallel per turn; begin editing once the edit set "
        "is confirmed. Preserve project rules and the request scope. Never run git. "
        "Do not claim checks pass before cuanta verifies them."
    )
    if fast:
        common += (
            " Work directly from the supplied pack in this one native session. Do not delegate "
            "or update documentation. Add or update tests when the request asks for tests."
        )
    if stepped:
        common += (
            " This is a large feature. Your FIRST response must only contain JSON: "
            '{"implementation_steps":[{"title":"first independently verifiable step"},'
            '{"title":"next step"}]}. Plan at most eight ordered steps; do not edit yet. '
            "Cuanta will authorize one step per turn. Stop after that step; never begin the "
            "next until cuanta confirms the current step is green."
        )
    return common


def project_rules(sources: Mapping[str, str]) -> str:
    rows: list[str] = []
    for path, text in sorted(sources.items()):
        if "tsconfig" in path:
            try:
                payload = json.loads(text)
            except ValueError:
                payload = None
            options = payload.get("compilerOptions") if isinstance(payload, dict) else None
            if isinstance(options, dict):
                rows.append(f"{path}: compilerOptions={stable_json(options)[:1600]}")
            else:
                rows.append(f"{path}: {' '.join(text.split())[:1000]}")
        else:
            rows.append(f"{path}: {' '.join(text.split())[:1600]}")
        if "react" in text.lower():
            rows.append(
                "React hooks: call hooks unconditionally; include effect dependencies; avoid "
                "synchronous setState in effects (react-hooks/set-state-in-effect). Derive "
                "render state during render or update it from events when possible."
            )
    return "Project lint and TypeScript rules:\n" + "\n".join(dict.fromkeys(rows)) if rows else ""
