from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from cuanta.domain.anatomy import AnatomyReport
from cuanta.domain.bench import (
    LEAN_SESSION,
    BenchTask,
    Condition,
    PlannedRun,
    ProofRecord,
    RunMetrics,
)
from cuanta.domain.change_plan import ChangePlan, path_matches
from cuanta.domain.config import Config
from cuanta.domain.engine import BUDGET_LIMIT_SUBTYPE
from cuanta.domain.errors import DomainFailure
from cuanta.domain.governor import (
    FINISH_HEADROOM_STEPS,
    FINISH_SHARE,
    ReactionKind,
    ReactionTaken,
)
from cuanta.domain.governor_report import BlockedCalls
from cuanta.domain.read_discipline import READ_LINE_LIMIT, decide_read_discipline
from cuanta.domain.read_efficiency import ReadCall
from cuanta.domain.scout import ScoutMode

READ_DISCIPLINE_TARGET = 0.5
SCOUT_TARGET = 0.4
WARM_TARGET = 0.8
FINISH_TARGET = 0.0
FINISH_ROOM = FINISH_HEADROOM_STEPS
SEND_TOLERANCE = 0.01
DEFAULT_OVERSHOOT_USD = 0.10
NOT_LAUNCHED = "not_launched"
DENY = "deny"


class Comparison(StrEnum):
    READ_DISCIPLINE = "read-discipline"
    SCOUT = "scout"
    WARM_QUEUE = "warm-queue"
    FINISH = "finish"


COMPARISONS = tuple(Comparison)


@dataclass(frozen=True, slots=True)
class Arm:
    name: str
    comparison: Comparison
    condition: Condition
    shape: str = ""
    read_discipline: bool | None = None
    position: int = 0
    governor: bool | None = None
    scout_mode: str = ""


DISCIPLINE_ON = Arm(
    "discipline-on", Comparison.READ_DISCIPLINE, Condition.CUANTA, read_discipline=True
)
DISCIPLINE_OFF = Arm(
    "discipline-off", Comparison.READ_DISCIPLINE, Condition.CUANTA, read_discipline=False
)
SCOUT_ARM = Arm(
    "scout", Comparison.SCOUT, Condition.ROUTED, shape="scout", scout_mode=ScoutMode.NATIVE.value
)
PIPELINE_ARM = Arm("pipeline", Comparison.SCOUT, Condition.ROUTED, shape="pipeline")
QUEUE_FIRST = Arm("queue-first", Comparison.WARM_QUEUE, Condition.CUANTA, position=1)
QUEUE_SECOND = Arm("queue-second", Comparison.WARM_QUEUE, Condition.CUANTA, position=2)
TIGHT_CAP = Arm("tight-cap", Comparison.FINISH, Condition.ROUTED, governor=True)
ARMS: dict[Comparison, tuple[Arm, ...]] = {
    Comparison.READ_DISCIPLINE: (DISCIPLINE_ON, DISCIPLINE_OFF),
    Comparison.SCOUT: (SCOUT_ARM, PIPELINE_ARM),
    Comparison.WARM_QUEUE: (QUEUE_FIRST, QUEUE_SECOND),
    Comparison.FINISH: (TIGHT_CAP,),
}
PAIRED = frozenset({Comparison.WARM_QUEUE})


class Verdict(StrEnum):
    MET = "met"
    MISSED = "missed"
    UNKNOWN = "n/a"


class FinishVerdict(StrEnum):
    FINISHED = "finished"
    NO_ROOM = "cut, no room for a finish"
    NOT_SENT = "cut, finish not sent"
    TOO_LATE = "cut, finish sent too late"
    IGNORED = "cut after a finish in time"
    NO_TELEMETRY = "cut, no request telemetry"
    SEND_UNKNOWN = "cut, finish sent at an unknown spend"


MISSED_FINISHES = frozenset({FinishVerdict.NOT_SENT, FinishVerdict.TOO_LATE, FinishVerdict.IGNORED})
UNKNOWN_FINISHES = frozenset({FinishVerdict.NO_TELEMETRY, FinishVerdict.SEND_UNKNOWN})


@dataclass(frozen=True, slots=True)
class FinishCheck:
    task: str
    rep: int
    run_id: str
    cap_usd: float
    cost_usd: float | None
    cut: bool
    requests: int | None
    trigger: int | None
    sent: bool
    after_send: int | None
    verdict: FinishVerdict

    @property
    def missed(self) -> bool:
        return self.verdict in MISSED_FINISHES


@dataclass(frozen=True, slots=True)
class TargetResult:
    comparison: Comparison
    verdict: Verdict
    measured: float | None
    target: float
    summary: str
    finishes: tuple[FinishCheck, ...] = ()


def parse_comparison(text: str) -> Comparison:
    try:
        return Comparison(text.strip())
    except ValueError as error:
        names = ", ".join(item.value for item in COMPARISONS)
        raise DomainFailure(f"unknown comparison {text}", f"use one of {names}") from error


def arm_named(name: str) -> Arm | None:
    return next((arm for arms in ARMS.values() for arm in arms if arm.name == name), None)


def planned_arm(run: PlannedRun | None) -> Arm | None:
    if run is None:
        return None
    arm = arm_named(run.arm)
    if arm is None:
        raise ValueError(f"bench arm {run.arm!r} is not defined")
    return arm


def arm_config(config: Config, arm: Arm | None) -> Config:
    if arm is None:
        return config
    changed = config
    if arm.read_discipline is not None:
        changed = replace(
            changed,
            read_discipline=arm.read_discipline,
            pipeline_read_discipline=arm.read_discipline,
        )
    if arm.governor is not None:
        changed = replace(changed, governor=arm.governor)
    if arm.scout_mode:
        changed = replace(changed, scout_mode=arm.scout_mode)
    return changed


def _flag(value: bool) -> str:
    return "true" if value else "false"


def arm_settings(arm: Arm) -> str:
    parts: list[str] = []
    if arm.read_discipline is not None:
        value = _flag(arm.read_discipline)
        parts += [f"runs.read_discipline={value}", f"runs.pipeline_read_discipline={value}"]
    if arm.governor is not None:
        parts.append(f"runs.governor={_flag(arm.governor)}")
    if arm.scout_mode:
        parts.append(f"runs.scout_mode={arm.scout_mode}")
    if arm.shape:
        parts.append(f"shape {arm.shape}")
    return ", ".join(parts) or "the copy's config"


def arm_caps(values: Sequence[str], comparison: Comparison) -> dict[str, float]:
    names = {arm.name for arm in ARMS[comparison]}
    caps: dict[str, float] = {}
    for value in values:
        name, separator, amount = value.partition("=")
        name = name.strip()
        if not separator or name not in names:
            raise DomainFailure(
                f"--arm-cap {value} names no arm of {comparison.value}",
                f"use ARM=USD with ARM one of {', '.join(sorted(names))}",
            )
        try:
            cap = float(amount)
        except ValueError as error:
            raise DomainFailure(f"--arm-cap {value} has no amount", "use ARM=USD") from error
        if not math.isfinite(cap) or cap <= 0:
            raise DomainFailure(f"--arm-cap {value} must be positive", "use ARM=USD")
        caps[name] = cap
    return caps


def plan_proof(
    tasks: Sequence[BenchTask],
    comparison: Comparison,
    reps: int,
    seed: int,
    caps: Mapping[str, float],
    default_cap: float,
    session: str = LEAN_SESSION,
    index: str = "on",
) -> tuple[PlannedRun, ...]:
    arms = ARMS[comparison]
    reps_range = range(1, reps + 1)
    groups: list[list[tuple[str, Arm, int]]] = (
        [[(task.name, arm, rep) for arm in arms] for task in tasks for rep in reps_range]
        if comparison in PAIRED
        else [[(task.name, arm, rep)] for task in tasks for arm in arms for rep in reps_range]
    )
    random.Random(seed).shuffle(groups)
    planned: list[PlannedRun] = []
    for number, members in enumerate(groups, 1):
        for name, arm, rep in members:
            planned.append(
                PlannedRun(
                    len(planned) + 1,
                    name,
                    arm.condition,
                    rep,
                    session,
                    index,
                    arm.name,
                    caps.get(arm.name, default_cap),
                    number,
                )
            )
    return tuple(planned)


def overshoot_allowance(samples: Mapping[str, Sequence[float]]) -> float:
    measured = [value for values in samples.values() for value in values if math.isfinite(value)]
    return max((DEFAULT_OVERSHOOT_USD, *measured))


def comparison_key(run: PlannedRun) -> tuple[str, int]:
    return run.task, run.rep


def comparison_caps(planned: Sequence[PlannedRun]) -> dict[tuple[str, int], float]:
    caps: dict[tuple[str, int], float] = {}
    for item in planned:
        key = comparison_key(item)
        caps[key] = caps.get(key, 0.0) + item.cap_usd
    return caps


def committed_runs(planned: Sequence[PlannedRun]) -> int:
    sizes = Counter(comparison_key(item) for item in planned)
    started: set[tuple[str, int]] = set()
    left = most = 0
    for item in planned:
        key = comparison_key(item)
        if key not in started:
            started.add(key)
            left += sizes[key]
            most = max(most, left)
        left -= 1
    return most


def worst_case(
    planned: Sequence[PlannedRun], budget_usd: float, overshoot_usd: float = 0.0
) -> float:
    total = sum(item.cap_usd + overshoot_usd for item in planned)
    if budget_usd <= 0:
        return total
    if all(caps > budget_usd for caps in comparison_caps(planned).values()):
        return 0.0
    return min(total, budget_usd + overshoot_usd * committed_runs(planned))


def breaks_discipline(call: ReadCall, lines: Mapping[str, int], line_limit: int) -> bool:
    decision = decide_read_discipline(
        call.tool, call.inputs, lines.get(call.path, 0), 0, line_limit
    )
    return decision.permission == DENY


def exploration_leak(
    calls: Sequence[ReadCall],
    plan: ChangePlan,
    lines: Mapping[str, int],
    line_limit: int = READ_LINE_LIMIT,
) -> tuple[int, int]:
    allowed = (*(target.path for target in plan.edit), *plan.read)
    leak = rule = 0
    for call in calls:
        breaks = breaks_discipline(call, lines, line_limit)
        outside = not call.search and not any(
            path_matches(call.path, pattern) for pattern in allowed
        )
        if breaks:
            rule += call.tokens
        if breaks or outside:
            leak += call.tokens
    return leak, rule


def proof_record(
    arm: Arm,
    run: PlannedRun,
    end_reason: str,
    leak: int | None,
    blocked: BlockedCalls | None,
    scout: Mapping[str, object] | None,
    first_cache_read: int | None,
    fixed_prefix: int | None,
    reactions: Sequence[ReactionTaken],
    rule_tokens: int | None = None,
    answered: bool | None = None,
) -> ProofRecord:
    finish = next(
        (
            taken
            for taken in reactions
            if taken.sent and taken.reaction.kind is ReactionKind.FINISH_NOW
        ),
        None,
    )
    dispatched = scout.get("dispatched") if scout is not None else None
    return ProofRecord(
        comparison=arm.comparison.value,
        arm=arm.name,
        cap_usd=run.cap_usd,
        end_reason=end_reason,
        leak_tokens=leak,
        blocked_reads=blocked.reads if blocked is not None else None,
        blocked_tokens=blocked.tokens if blocked is not None else None,
        scout_mode=str(scout.get("mode") or "") if scout is not None else "",
        scout_dispatched=dispatched if isinstance(dispatched, bool) else None,
        first_cache_read=first_cache_read,
        fixed_prefix=fixed_prefix,
        finish_sent=finish is not None,
        finish_spent_usd=finish.reaction.projection.spent_usd if finish is not None else None,
        group=run.group,
        position=arm.position,
        rule_tokens=rule_tokens,
        answered=answered,
    )


def ended_by_cap(proof: ProofRecord | None, capped: bool) -> bool:
    if proof is None or not proof.end_reason:
        return capped
    return proof.end_reason == BUDGET_LIMIT_SUBTYPE and proof.answered is not True


def launched_cost(run: RunMetrics) -> float | None:
    if run.proof is not None and run.proof.end_reason == NOT_LAUNCHED:
        return 0.0
    return run.cost_usd


def request_costs(anatomy: AnatomyReport) -> tuple[float, ...] | None:
    events = sorted(anatomy.events, key=lambda item: (item.ts, item.event_id))
    known = [event.cost_usd for event in events if event.cost_usd is not None]
    if not events or len(known) != len(events):
        return None
    return tuple(known)


def finish_trigger(costs: Sequence[float], cap_usd: float) -> int | None:
    spent = 0.0
    for index, cost in enumerate(costs, 1):
        spent += cost
        if spent >= FINISH_SHARE * cap_usd or spent + FINISH_HEADROOM_STEPS * cost >= cap_usd:
            return index
    return None


def requests_after(costs: Sequence[float], spent_usd: float, cap_usd: float) -> int:
    limit = spent_usd + SEND_TOLERANCE * cap_usd
    spent = 0.0
    after = 0
    for cost in costs:
        spent += cost
        if spent > limit:
            after += 1
    return after


def finish_check(run: RunMetrics) -> FinishCheck:
    proof = run.proof
    cap = proof.cap_usd if proof is not None else 0.0
    cut = ended_by_cap(proof, run.capped)
    costs = request_costs(run.anatomy)
    trigger = finish_trigger(costs, cap) if costs is not None and cap > 0 else None
    sent = proof is not None and proof.finish_sent
    spent = proof.finish_spent_usd if proof is not None else None
    after = (
        requests_after(costs, spent, cap)
        if costs is not None and sent and spent is not None
        else None
    )
    return FinishCheck(
        task=run.task,
        rep=run.rep,
        run_id=run.run_id,
        cap_usd=cap,
        cost_usd=run.cost_usd,
        cut=cut,
        requests=len(costs) if costs is not None else None,
        trigger=trigger,
        sent=sent,
        after_send=after,
        verdict=_finish_verdict(cut, costs, trigger, sent, after),
    )


def _finish_verdict(
    cut: bool,
    costs: Sequence[float] | None,
    trigger: int | None,
    sent: bool,
    after: int | None,
) -> FinishVerdict:
    if not cut:
        return FinishVerdict.FINISHED
    if costs is None:
        return FinishVerdict.NO_TELEMETRY
    if trigger is None or len(costs) - trigger < FINISH_ROOM:
        return FinishVerdict.NO_ROOM
    if not sent:
        return FinishVerdict.NOT_SENT
    if after is None:
        return FinishVerdict.SEND_UNKNOWN
    if after < FINISH_ROOM:
        return FinishVerdict.TOO_LATE
    return FinishVerdict.IGNORED


def prefix_share(run: RunMetrics) -> float | None:
    proof = run.proof
    if proof is None or proof.first_cache_read is None or proof.fixed_prefix is None:
        return None
    if proof.fixed_prefix <= 0:
        return None
    return min(1.0, proof.first_cache_read / proof.fixed_prefix)


def _runs(metrics: Sequence[RunMetrics], arm: Arm) -> list[RunMetrics]:
    return [item for item in metrics if item.arm == arm.name]


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _unknown(comparison: Comparison, target: float, why: str) -> TargetResult:
    return TargetResult(comparison, Verdict.UNKNOWN, None, target, why)


def _known(
    runs: Sequence[RunMetrics], pick: Callable[[ProofRecord], int | None]
) -> list[float] | None:
    values = [
        float(found)
        for item in runs
        if item.proof is not None and (found := pick(item.proof)) is not None
    ]
    return values if len(values) == len(runs) else None


def _leak(record: ProofRecord) -> int | None:
    return record.leak_tokens


def _rule(record: ProofRecord) -> int | None:
    return record.rule_tokens


def _blocked(runs: Sequence[RunMetrics]) -> tuple[int, float] | None:
    records = [item.proof for item in runs if item.proof is not None]
    reads = [record.blocked_reads for record in records if record.blocked_reads is not None]
    tokens = [record.blocked_tokens for record in records if record.blocked_tokens is not None]
    if len(reads) != len(runs) or len(tokens) != len(runs):
        return None
    return sum(reads), _mean([float(value) for value in tokens])


def read_discipline_saving(metrics: Sequence[RunMetrics]) -> TargetResult:
    comparison, target = Comparison.READ_DISCIPLINE, READ_DISCIPLINE_TARGET
    on, off = _runs(metrics, DISCIPLINE_ON), _runs(metrics, DISCIPLINE_OFF)
    if not on or not off:
        return _unknown(comparison, target, "needs runs of discipline-on and discipline-off")
    leaks_on, leaks_off = _known(on, _leak), _known(off, _leak)
    if leaks_on is None or leaks_off is None:
        return _unknown(comparison, target, "read telemetry is missing in some runs")
    mean_on, mean_off = _mean(leaks_on), _mean(leaks_off)
    blocked = _blocked(on)
    if blocked is None:
        blocked_text = "blocked reads n/a"
    else:
        reads, avoided = blocked
        blocked_text = f"{reads} reads blocked, ~{avoided:,.0f} tokens avoided per run (estimate)"
    rules_on, rules_off = _known(on, _rule), _known(off, _rule)
    rule_text = (
        "rule-breaking reads n/a"
        if rules_on is None or rules_off is None
        else f"of it, reads that break the discipline's rule {_mean(rules_on):,.0f} against "
        f"{_mean(rules_off):,.0f}"
    )
    if mean_off <= 0:
        return _unknown(
            comparison,
            target,
            f"no exploration leak without the discipline in {len(off)} runs; {blocked_text}",
        )
    saving = 1 - mean_on / mean_off
    summary = (
        f"exploration leak {mean_on:,.0f} tokens per run with the discipline ({len(on)} runs) "
        f"against {mean_off:,.0f} without ({len(off)} runs): {saving:.0%} fewer "
        f"(target {target:.0%}); {rule_text}; {blocked_text}"
    )
    verdict = Verdict.MET if saving >= target else Verdict.MISSED
    return TargetResult(comparison, verdict, saving, target, summary)


def _scout_ran(run: RunMetrics) -> bool:
    proof = run.proof
    return proof is not None and bool(proof.scout_mode) and proof.scout_dispatched is not False


def scout_saving(metrics: Sequence[RunMetrics]) -> TargetResult:
    comparison, target = Comparison.SCOUT, SCOUT_TARGET
    scout, pipeline = _runs(metrics, SCOUT_ARM), _runs(metrics, PIPELINE_ARM)
    if not scout or not pipeline:
        return _unknown(comparison, target, "needs runs of the scout and pipeline arms")
    scout_costs = [item.cost_usd for item in scout if item.cost_usd is not None]
    pipeline_costs = [item.cost_usd for item in pipeline if item.cost_usd is not None]
    if len(scout_costs) != len(scout) or len(pipeline_costs) != len(pipeline):
        return _unknown(comparison, target, "a run's cost is unknown")
    skipped = sum(not _scout_ran(item) for item in scout)
    if skipped:
        return _unknown(
            comparison,
            target,
            f"the scout did not run in {skipped} of {len(scout)} scout-arm runs",
        )
    mean_scout, mean_pipeline = _mean(scout_costs), _mean(pipeline_costs)
    if mean_pipeline <= 0:
        return _unknown(comparison, target, "the pipeline runs cost nothing")
    saving = 1 - mean_scout / mean_pipeline
    accepted_scout = sum(item.accepted for item in scout)
    accepted_pipeline = sum(item.accepted for item in pipeline)
    if not accepted_scout and not accepted_pipeline:
        return _unknown(
            comparison,
            target,
            f"neither arm has an accepted run (scout 0/{len(scout)}, pipeline 0/{len(pipeline)})",
        )
    parity = accepted_scout * len(pipeline) >= accepted_pipeline * len(scout)
    modes = sorted({proof.scout_mode or "none" for item in scout if (proof := item.proof)})
    ran = sum(_scout_ran(item) for item in scout)
    summary = (
        f"${mean_scout:,.4f} per scout run against ${mean_pipeline:,.4f} per pipeline run: "
        f"{saving:.0%} cheaper (target {target:.0%}); accepted {accepted_scout}/{len(scout)} "
        f"against {accepted_pipeline}/{len(pipeline)} "
        f"({'parity' if parity else 'below parity'}); scout mode {', '.join(modes) or 'none'}, "
        f"scout ran in {ran}/{len(scout)}"
    )
    verdict = Verdict.MET if saving >= target and parity else Verdict.MISSED
    return TargetResult(comparison, verdict, saving, target, summary)


def queue_warm_share(metrics: Sequence[RunMetrics]) -> TargetResult:
    comparison, target = Comparison.WARM_QUEUE, WARM_TARGET
    second = _runs(metrics, QUEUE_SECOND)
    if not second:
        return _unknown(comparison, target, "needs the second mandate of a queued pair")
    shares = [prefix_share(item) for item in second]
    known = [share for share in shares if share is not None]
    if len(known) != len(shares):
        return _unknown(comparison, target, "first-request telemetry is missing in some runs")
    firsts = [
        share for item in _runs(metrics, QUEUE_FIRST) if (share := prefix_share(item)) is not None
    ]
    lowest = min(known)
    first_text = ", ".join(f"{share:.0%}" for share in firsts) or "n/a"
    summary = (
        f"the second mandate's first request read {lowest:.0%} of its fixed prefix from cache "
        f"(lowest of {len(known)} pairs; target {target:.0%}); first mandates: {first_text}"
    )
    verdict = Verdict.MET if lowest >= target else Verdict.MISSED
    return TargetResult(comparison, verdict, lowest, target, summary)


def cut_by_cap_when_finish_possible(metrics: Sequence[RunMetrics]) -> TargetResult:
    comparison, target = Comparison.FINISH, FINISH_TARGET
    runs = _runs(metrics, TIGHT_CAP)
    if not runs:
        return _unknown(comparison, target, "needs tight-cap runs")
    checks = tuple(finish_check(item) for item in runs)
    missed = sum(check.missed for check in checks)
    unknown = sum(check.verdict in UNKNOWN_FINISHES for check in checks)
    cut = sum(check.cut for check in checks)
    fired = sum(check.trigger is not None for check in checks)
    summary = (
        f"{cut} of {len(checks)} runs cut by their cap; {missed} cut although a graceful "
        f"finish was possible (target 0); the finish rule fired in {fired} of {len(checks)} runs"
    )
    if unknown:
        summary += f"; {unknown} cut runs cannot be judged"
    if not fired:
        summary += "; no run reached the rule, so the cap was never tight"
    verdict = Verdict.MISSED if missed else Verdict.UNKNOWN if unknown or not fired else Verdict.MET
    return TargetResult(comparison, verdict, float(missed), target, summary, checks)


TARGETS = {
    Comparison.READ_DISCIPLINE: read_discipline_saving,
    Comparison.SCOUT: scout_saving,
    Comparison.WARM_QUEUE: queue_warm_share,
    Comparison.FINISH: cut_by_cap_when_finish_possible,
}


def targets(metrics: Sequence[RunMetrics]) -> tuple[TargetResult, ...]:
    present = {proof.comparison for item in metrics if (proof := item.proof) is not None}
    return tuple(
        TARGETS[comparison](metrics) for comparison in COMPARISONS if comparison.value in present
    )


def _usd(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.4f}"


def _count(value: int | None) -> str:
    return "n/a" if value is None else f"{value:,}"


def _share(run: RunMetrics) -> str:
    proof = run.proof
    share = prefix_share(run)
    if proof is None or share is None:
        return "n/a"
    return f"{share:.0%} ({proof.first_cache_read:,}/{proof.fixed_prefix:,})"


def proof_markdown(metrics: Sequence[RunMetrics]) -> str:
    results = targets(metrics)
    if not results:
        return ""
    lines = [
        "",
        "## Targets",
        "",
        "Each target is computed from the stored run records; n/a means the records cannot "
        "decide it. Exploration leak counts, the same way in both arms, the tokens returned by "
        "reads outside the change plan's edit and read sets and by reads that break the read "
        "discipline's rule (a Read without a range of a file above the line limit, a "
        "whole-tree content Grep); blocked reads are the hooks' estimates of what they avoided.",
        "",
        "| Target | Verdict | Measured |",
        "|---|---|---|",
    ]
    lines += [
        f"| {result.comparison.value} | {result.verdict.value} | {result.summary} |"
        for result in results
    ]
    lines += [
        "",
        "### Runs",
        "",
        "| Task | Arm | Rep | Cap | Cost | Accepted | End | Leak tokens | Rule-breaking tokens "
        "| Blocked reads | Scout mode | First request from cache / fixed prefix |",
        "|---|---|---:|---:|---:|---|---|---:|---:|---:|---|---:|",
    ]
    proofs = sorted(
        ((item, proof) for item in metrics if (proof := item.proof) is not None),
        key=lambda pair: (pair[1].arm, pair[0].task, pair[0].rep),
    )
    for item, proof in proofs:
        lines.append(
            f"| {item.task} | {proof.arm} | {item.rep} | {_usd(proof.cap_usd)} | "
            f"{_usd(item.cost_usd)} | {'yes' if item.accepted else 'no'} | "
            f"{proof.end_reason or '-'} | {_count(proof.leak_tokens)} | "
            f"{_count(proof.rule_tokens)} | {_count(proof.blocked_reads)} | "
            f"{proof.scout_mode or '-'} | {_share(item)} |"
        )
    finishes = [check for result in results for check in result.finishes]
    if finishes:
        lines += [
            "",
            "### Graceful finish per run",
            "",
            "A run is cut when it ended on its budget stop without a final answer. A finish is "
            "possible when the governor's rule (85% of the cap, or two requests from it) fired "
            "at least two requests before the run ended.",
            "",
            "| Task | Rep | Run | Cap | Cost | Requests | Rule fired at | Finish sent "
            "| Requests after it | Verdict |",
            "|---|---:|---|---:|---:|---:|---:|---|---:|---|",
        ]
        lines += [
            f"| {check.task} | {check.rep} | {check.run_id or '-'} | {_usd(check.cap_usd)} | "
            f"{_usd(check.cost_usd)} | {_count(check.requests)} | {_count(check.trigger)} | "
            f"{'yes' if check.sent else 'no'} | {_count(check.after_send)} | "
            f"{check.verdict.value} |"
            for check in finishes
        ]
    return "\n".join(lines) + "\n"


def finish_payload(check: FinishCheck) -> dict[str, object]:
    return {
        "task": check.task,
        "rep": check.rep,
        "run_id": check.run_id,
        "cap_usd": check.cap_usd,
        "cost_usd": check.cost_usd,
        "cut": check.cut,
        "requests": check.requests,
        "rule_fired_at": check.trigger,
        "finish_sent": check.sent,
        "requests_after_finish": check.after_send,
        "verdict": check.verdict.value,
        "missed": check.missed,
    }


def targets_payload(results: Sequence[TargetResult]) -> list[dict[str, object]]:
    return [
        {
            "target": result.comparison.value,
            "verdict": result.verdict.value,
            "measured": result.measured,
            "threshold": result.target,
            "summary": result.summary,
            "runs": [finish_payload(check) for check in result.finishes],
        }
        for result in results
    ]
