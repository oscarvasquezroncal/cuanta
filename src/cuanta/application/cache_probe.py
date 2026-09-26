from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from cuanta.application.engine_run import EngineLauncher, LaunchSpec
from cuanta.domain.cache_probe import (
    CacheProbePlan,
    CacheReading,
    CacheTtlResult,
    Verdict,
    Warmth,
    decide,
    probe_prompt,
)
from cuanta.domain.engine import EngineEvent, SessionStarted, StepUsage
from cuanta.domain.messages import msg
from cuanta.domain.pricing import Price
from cuanta.domain.progress import Status, note
from cuanta.ports.progress import ProgressSink
from cuanta.ports.system import Clock


@dataclass(frozen=True, slots=True)
class CacheProbeOptions:
    model: str
    gaps_s: tuple[int, ...]
    budget_usd: float
    per_run_usd: float
    tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CacheProbeReport:
    plan: CacheProbePlan
    model: str
    expected_auth: str
    result: CacheTtlResult | None = None
    saved: bool = False
    scratch_path: str = ""


def first_step(events: Sequence[EngineEvent]) -> StepUsage | None:
    for event in events:
        if isinstance(event, StepUsage) and not event.parent_tool_use_id:
            return event
    return None


class CacheTtlProbe:
    def __init__(
        self,
        launcher: EngineLauncher,
        cwd: str,
        clock: Clock,
        today: Callable[[], str],
        nonce: str,
        price: Price | None,
    ) -> None:
        self._launcher = launcher
        self._cwd = cwd
        self._clock = clock
        self._today = today
        self._nonce = nonce
        self._price = price

    def _once(
        self,
        options: CacheProbeOptions,
        index: int,
        previous_start: float | None,
        remaining: float,
    ) -> tuple[CacheReading, float]:
        events: list[EngineEvent] = []
        start = self._clock.monotonic()
        launch = self._launcher.launch(
            LaunchSpec(
                kind="probe-cache",
                prompt=probe_prompt(),
                cwd=self._cwd,
                allowed_tools=(),
                model=options.model,
                max_budget_usd=min(options.per_run_usd, remaining),
                append_system_prompt=f"cuanta cache probe {self._nonce}",
                session="lean",
                tools=options.tools,
                stable_prefix=True,
                persist_session=False,
            ),
            events.append,
        )
        init = next((event for event in events if isinstance(event, SessionStarted)), None)
        usage = first_step(events)
        reading = CacheReading(
            index=index,
            gap_s=0.0 if previous_start is None else start - previous_start,
            run_id=launch.run.id,
            ok=launch.outcome.ok,
            has_usage=usage is not None,
            cache_read=usage.usage.cache_read_tokens if usage is not None else 0,
            cache_write=usage.usage.cache_write_tokens if usage is not None else 0,
            write_5m=usage.write_5m_tokens if usage is not None else 0,
            write_1h=usage.write_1h_tokens if usage is not None else 0,
            fresh_input=usage.usage.input_tokens if usage is not None else 0,
            cost_usd=launch.run.cost_usd,
            engine_version=init.engine_version if init is not None else "",
            api_key_source=init.api_key_source if init is not None else "",
            local_date=self._today(),
        )
        return reading, start

    def run(self, options: CacheProbeOptions, progress: ProgressSink) -> CacheTtlResult:
        seed, previous_start = self._once(options, 0, None, options.budget_usd)
        spent = seed.cost_usd or 0.0
        charged = seed.cost_usd if seed.cost_usd is not None else options.per_run_usd
        progress.publish(
            note(
                Status.INFO,
                msg(
                    "cache_probe.seed",
                    model=options.model,
                    read=seed.cache_read,
                    written=seed.cache_write,
                ),
            )
        )
        readings: list[CacheReading] = []
        skipped: list[int] = []
        if seed.ok and seed.has_usage and seed.cache_read + seed.cache_write > 0:
            for index, gap in enumerate(options.gaps_s, 1):
                if charged + options.per_run_usd > options.budget_usd + 1e-9:
                    skipped.extend(options.gaps_s[index - 1 :])
                    progress.publish(
                        note(
                            Status.SKIP,
                            msg(
                                "cache_probe.skipped",
                                budget=f"{options.budget_usd:.2f}",
                                gap=gap,
                            ),
                        )
                    )
                    break
                progress.publish(note(Status.INFO, msg("cache_probe.waiting", seconds=gap)))
                self._clock.sleep(previous_start + gap - self._clock.monotonic())
                reading, previous_start = self._once(
                    options, index, previous_start, options.budget_usd - charged
                )
                readings.append(reading)
                spent += reading.cost_usd or 0.0
                charged += reading.cost_usd if reading.cost_usd is not None else options.per_run_usd
                result = decide(
                    seed,
                    tuple(readings),
                    tuple(skipped),
                    spent,
                    options.model,
                    self._today(),
                    options.tools,
                )
                state = result.warmth[-1] if result.warmth else Warmth.UNKNOWN
                key = f"cache_probe.{state.value}"
                reason = "no first-request usage" if not reading.has_usage else "run failed"
                progress.publish(
                    note(
                        Status.OK if state is Warmth.WARM else Status.WARN,
                        msg(
                            key,
                            gap=round(reading.gap_s),
                            read=reading.cache_read,
                            written=reading.cache_write,
                            reason=reason,
                        ),
                    )
                )
                if result.verdict in {Verdict.MEASURED, Verdict.UNSTABLE} or (
                    index == 1 and state is Warmth.UNKNOWN
                ):
                    break
        return decide(
            seed,
            tuple(readings),
            tuple(skipped),
            spent,
            options.model,
            self._today(),
            options.tools,
        )
