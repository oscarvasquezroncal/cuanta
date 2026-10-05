from __future__ import annotations

import re
import threading
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from cuanta.adapters.system.log_file import LogFile
from cuanta.bootstrap import record_failure

STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z ")


def test_source_checkout_failure_logs_are_isolated_from_the_real_project() -> None:
    checkout = Path(__file__).resolve().parents[2]
    with patch.object(LogFile, "_rotate", autospec=True) as rotated:
        record_failure(checkout, RuntimeError("isolated test failure"))
    assert rotated.call_count == 1
    assert not rotated.call_args.args[1].is_relative_to(checkout / ".cuanta/logs")


def test_listener_tracebacks_also_reach_the_daily_log(tmp_path: Path) -> None:
    path = tmp_path / "logs/listener.log"
    LogFile(path).write(
        "listener failed", "Traceback (most recent call last):\nValueError: token=abc123"
    )
    daily = path.parent / f"{datetime.now().date().isoformat()}.log"
    assert daily.read_text(encoding="utf-8") == path.read_text(encoding="utf-8")
    assert "abc123" not in daily.read_text(encoding="utf-8")


def test_entries_are_stamped_redacted_and_appended(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "logs" / "listener.log"
    log = LogFile(path)
    log.write("unreadable claude_code.tool_result", "ValueError: token=abcdef123456\n")
    log.write("second", "mail ops@example.invalid")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert STAMP.match(lines[0])
    assert lines[0].endswith(" unreadable claude_code.tool_result")
    assert lines[1] == "ValueError: token=[redacted]"
    assert lines[2].endswith(" second")
    assert lines[3] == "mail [email]"
    assert len(lines) == 4
    assert log.path == path
    assert capsys.readouterr().err == ""


def test_echo_repeats_the_entry_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "listener.log"
    LogFile(path, echo=True).write("boom", "Traceback (most recent call last):\nValueError: x")
    err = capsys.readouterr().err
    assert "boom" in err and "ValueError: x" in err
    assert err == path.read_text(encoding="utf-8")


def test_no_path_and_no_echo_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    LogFile(None).write("quiet", "details")
    assert capsys.readouterr().err == ""
    assert list(tmp_path.iterdir()) == []


def test_the_log_rotates_once_over_its_limit(tmp_path: Path) -> None:
    path = tmp_path / "listener.log"
    log = LogFile(path, limit_bytes=40)
    log.write("first", "x" * 50)
    log.write("second", "")
    log.write("third", "")
    rotated = path.with_name("listener.log.1")
    assert "first" in rotated.read_text(encoding="utf-8")
    current = path.read_text(encoding="utf-8")
    assert "second" in current and "third" in current
    log.write("fourth", "y" * 50)
    log.write("fifth", "")
    assert "first" not in rotated.read_text(encoding="utf-8")
    assert "fourth" in rotated.read_text(encoding="utf-8")
    assert sorted(item.name for item in tmp_path.iterdir()) == ["listener.log", "listener.log.1"]


def test_unwritable_targets_are_ignored(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    blocker = tmp_path / "logs"
    blocker.write_text("a file where the directory should be", encoding="utf-8")
    LogFile(blocker / "listener.log").write("lost", "details")
    LogFile(tmp_path / "bad.log").write("surrogate \udcff", "")
    assert capsys.readouterr().err == ""
    assert "surrogate" in (tmp_path / "bad.log").read_text(encoding="utf-8")


def test_concurrent_writers_keep_whole_entries(tmp_path: Path) -> None:
    path = tmp_path / "listener.log"
    log = LogFile(path)

    def burst(name: str) -> None:
        for index in range(50):
            log.write(f"{name} {index}", f"{name} detail {index}")

    threads = [threading.Thread(target=burst, args=(f"t{number}",)) for number in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 400
    for title, detail in zip(lines[::2], lines[1::2], strict=True):
        name, index = title.split(" ")[1:]
        assert detail == f"{name} detail {index}"
