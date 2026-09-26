from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from cuanta.adapters.system import shell

TABLE = {10: (1, "python.exe"), 11: (10, "node.exe"), 12: (11, "sleep.exe"), 20: (1, "other.exe")}


def test_posix_table_excludes_its_probe_but_keeps_other_ps_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = "10 1 /usr/bin/python\n11 10 /bin/ps\n12 10 /bin/ps\ninvalid\n"
    probe = MagicMock(pid=11, returncode=0)
    probe.__enter__.return_value = probe
    probe.communicate.return_value = (output, "")
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", lambda *args, **kwargs: probe)
        table = shell._posix_table()
    assert table == {10: (1, "python"), 12: (10, "ps")}
    assert shell.descendants(10, table) == {12}


def test_posix_probe_timeout_kills_and_reaps_the_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = MagicMock()
    probe.__enter__.return_value = probe
    probe.communicate.side_effect = [subprocess.TimeoutExpired("ps", 10), ("", "")]
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", lambda *args, **kwargs: probe)
        assert shell._posix_table() == {}
    probe.kill.assert_called_once_with()
    assert probe.communicate.call_count == 2
    probe.communicate.assert_called_with()


def test_posix_probe_failure_returns_no_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = MagicMock(returncode=1)
    probe.__enter__.return_value = probe
    probe.communicate.return_value = ("11 10 /bin/ps\n", "denied")
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", lambda *args, **kwargs: probe)
        assert shell._posix_table() == {}


def test_live_descendants_find_real_children_without_the_probe() -> None:
    with subprocess.Popen(
        [sys.executable, "-c", "import sys; print('ready', flush=True); sys.stdin.readline()"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as child:
        try:
            assert child.stdout is not None
            assert child.stdout.readline().strip() == "ready"
            assert shell.descendants(os.getpid()) == {child.pid, *shell.descendants(child.pid)}
        finally:
            child.communicate("\n", timeout=5)
    assert child.returncode == 0
    assert shell.descendants(os.getpid()) == set()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process handles outlive termination")
@pytest.mark.parametrize("exit_code", [0, 259])
def test_live_walk_rejects_exited_processes_in_a_retained_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
) -> None:
    with subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"import sys; print('ready', flush=True); sys.stdin.readline(); sys.exit({exit_code})",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as child:
        try:
            assert child.stdout is not None
            assert child.stdout.readline().strip() == "ready"
            snapshot = shell.process_table()
            assert child.pid in snapshot
            assert shell.descendants(os.getpid()) == {child.pid, *shell.descendants(child.pid)}
        finally:
            child.communicate("\n", timeout=5)
    assert child.returncode == exit_code
    assert shell._creation_time(child.pid) is not None
    with monkeypatch.context() as patch:
        patch.setattr(shell, "process_table", lambda: snapshot)
        assert shell.descendants(os.getpid()) == set()


def test_born_after_needs_both_times_in_order() -> None:
    assert shell._born_after(200, 100)
    assert shell._born_after(100, 100)
    assert not shell._born_after(50, 100)
    assert not shell._born_after(None, 100)
    assert not shell._born_after(200, None)


def test_a_given_table_is_walked_without_time_checks() -> None:
    assert shell.descendants(10, TABLE) == {11, 12}


def test_live_walk_rejects_children_older_than_their_recorded_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    born = {10: 100, 11: 150, 12: 50}
    monkeypatch.setattr(shell, "process_table", lambda: TABLE)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(shell, "_creation_time", born.get, raising=False)
    monkeypatch.setattr(shell, "_process_exited", lambda _: False)
    assert shell.descendants(10) == {11}
    born[12] = 160
    assert shell.descendants(10) == {11, 12}


def test_live_walk_keeps_living_grandchildren_behind_an_exited_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    born = {10: 100, 11: 150, 12: 160}
    monkeypatch.setattr(shell, "process_table", lambda: TABLE)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(shell, "_creation_time", born.get)
    monkeypatch.setattr(shell, "_process_exited", lambda pid: pid == 11)
    assert shell.descendants(10) == {12}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process handles support native waits")
@pytest.mark.parametrize(("wait_status", "exited"), [(0, True), (258, False), (0xFFFFFFFF, False)])
def test_process_exit_requires_a_signaled_handle(
    monkeypatch: pytest.MonkeyPatch, wait_status: int, exited: bool
) -> None:
    kernel = MagicMock()
    kernel.OpenProcess.return_value = 123
    kernel.WaitForSingleObject.return_value = wait_status
    with monkeypatch.context() as patch:
        patch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel)
        assert shell._process_exited(42) is exited
    kernel.OpenProcess.assert_called_once_with(shell.SYNCHRONIZE, False, 42)
    kernel.WaitForSingleObject.assert_called_once_with(123, 0)
    kernel.CloseHandle.assert_called_once_with(123)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows OpenProcess reports process absence")
@pytest.mark.parametrize(("error", "exited"), [(87, True), (5, False)])
def test_process_exit_distinguishes_missing_pids_from_access_denied(
    monkeypatch: pytest.MonkeyPatch, error: int, exited: bool
) -> None:
    kernel = MagicMock()
    kernel.OpenProcess.return_value = None
    with monkeypatch.context() as patch:
        patch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel)
        patch.setattr(ctypes, "get_last_error", lambda: error)
        assert shell._process_exited(42) is exited
    kernel.WaitForSingleObject.assert_not_called()
    kernel.CloseHandle.assert_not_called()
