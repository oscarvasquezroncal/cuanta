from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from html import escape

from cuanta.domain.mandate import MandateRequest
from cuanta.domain.routing import percentile


class Condition(StrEnum):
    BASELINE = "baseline"
    CUANTA = "cuanta"
    ROUTED = "cuanta-routed"


CONDITIONS = tuple(Condition)
LEAN_SESSION = "lean"
FULL_SESSION = "full"
BOTH_SESSIONS = "both"
SESSION_PROFILES = (LEAN_SESSION, FULL_SESSION, BOTH_SESSIONS)
DEFAULT_ACCEPT = "{python} -m pytest -q -p no:cacheprovider {hidden}"


@dataclass(frozen=True, slots=True)
class Source:
    url: str
    sha256: str


@dataclass(frozen=True, slots=True)
class BenchTask:
    name: str
    suites: tuple[str, ...]
    fixture: str
    request: MandateRequest
    prompt: str
    hidden: Mapping[str, str]
    files: Mapping[str, str] = field(default_factory=dict)
    setup: tuple[str, ...] = ()
    accept: str = DEFAULT_ACCEPT
    source: Source | None = None
    edits: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class PlannedRun:
    order: int
    task: str
    condition: Condition
    rep: int
    session: str = LEAN_SESSION


@dataclass(frozen=True, slots=True)
class RunMetrics:
    task: str
    condition: Condition
    rep: int
    run_id: str
    accepted: bool
    capped: bool
    fresh_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    output_tokens: int
    cost_usd: float | None
    wall_s: float
    test_output_tokens: int
    retries: int
    models: tuple[tuple[str, str, str], ...] = ()
    error: str = ""
    session: str = LEAN_SESSION
    context_tokens: int = 0
    loaded: tuple[int, int, int] = (0, 0, 0)

    @property
    def total_tokens(self) -> int:
        return (
            self.fresh_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
            + self.output_tokens
        )


@dataclass(frozen=True, slots=True)
class Spread:
    median: float | None
    low: float | None
    high: float | None


@dataclass(frozen=True, slots=True)
class ConditionSummary:
    condition: Condition
    runs: int
    accepted: int
    tokens_per_accepted: Spread
    cost: Spread
    spent_usd: float
    wall: Spread
    session: str = LEAN_SESSION
    context: Spread = field(default_factory=lambda: Spread(None, None, None))

    @property
    def label(self) -> str:
        if self.session == LEAN_SESSION:
            return self.condition.value
        return f"{self.condition.value} · {self.session}"

    @property
    def success(self) -> float:
        return self.accepted / self.runs if self.runs else 0.0


@dataclass(frozen=True, slots=True)
class BenchMeta:
    bench_id: str
    suite: str
    reps: int
    seed: int
    engine: str
    engine_version: str
    model: str
    started_at: str
    budget_usd: float
    per_run_usd: float
    tasks: tuple[str, ...]
    session: str = LEAN_SESSION


def select(tasks: Sequence[BenchTask], suite: str) -> tuple[BenchTask, ...]:
    return tuple(task for task in tasks if suite in task.suites)


def sessions_for(profile: str) -> tuple[str, ...]:
    return (LEAN_SESSION, FULL_SESSION) if profile == BOTH_SESSIONS else (profile,)


def plan_runs(
    tasks: Sequence[BenchTask],
    conditions: Sequence[Condition],
    reps: int,
    seed: int,
    sessions: Sequence[str] = (LEAN_SESSION,),
) -> tuple[PlannedRun, ...]:
    combos = [
        (task.name, condition, rep, session)
        for task in tasks
        for condition in conditions
        for session in sessions
        for rep in range(1, reps + 1)
    ]
    random.Random(seed).shuffle(combos)
    return tuple(
        PlannedRun(index + 1, task, condition, rep, session)
        for index, (task, condition, rep, session) in enumerate(combos)
    )


def spread(values: Sequence[float]) -> Spread:
    if not values:
        return Spread(None, None, None)
    return Spread(percentile(values, 0.5), min(values), max(values))


def summarize(metrics: Sequence[RunMetrics]) -> tuple[ConditionSummary, ...]:
    rows: list[ConditionSummary] = []
    for condition in CONDITIONS:
        for session in (LEAN_SESSION, FULL_SESSION):
            row = _summary(metrics, condition, session)
            if row is not None:
                rows.append(row)
    return tuple(rows)


def _summary(
    metrics: Sequence[RunMetrics], condition: Condition, session: str
) -> ConditionSummary | None:
    runs = [item for item in metrics if item.condition is condition and item.session == session]
    if not runs:
        return None
    accepted = [item for item in runs if item.accepted]
    costs = [item.cost_usd for item in runs if item.cost_usd is not None]
    contexts = [float(item.context_tokens) for item in runs if item.context_tokens]
    return ConditionSummary(
        condition=condition,
        runs=len(runs),
        accepted=len(accepted),
        tokens_per_accepted=spread([float(item.total_tokens) for item in accepted]),
        cost=spread(costs),
        spent_usd=sum(costs),
        wall=spread([item.wall_s for item in runs]),
        session=session,
        context=spread(contexts),
    )


def _number(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.{digits}f}"


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.2f}"


def bar_chart(
    title: str, rows: Sequence[tuple[str, float | None, float | None, float | None]]
) -> str:
    width, bar_height, gap, left, top = 640, 28, 18, 150, 44
    height = top + len(rows) * (bar_height + gap) + 20
    peak = max((high or median or 0.0) for _, median, _, high in rows) or 1.0
    usable = width - left - 90
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="sans-serif" font-size="13">',
        f'<text x="12" y="24" font-size="15" font-weight="bold">{escape(title)}</text>',
    ]
    for index, (label, median, low, high) in enumerate(rows):
        y = top + index * (bar_height + gap)
        parts.append(f'<text x="12" y="{y + 19}">{escape(label)}</text>')
        if median is None:
            parts.append(f'<text x="{left}" y="{y + 19}" fill="#777">no accepted runs</text>')
            continue
        length = max(1.0, median / peak * usable)
        parts.append(
            f'<rect x="{left}" y="{y}" width="{length:.1f}" height="{bar_height}" '
            'fill="#e8956b" rx="3"/>'
        )
        if low is not None and high is not None:
            x1, x2 = left + low / peak * usable, left + high / peak * usable
            mid = y + bar_height / 2
            parts.append(
                f'<line x1="{x1:.1f}" y1="{mid}" x2="{x2:.1f}" y2="{mid}" '
                'stroke="#3d3a4b" stroke-width="2"/>'
            )
        shown = _number(median, 2) if median < 10 else _number(median)
        parts.append(f'<text x="{left + length + 8:.1f}" y="{y + 19}">{escape(shown)}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def charts(summary: Sequence[ConditionSummary]) -> dict[str, str]:
    return {
        "tokens.svg": bar_chart(
            "Tokens per accepted task (median, range)",
            [
                (
                    row.label,
                    row.tokens_per_accepted.median,
                    row.tokens_per_accepted.low,
                    row.tokens_per_accepted.high,
                )
                for row in summary
            ],
        ),
        "success.svg": bar_chart(
            "Success rate (%)",
            [(row.label, row.success * 100, None, None) for row in summary],
        ),
        "cost.svg": bar_chart(
            "Cost per run in USD (median, range)",
            [(row.label, row.cost.median, row.cost.low, row.cost.high) for row in summary],
        ),
    }


def report_markdown(
    meta: BenchMeta, summary: Sequence[ConditionSummary], metrics: Sequence[RunMetrics]
) -> str:
    spent = sum(item.cost_usd or 0.0 for item in metrics)
    lines = [
        f"# cuanta bench · {meta.suite}",
        "",
        f"- Suite **{meta.suite}**: {len(meta.tasks)} tasks × {meta.reps} repetitions × "
        f"{len(summary)} conditions, randomized order (seed {meta.seed}).",
        f"- Engine **{meta.engine} {meta.engine_version}**, main model **{meta.model}** "
        "for every condition.",
        f"- Budget: {_money(meta.per_run_usd)} per run, {_money(meta.budget_usd)} for the bench; "
        f"**{_money(spent)} spent** in total. A run that hits its cap counts as not accepted.",
        f"- Session profile **{meta.session}**: a lean session loads no user plugins, hooks or "
        "MCP servers; a full session loads everything the user's Claude Code loads.",
        f"- Started {meta.started_at} · bench `{meta.bench_id}`.",
        "",
        "| Condition | Runs | Accepted | Success | Tokens / accepted task (median, range) "
        "| Cost / run (median, range) | First-request context (median) | Wall time (median) |",
        "|---|---:|---:|---:|---|---|---:|---:|",
    ]
    for row in summary:
        tokens = row.tokens_per_accepted
        cost = row.cost
        lines.append(
            f"| {row.label} | {row.runs} | {row.accepted} | {row.success:.0%} | "
            f"{_number(tokens.median)} ({_number(tokens.low)}–{_number(tokens.high)}) | "
            f"{_money(cost.median)} ({_money(cost.low)}–{_money(cost.high)}) | "
            f"{_number(row.context.median)} | {_number(row.wall.median)} s |"
        )
    lines += [
        "",
        "![Tokens per accepted task](tokens.svg)",
        "![Success rate](success.svg)",
        "![Cost per run](cost.svg)",
        "",
        "## Runs",
        "",
        "| Task | Condition | Session | Rep | Accepted | Tokens | Context "
        "| Plugins / MCP / hooks | Cost | Test-output tokens | Retries |",
        "|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    ordered = sorted(metrics, key=lambda run: (run.task, run.condition.value, run.session, run.rep))
    for item in ordered:
        verdict = "yes" if item.accepted else ("capped" if item.capped else "no")
        lines.append(
            f"| {item.task} | {item.condition.value} | {item.session} | {item.rep} | {verdict} | "
            f"{item.total_tokens:,} | {item.context_tokens:,} | "
            f"{' / '.join(str(count) for count in item.loaded)} | {_money(item.cost_usd)} | "
            f"{item.test_output_tokens:,} | {item.retries} |"
        )
    routed = [item for item in metrics if item.models]
    if routed:
        lines += ["", "## Planned vs actual model per agent", ""]
        lines += ["| Task | Rep | Agent | Planned | Actual |", "|---|---:|---|---|---|"]
        for item in routed:
            for agent, planned, actual in item.models:
                lines.append(
                    f"| {item.task} | {item.rep} | {agent} | {planned} | {actual or '-'} |"
                )
    return "\n".join(lines) + "\n"


TEST_COMMANDS = ("pytest", "cuanta test", "go test", "npm test", "npx jest", "vitest", "jest")


def command_args(template: str, python: str, hidden: Sequence[str] = ()) -> tuple[str, ...]:
    args: list[str] = []
    for token in template.split():
        if token == "{python}":
            args.append(python)
        elif token == "{hidden}":
            args.extend(hidden)
        else:
            args.append(token)
    return tuple(args)


def rerun_count(commands: Sequence[str]) -> int:
    runs = sum(1 for command in commands if any(name in command for name in TEST_COMMANDS))
    return max(0, runs - 1)


def was_capped(subtype: str, cost_usd: float | None, cap_usd: float) -> bool:
    if "budget" in subtype:
        return True
    return cap_usd > 0 and cost_usd is not None and cost_usd >= cap_usd


README_START = "<!-- cuanta-bench:start -->"
README_END = "<!-- cuanta-bench:end -->"


def readme_section(
    meta: BenchMeta, summary: Sequence[ConditionSummary], spent: float, charts_dir: str
) -> str:
    lines = [
        README_START,
        "",
        f"Suite `{meta.suite}`, {len(meta.tasks)} tasks × {meta.reps} repetitions per condition, "
        f"{meta.engine} {meta.engine_version} with `{meta.model}` as the main model, "
        f"{_money(spent)} spent in total ({meta.started_at[:10]}).",
        "",
        "| Condition | Success | Tokens / accepted task (median) | Cost / run (median) |",
        "|---|---:|---:|---:|",
    ]
    for row in summary:
        lines.append(
            f"| {row.label} | {row.accepted}/{row.runs} | "
            f"{_number(row.tokens_per_accepted.median)} | {_money(row.cost.median)} |"
        )
    lines += [
        "",
        f"![Tokens per accepted task]({charts_dir}/tokens.svg)",
        "",
        f"Full report: [{charts_dir}/report.md]({charts_dir}/report.md).",
        "",
        README_END,
    ]
    return "\n".join(lines)


def splice(readme: str, section: str) -> str:
    start, end = readme.find(README_START), readme.find(README_END)
    if start >= 0 and end > start:
        return readme[:start] + section + readme[end + len(README_END) :]
    return readme.rstrip("\n") + "\n\n## Benchmark\n\n" + section + "\n"
