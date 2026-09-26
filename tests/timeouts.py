from __future__ import annotations

import faulthandler
import os
import sys
from collections.abc import Sequence
from typing import Protocol

import pytest

LONG_S = 300
STDERR: list[int] = []


class TimeoutSettings(Protocol):
    @property
    def timeout(self) -> float: ...

    @property
    def method(self) -> str: ...

    @property
    def disable_debugger_detection(self) -> bool: ...


class Marked(Protocol):
    def get_closest_marker(self, name: str) -> pytest.Mark | None: ...

    def add_marker(self, marker: pytest.MarkDecorator) -> None: ...


def remember_stderr() -> None:
    if STDERR:
        return
    for stream in (sys.stderr, sys.__stderr__):
        if stream is None:
            continue
        try:
            STDERR.append(os.dup(stream.fileno()))
        except (AttributeError, OSError, ValueError):
            continue
        return


def debugging(item: pytest.Item, settings: TimeoutSettings) -> bool:
    if settings.disable_debugger_detection:
        return False
    plugin = item.config.pluginmanager.getplugin("timeout")
    return bool(plugin.is_debugging())


@pytest.hookimpl(optionalhook=True)
def pytest_timeout_set_timer(item: pytest.Item, settings: TimeoutSettings) -> bool | None:
    if settings.method != "thread" or debugging(item, settings) or not STDERR:
        return None
    faulthandler.dump_traceback_later(settings.timeout, exit=True, file=STDERR[0])
    return True


@pytest.hookimpl(optionalhook=True)
def pytest_timeout_cancel_timer(item: pytest.Item) -> bool | None:
    faulthandler.cancel_dump_traceback_later()
    return None


def pytest_enter_pdb() -> None:
    faulthandler.cancel_dump_traceback_later()


def unbounded_live(items: Sequence[Marked]) -> None:
    for item in items:
        if item.get_closest_marker("live") and not item.get_closest_marker("timeout"):
            item.add_marker(pytest.mark.timeout(0))
