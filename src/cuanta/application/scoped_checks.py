from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from cuanta.application.verification import Verifier
from cuanta.domain.role_handoff import VerifyResult
from cuanta.domain.scoped_checks import (
    PATH_RUNNERS,
    affected_tests,
    changed_paths,
    daemon_command,
    daemon_control,
    runner_of,
    scoped_test_command,
)

DAEMON_EXITS = frozenset({0, 1})


class ScopedChecks:
    def __init__(
        self,
        verifier: Verifier,
        commands: Sequence[str],
        sources: tuple[str, ...],
        snapshot: Callable[[], Mapping[str, str]],
        parallel: bool,
        neighbours: Mapping[str, frozenset[str]] | None = None,
    ) -> None:
        self._verifier = verifier
        self._sources = sources
        self._snapshot = snapshot
        self._parallel = parallel
        self._neighbours = neighbours or {}
        self._start = dict(snapshot())
        self._daemons = {
            command: found for command in commands if (found := daemon_command(command)) is not None
        }
        self._runners = {
            command: runner
            for command in commands
            if (runner := runner_of(command)) in PATH_RUNNERS
        }
        self._daemon_used = False

    @property
    def signature(self) -> str:
        return "|".join((*sorted(self._daemons.values()), *self._sources))

    def baseline(
        self, commands: Sequence[str], stopped: Callable[[], bool]
    ) -> tuple[VerifyResult, ...]:
        return self._checks(commands, stopped, self._sources, tuple(self._start), daemon=True)

    def __call__(
        self, commands: Sequence[str], stopped: Callable[[], bool]
    ) -> tuple[VerifyResult, ...]:
        return self._checks(commands, stopped, *self._current(), daemon=True)

    def final(
        self, commands: Sequence[str], stopped: Callable[[], bool]
    ) -> tuple[VerifyResult, ...]:
        return self._checks(commands, stopped, *self._current(), daemon=False)

    def close(self) -> None:
        if self._daemon_used:
            self._verifier.run(
                tuple(daemon_control(found, "stop") for found in self._daemons.values())
            )
            self._daemon_used = False

    def _current(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        current = self._snapshot()
        changed = (*self._sources, *changed_paths(self._start, current))
        return tuple(dict.fromkeys(changed)), tuple(current)

    def _checks(
        self,
        commands: Sequence[str],
        stopped: Callable[[], bool],
        changed: tuple[str, ...],
        files: tuple[str, ...],
        daemon: bool,
    ) -> tuple[VerifyResult, ...]:
        actual: dict[str, str] = {}
        skipped: set[str] = set()
        for command in commands:
            runner = self._runners.get(command, "")
            if runner:
                tests, full = affected_tests(runner, changed, files, self._neighbours)
                scoped = scoped_test_command(command, runner, tests, self._parallel and full)
                if (tests or full) and scoped is not None:
                    actual[command] = scoped
                else:
                    skipped.add(command)
            elif daemon and command in self._daemons:
                actual[command] = self._daemons[command]
        ran = tuple(command for command in commands if command not in skipped)
        found = {
            result.command: result
            for result in self._verifier.run_checks(ran, stopped, actual=actual)
        }
        if daemon:
            found.update(self._retried(found, actual, stopped))
        return tuple(
            found.get(command, VerifyResult(command, 0))
            for command in commands
            if command in found or command in skipped
        )

    def _retried(
        self,
        found: Mapping[str, VerifyResult],
        actual: Mapping[str, str],
        stopped: Callable[[], bool],
    ) -> dict[str, VerifyResult]:
        retried: dict[str, VerifyResult] = {}
        for command, daemonized in tuple(self._daemons.items()):
            if actual.get(command) != daemonized or command not in found:
                continue
            self._daemon_used = True
            result = found[command]
            if result.exit_code in DAEMON_EXITS and not result.broken:
                continue
            self._verifier.run((daemon_control(daemonized, "kill"),), stopped)
            again = self._verifier.run((command,), stopped, actual)
            if again and again[0].exit_code in DAEMON_EXITS and not again[0].broken:
                retried[command] = again[0]
                continue
            self._verifier.run((daemon_control(daemonized, "kill"),), stopped)
            del self._daemons[command]
            plain = self._verifier.run((command,), stopped)
            if plain:
                retried[command] = plain[0]
        return retried
