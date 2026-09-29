from __future__ import annotations

import math
from collections.abc import Iterable, Set
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import PureWindowsPath

from cuanta.domain.cache_probe import ONE_HOUR_WRITE_FACTOR
from cuanta.domain.change_plan import ChangePlan
from cuanta.domain.costs import median
from cuanta.domain.engine import StepUsage
from cuanta.domain.envelope import RoleForecast, write_rate
from cuanta.domain.ledger import Run
from cuanta.domain.pricing import PER_MILLION, Price
from cuanta.domain.routing import Provider, Role

FINISH_SHARE = 0.85
FINISH_HEADROOM_STEPS = 2
WRAP_UP_STEPS = 2
GROWTH_MIN_REQUESTS = 3
ROTATION_CUTOFF = 0.85
ROTATION_MIN_SAVING_USD = 0.01
NOTE_TOKENS = 600
LOOKAHEAD_LIMIT = 10_000
RATE_MIN_SAMPLES = 3
RESUMED = "resumed"
SALVAGED = "salvaged"
ROTATED = "rotated"
SKIPPED = "skipped"
EDITING_ROLES = frozenset({Role.SENIOR, Role.ORCHESTRATOR})
SCOPED_READ_ROLES = frozenset({Role.ANALYST, Role.SENIOR, Role.ORCHESTRATOR})


class ReactionKind(StrEnum):
    FINISH_NOW = "finish_now"
    CODEX_STOP = "codex_stop"
    ROTATE = "rotate"


class Trigger(StrEnum):
    SHARE = "share"
    HEADROOM = "headroom"
    PROJECTION = "projection"
    FRESH_SESSION = "fresh_session"


@dataclass(frozen=True, slots=True)
class RolePlan:
    role: Role
    provider: Provider
    model: str
    share_usd: float
    planned_requests: int
    edit_paths: tuple[str, ...] = ()
    read_paths: tuple[str, ...] = ()
    price: Price | None = None
    hard_cap_usd: float = 0.0
    ttl_s: int = 0
    usd_per_item: float = 0.0
    usd_per_second: float = 0.0
    fixed_tokens: int = 0
    cards_tokens: int = 0
    rotatable: bool = False

    @property
    def limit_usd(self) -> float:
        caps = [value for value in (self.share_usd, self.hard_cap_usd) if value > 0]
        return min(caps) if caps else 0.0


@dataclass(frozen=True, slots=True)
class RoleProgress:
    requests: int = 0
    spent_usd: float | None = 0.0
    estimated: bool = False
    tokens: int = 0
    context_tokens: int = 0
    growth_tokens: float = 0.0
    growth_requests: int = 0
    output_tokens: float = 0.0
    cache_read_share: float = 0.0
    items: int = 0
    edits: int = 0
    edit_calls: int = 0
    stray_edits: int = 0
    first_edit_step: int = 0
    reads: int = 0
    leaked_reads: int = 0
    elapsed_s: float = 0.0

    @property
    def tokens_per_request(self) -> float:
        return self.tokens / self.requests if self.requests > 0 else 0.0


@dataclass(frozen=True, slots=True)
class Projection:
    spent_usd: float | None
    estimated: bool
    limit_usd: float
    next_usd: float | None
    completion: float
    remaining_steps: int
    at_completion_usd: float | None
    steps_to_cap: int | None
    completion_at_cap: float | None
    leaked_reads: int = 0

    @property
    def cap_first(self) -> bool:
        return (
            self.limit_usd > 0
            and self.remaining_steps > 0
            and self.at_completion_usd is not None
            and self.at_completion_usd > self.limit_usd
        )


@dataclass(frozen=True, slots=True)
class Decision:
    kind: ReactionKind
    trigger: Trigger
    projection: Projection
    saving_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class Reaction:
    role: Role
    kind: ReactionKind
    trigger: Trigger
    at_s: float
    projection: Projection
    saving_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class ReactionTaken:
    reaction: Reaction
    run_id: str
    sent: bool
    outcome: str = ""


def role_plan(
    forecast: RoleForecast,
    provider: Provider,
    share_usd: float,
    change: ChangePlan | None = None,
    price: Price | None = None,
    hard_cap_usd: float = 0.0,
    ttl_s: int = 0,
    usd_per_item: float = 0.0,
    usd_per_second: float = 0.0,
    cards_tokens: int = 0,
    rotatable: bool = False,
) -> RolePlan:
    plan = change if change is not None else ChangePlan()
    edits = tuple(target.path for target in plan.edit)
    writes = forecast.role in EDITING_ROLES and not plan.read_only
    per_item = usd_per_item
    if per_item <= 0 and provider is Provider.CODEX and forecast.requests > 0:
        per_item = (forecast.p50_usd or 0.0) / forecast.requests
    return RolePlan(
        role=forecast.role,
        provider=provider,
        model=forecast.model,
        share_usd=share_usd,
        planned_requests=forecast.requests,
        edit_paths=edits if writes else (),
        read_paths=(*edits, *plan.read) if forecast.role in SCOPED_READ_ROLES else (),
        price=price,
        hard_cap_usd=hard_cap_usd,
        ttl_s=ttl_s,
        usd_per_item=per_item,
        usd_per_second=usd_per_second,
        fixed_tokens=forecast.fixed.tokens,
        cards_tokens=cards_tokens,
        rotatable=rotatable,
    )


def run_seconds(run: Run) -> float | None:
    try:
        seconds = (
            datetime.fromisoformat(run.ended_at) - datetime.fromisoformat(run.started_at)
        ).total_seconds()
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def seconds_rate(runs: Iterable[Run], model: str) -> float:
    rates = [
        run.cost_usd / seconds
        for run in runs
        if run.engine == "codex"
        and run.model == model
        and run.status == "ok"
        and run.cost_usd is not None
        and run.cost_usd > 0
        and (seconds := run_seconds(run)) is not None
    ]
    found = median(rates) if len(rates) >= RATE_MIN_SAMPLES else None
    return found if found is not None else 0.0


def windows_root(root: str) -> bool:
    return bool(PureWindowsPath(root.strip()).drive)


def path_key(value: str, windows: bool) -> str:
    text = value.strip().replace("\\", "/").removeprefix("./")
    return text.casefold() if windows else text


def project_path(value: str, root: str) -> str:
    windows = windows_root(root) or windows_root(value)
    text = value.strip().replace("\\", "/")
    base = root.strip().replace("\\", "/").rstrip("/")
    if base and path_key(text, windows).startswith(path_key(base, windows) + "/"):
        return path_key(text[len(base) + 1 :], windows)
    if windows_root(text) or text.startswith("/"):
        return ""
    return path_key(text, windows)


def read_rate(price: Price) -> float:
    return price.cache_read if price.cache_read is not None else price.input


def step_usd(step: StepUsage, price: Price, provider: Provider, ttl_s: int = 0) -> float:
    usage = step.usage
    five = price.cache_write if price.cache_write is not None else price.input
    split = step.write_5m_tokens + step.write_1h_tokens
    rest = max(usage.cache_write_tokens - split, 0)
    writes = (
        step.write_5m_tokens * five
        + step.write_1h_tokens * price.input * ONE_HOUR_WRITE_FACTOR
        + rest * write_rate(price, provider, ttl_s)
    )
    total = (
        usage.input_tokens * price.input
        + (usage.output_tokens + usage.reasoning_tokens) * price.output
        + usage.cache_read_tokens * read_rate(price)
        + writes
    )
    return total / PER_MILLION


def steps_of(plan: RolePlan, progress: RoleProgress) -> int:
    return progress.items if plan.provider is Provider.CODEX else progress.requests


def codex_spend(plan: RolePlan, items: int, elapsed_s: float) -> float | None:
    found = [
        value
        for value, rate in (
            (items * plan.usd_per_item, plan.usd_per_item),
            (elapsed_s * plan.usd_per_second, plan.usd_per_second),
        )
        if rate > 0
    ]
    return max(found) if found else None


def codex_step_usd(plan: RolePlan, progress: RoleProgress) -> float | None:
    rates = [plan.usd_per_item] if plan.usd_per_item > 0 else []
    if plan.usd_per_second > 0 and progress.items > 0:
        rates.append(plan.usd_per_second * progress.elapsed_s / progress.items)
    return max(rates) if rates else None


def completion(plan: RolePlan, progress: RoleProgress) -> float:
    planned = len(plan.edit_paths)
    if planned > 0:
        return min(1.0, progress.edits / planned)
    if plan.planned_requests > 0:
        return min(1.0, steps_of(plan, progress) / plan.planned_requests)
    return 0.0


def open_ended(plan: RolePlan, progress: RoleProgress) -> bool:
    if plan.edit_paths and progress.edits > 0:
        return False
    return steps_of(plan, progress) >= plan.planned_requests


def remaining_steps(plan: RolePlan, progress: RoleProgress) -> int:
    steps = steps_of(plan, progress)
    planned = len(plan.edit_paths)
    if planned == 0:
        return max(plan.planned_requests - steps, 0)
    left = planned - min(progress.edits, planned)
    if left == 0:
        return 0
    if progress.edits == 0 or progress.first_edit_step <= 0:
        return max(plan.planned_requests - steps, 1)
    pace = max(steps - progress.first_edit_step + 1, 1) / progress.edits
    return max(math.ceil(left * pace), 1)


def ahead_usd(plan: RolePlan, progress: RoleProgress, steps: int) -> float | None:
    if steps <= 0:
        return 0.0
    if plan.provider is Provider.CODEX:
        rate = codex_step_usd(plan, progress)
        return None if rate is None else steps * rate
    price = plan.price
    if price is None:
        if progress.requests == 0 or progress.spent_usd is None:
            return None
        return steps * progress.spent_usd / progress.requests
    read = read_rate(price)
    write = write_rate(price, plan.provider, plan.ttl_s)
    growth = max(progress.growth_tokens, 0.0)
    each = progress.context_tokens * read + growth * write + progress.output_tokens * price.output
    return (steps * each + read * growth * steps * (steps - 1) / 2) / PER_MILLION


def steps_to_cap(plan: RolePlan, progress: RoleProgress, spent: float, limit: float) -> int | None:
    if limit <= 0 or ahead_usd(plan, progress, 1) is None:
        return None
    low, high = 0, LOOKAHEAD_LIMIT
    while low < high:
        middle = (low + high + 1) // 2
        cost = ahead_usd(plan, progress, middle)
        if cost is not None and spent + cost <= limit:
            low = middle
        else:
            high = middle - 1
    return low


def completion_after(current: float, remaining: int, more: int) -> float:
    if remaining <= 0 or more >= remaining:
        return 1.0
    return current + (1.0 - current) * more / remaining


def project(plan: RolePlan, progress: RoleProgress) -> Projection:
    spent = progress.spent_usd
    limit = plan.limit_usd
    remaining = remaining_steps(plan, progress)
    done = completion(plan, progress)
    endless = open_ended(plan, progress)
    ahead = None if endless else ahead_usd(plan, progress, remaining)
    reach = steps_to_cap(plan, progress, spent, limit) if spent is not None else None
    return Projection(
        spent_usd=spent,
        estimated=progress.estimated,
        limit_usd=limit,
        next_usd=ahead_usd(plan, progress, 1),
        completion=done,
        remaining_steps=remaining,
        at_completion_usd=spent + ahead if spent is not None and ahead is not None else None,
        steps_to_cap=reach,
        completion_at_cap=(
            completion_after(done, remaining, reach) if reach is not None and not endless else None
        ),
        leaked_reads=progress.leaked_reads,
    )


def rotation_saving(plan: RolePlan, progress: RoleProgress, remaining: int) -> float | None:
    price = plan.price
    context = progress.context_tokens
    if price is None or remaining <= 0 or context <= 0 or plan.fixed_tokens <= 0:
        return None
    read = read_rate(price)
    write = write_rate(price, plan.provider, plan.ttl_s)
    restart = plan.fixed_tokens + plan.cards_tokens + NOTE_TOKENS
    keep = remaining * context * read
    checkpoint = context * read + NOTE_TOKENS * price.output
    fresh = checkpoint + restart * write + (remaining - 1) * restart * read
    return (keep - fresh) / PER_MILLION


def stop_trigger(plan: RolePlan, projection: Projection) -> Trigger | None:
    spent = projection.spent_usd
    if spent is None or projection.limit_usd <= 0:
        return None
    if plan.share_usd > 0 and spent >= FINISH_SHARE * plan.share_usd:
        return Trigger.SHARE
    upcoming = projection.next_usd
    if upcoming is not None and spent + FINISH_HEADROOM_STEPS * upcoming >= projection.limit_usd:
        return Trigger.HEADROOM
    return None


def finish_due(progress: RoleProgress, projection: Projection) -> bool:
    reach = projection.steps_to_cap
    return (
        projection.cap_first
        and progress.growth_requests >= GROWTH_MIN_REQUESTS
        and reach is not None
        and reach <= FINISH_HEADROOM_STEPS + WRAP_UP_STEPS
    )


def decide(plan: RolePlan, progress: RoleProgress, done: Set[ReactionKind]) -> Decision | None:
    projection = project(plan, progress)
    spent = projection.spent_usd
    stop = ReactionKind.CODEX_STOP if plan.provider is Provider.CODEX else ReactionKind.FINISH_NOW
    if spent is None or projection.limit_usd <= 0 or stop in done:
        return None
    trigger = stop_trigger(plan, projection)
    if trigger is not None:
        return Decision(stop, trigger, projection)
    if plan.provider is Provider.CODEX:
        return None
    if (
        plan.rotatable
        and ReactionKind.ROTATE not in done
        and spent < ROTATION_CUTOFF * plan.share_usd
    ):
        saving = rotation_saving(plan, progress, projection.remaining_steps)
        if saving is not None and saving >= ROTATION_MIN_SAVING_USD:
            return Decision(ReactionKind.ROTATE, Trigger.FRESH_SESSION, projection, saving)
    if finish_due(progress, projection):
        return Decision(ReactionKind.FINISH_NOW, Trigger.PROJECTION, projection)
    return None
