from __future__ import annotations

import threading
from collections.abc import Callable

import pytest

from cuanta.application.progress import RecordingSink, SlowSteps, later
from cuanta.domain.messages import msg
from cuanta.domain.progress import (
    FORECAST_STEP,
    INDEX_STEP,
    PLAN_STEP,
    SLOW_STEP_S,
    ProgressEvent,
    Status,
    StepFinished,
    StepStarted,
    file_count,
    finished,
    started,
    took,
)
from tests.fakes import VirtualTime

INDEX = msg("progress.index")
PLAN = msg("progress.plan")
FORECAST = msg("progress.forecast")


def slow_steps(time: VirtualTime, sink: RecordingSink | None) -> SlowSteps:
    return SlowSteps(sink, time.monotonic, time.schedule)


def test_a_step_under_two_seconds_publishes_nothing_and_cancels_its_timer() -> None:
    time, sink = VirtualTime(), RecordingSink()
    with slow_steps(time, sink).step(INDEX_STEP, INDEX) as done:
        time.advance(1.9)
        done(file_count(854))
    assert sink.events == []
    assert [timer.due for timer in time.timers] == [SLOW_STEP_S]
    assert [timer.cancelled for timer in time.timers] == [True]


def test_the_real_run_index_step_is_shown_live_and_ends_with_its_files_and_seconds() -> None:
    time, sink = VirtualTime(), RecordingSink()
    with slow_steps(time, sink).step(INDEX_STEP, INDEX) as done:
        time.advance(SLOW_STEP_S)
        assert sink.events == [StepStarted("index", "index", INDEX)]
        time.advance(50.0)
        done(file_count(854))
    detail = took(52.0, file_count(854))
    assert sink.events == [
        StepStarted("index", "index", INDEX),
        StepFinished("index", Status.OK, "854 files · 52 s", detail, 52.0),
    ]
    assert finished(INDEX_STEP, Status.OK, detail, 52.0) == sink.events[-1]


def test_a_slow_step_whose_timer_never_fired_reports_its_start_and_end_once() -> None:
    time, sink = VirtualTime(), RecordingSink()
    with slow_steps(time, sink).step(INDEX_STEP, INDEX) as done:
        time.now = 3.0
        done(file_count(1))
    assert sink.events == [
        started(INDEX_STEP, INDEX),
        StepFinished("index", Status.OK, "1 file · 3 s", took(3.0, file_count(1)), 3.0),
    ]


def fail_after(steps: SlowSteps, time: VirtualTime, seconds: float, stop: BaseException) -> None:
    with steps.step(INDEX_STEP, INDEX):
        time.advance(seconds)
        raise stop


@pytest.mark.parametrize("stop", [RuntimeError("index too large"), KeyboardInterrupt()])
def test_a_shown_step_that_fails_closes_as_failed_and_the_error_propagates(
    stop: BaseException,
) -> None:
    time, sink = VirtualTime(), RecordingSink()
    with pytest.raises(type(stop)):
        fail_after(slow_steps(time, sink), time, 5.0, stop)
    assert sink.events == [
        started(INDEX_STEP, INDEX),
        StepFinished("index", Status.FAIL, "5 s", took(5.0), 5.0),
    ]
    assert [timer.cancelled for timer in time.timers] == [True]


def test_a_quick_step_that_fails_stays_hidden() -> None:
    time, sink = VirtualTime(), RecordingSink()
    with pytest.raises(RuntimeError, match="bad"):
        fail_after(slow_steps(time, sink), time, 0.5, RuntimeError("bad"))
    assert sink.events == []


def test_a_timer_that_fires_after_the_step_closed_publishes_nothing() -> None:
    time, sink = VirtualTime(), RecordingSink()
    with slow_steps(time, sink).step(INDEX_STEP, INDEX):
        time.now = 1.0
    time.timers[0].action()
    assert sink.events == []


def test_without_a_sink_nothing_is_scheduled_and_the_clock_is_not_read() -> None:
    time = VirtualTime()
    steps = slow_steps(time, None)
    with steps.step(INDEX_STEP, INDEX) as done:
        time.advance(60.0)
        done(file_count(3))
    with steps.sequence() as phase:
        phase(PLAN_STEP, PLAN)
        time.advance(60.0)
        phase(FORECAST_STEP, FORECAST)
    assert time.timers == []
    assert time.reads == 0


def test_a_sequence_closes_each_phase_before_opening_the_next() -> None:
    time, sink = VirtualTime(), RecordingSink()
    with slow_steps(time, sink).sequence() as phase:
        phase(PLAN_STEP, PLAN)
        time.advance(3.0)
        phase(FORECAST_STEP, FORECAST)
        time.advance(0.1)
    assert sink.events == [
        started(PLAN_STEP, PLAN),
        StepFinished("plan", Status.OK, "3 s", took(3.0), 3.0),
    ]
    assert [timer.cancelled for timer in time.timers] == [True, True]


def fail_in_the_forecast(steps: SlowSteps, time: VirtualTime) -> None:
    with steps.sequence() as phase:
        phase(PLAN_STEP, PLAN)
        time.advance(0.25)
        phase(FORECAST_STEP, FORECAST)
        time.advance(2.75)
        raise ValueError("no price")


def test_a_sequence_that_fails_closes_its_open_phase_as_failed() -> None:
    time, sink = VirtualTime(), RecordingSink()
    with pytest.raises(ValueError, match="no price"):
        fail_in_the_forecast(slow_steps(time, sink), time)
    assert sink.events == [
        started(FORECAST_STEP, FORECAST),
        StepFinished("forecast", Status.FAIL, "3 s", took(2.75), 2.75),
    ]


class RevealFails:
    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    def publish(self, event: ProgressEvent) -> None:
        if isinstance(event, StepStarted):
            raise OSError("console closed")
        self.events.append(event)


def test_a_sink_that_fails_on_the_reveal_never_raises_on_the_timer_thread() -> None:
    time, sink = VirtualTime(), RevealFails()
    with SlowSteps(sink, time.monotonic, time.schedule).step(INDEX_STEP, INDEX) as done:
        time.advance(SLOW_STEP_S)
        done(file_count(2))
    assert sink.events == [
        StepFinished("index", Status.OK, "2 files · 2 s", took(2.0, file_count(2)), 2.0)
    ]


def test_later_runs_the_action_on_a_daemon_timer_that_cancel_stops() -> None:
    fired = threading.Event()
    before = set(threading.enumerate())
    cancel: Callable[[], None] = later(30.0, fired.set)
    try:
        timers = set(threading.enumerate()) - before
        assert timers and all(thread.daemon for thread in timers)
    finally:
        cancel()
    assert not fired.wait(0.05)
    ran = threading.Event()
    later(0.01, ran.set)
    assert ran.wait(5)
