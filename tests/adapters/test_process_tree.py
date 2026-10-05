from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from cuanta.adapters.system import process_tree


def test_job_close_waits_until_all_processes_have_exited(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = MagicMock()
    remaining = iter((1, 0))
    observed: list[int] = []

    def query(job: int, mode: int, target: Any, size: int, length: Any) -> bool:
        active = next(remaining)
        target._obj.ActiveProcesses = active
        observed.append(active)
        return True

    kernel.QueryInformationJobObject.side_effect = query
    monkeypatch.setattr(process_tree, "_kernel", lambda: kernel)
    tree = process_tree.ProcessTree.__new__(process_tree.ProcessTree)
    tree._process = MagicMock()
    tree._job = 123
    tree._close_job()
    assert observed == [1, 0]
    kernel.TerminateJobObject.assert_called_once_with(123, 1)
    kernel.CloseHandle.assert_called_once_with(123)
    assert tree._job is None
