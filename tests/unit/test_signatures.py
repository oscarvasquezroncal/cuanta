from __future__ import annotations

import string

from hypothesis import given
from hypothesis import strategies as st

from cuanta.domain.capsules import LineRange, failure_window, parse_range, slice_lines
from cuanta.domain.gateway import choose_runner, split_command
from cuanta.domain.testing import (
    Frame,
    GatewayStatus,
    TestFailure,
    TestOutcome,
    cluster,
    gateway_status,
    normalize_message,
    persistent_signatures,
    representative_location,
    signature_id,
    split_error,
    summary,
)

ROOT = "/work/repo"


def test_normalization_strips_volatile_parts() -> None:
    assert (
        normalize_message("KeyError: 'user-6f1c2b7e-1d2a-4c3b-9f00-123456789abc'")
        == "KeyError: 'user-<uuid>'"
    )
    assert normalize_message("object at 0x7f3a2c10") == "object at <hex>"
    assert normalize_message("No such file: '/tmp/pytest-12/data.txt'") == "No such file: '<tmp>'"
    assert normalize_message(r"missing C:\Users\me\AppData\Local\Temp\x1\f.txt") == "missing <tmp>"
    assert normalize_message("failed at /home/me/repo/src/app.py:42") == "failed at app.py"
    assert normalize_message("expected 30, got 31") == "expected <n>, got <n>"
    assert normalize_message("first\nsecond") == "first"


def test_signature_ids_are_twelve_hex() -> None:
    value = signature_id("KeyError", "'a'")
    assert len(value) == 12
    assert all(character in string.hexdigits for character in value)


@given(st.integers(min_value=0, max_value=10**9), st.integers(min_value=0, max_value=10**9))
def test_numbers_never_split_signatures(left: int, right: int) -> None:
    assert signature_id("AssertionError", f"assert {left} == {right}") == signature_id(
        "AssertionError", "assert 1 == 2"
    )


@given(st.uuids(), st.uuids())
def test_uuids_never_split_signatures(first: object, second: object) -> None:
    assert signature_id("KeyError", f"user {first}") == signature_id("KeyError", f"user {second}")


@given(st.integers(min_value=0, max_value=2**48))
def test_hex_addresses_never_split_signatures(address: int) -> None:
    assert normalize_message(f"<Obj at {hex(address)}>") == "<Obj at <hex>>"


@given(
    st.lists(st.sampled_from(["src", "pkg", "app", "a b"]), min_size=1, max_size=4),
    st.sampled_from(["x.py", "y.ts"]),
)
def test_absolute_paths_reduce_to_basename(parts: list[str], name: str) -> None:
    safe = [part.replace(" ", "_") for part in parts]
    path = "/" + "/".join([*safe, name])
    assert normalize_message(f"boom in {path}") == f"boom in {name}"


@given(st.text(max_size=200))
def test_normalization_is_idempotent(text: str) -> None:
    once = normalize_message(text)
    assert normalize_message(once) == once


def test_different_error_types_differ() -> None:
    assert signature_id("KeyError", "x") != signature_id("ValueError", "x")


def test_representative_location_skips_external_frames() -> None:
    frames = (
        Frame("/usr/lib/python3.12/unittest/case.py", 10),
        Frame("/work/repo/.venv/lib/site-packages/lib.py", 3),
        Frame("/work/repo/node_modules/x/index.js", 1),
        Frame("/work/repo/src/app.py", 42),
    )
    assert representative_location(frames, ROOT) == "src/app.py:42"
    assert representative_location((Frame("tests/test_a.py", 7),), ROOT) == "tests/test_a.py:7"
    assert representative_location((Frame("/elsewhere/x.py", 1),), ROOT) == ""


def test_cluster_groups_by_signature_and_orders_by_size() -> None:
    failures = [
        TestFailure(f"t{index}", "AssertionError", f"assert {index} == 0") for index in range(5)
    ]
    failures.append(TestFailure("k", "KeyError", "'a'", (Frame("src/a.py", 3),)))
    signatures = cluster(failures, ROOT)
    assert [signature.tests for signature in signatures] == [5, 1]
    assert signatures[0].first == "t0"
    assert signatures[0].verbatim == "AssertionError: assert 0 == 0"
    assert signatures[1].location == "src/a.py:3"


def test_summary_is_bounded() -> None:
    failures = [TestFailure(f"t{index}", f"E{index}Error", "boom") for index in range(30)]
    outcome = TestOutcome(0, 30, 0, 0, 1.0, tuple(failures))
    lines = summary(outcome, cluster(failures, ROOT), "pytest")
    assert len(lines) <= 12
    assert lines[-1].startswith("...")


def test_status_and_breaker() -> None:
    red = TestOutcome(1, 1, 0, 0, 0.1, (TestFailure("t", "E", "m"),))
    green = TestOutcome(3, 0, 0, 0, 0.1, ())
    assert gateway_status(green, ()) is GatewayStatus.GREEN
    assert gateway_status(red, ()) is GatewayStatus.RED
    assert gateway_status(red, ("abc",)) is GatewayStatus.PERSISTENT
    assert persistent_signatures(["a", "b"], ["b", "c"]) == ("b",)


def test_split_error() -> None:
    assert split_error("KeyError: 'x'") == ("KeyError", "'x'")
    assert split_error("builtins.ValueError: bad") == ("ValueError", "bad")
    assert split_error("assert 1 == 2", "AssertionError") == ("AssertionError", "assert 1 == 2")


def test_choose_runner() -> None:
    assert choose_runner("pytest", "uv run pytest", "", "") is not None
    choice = choose_runner("go test", "go test ./...", "", "")
    assert choice is not None and choice.name == "go"
    generic = choose_runner("rspec", "bundle exec rspec", "", "")
    assert generic is not None and generic.name == "generic"
    override = choose_runner("pytest", "pytest", "", "make test")
    assert override is not None and override.command == "make test"
    assert choose_runner("", "", "", "") is None


def test_split_command_windows_keeps_backslashes() -> None:
    assert split_command(r'"C:\Py\python.exe" -m pytest', True) == (
        r"C:\Py\python.exe",
        "-m",
        "pytest",
    )
    assert split_command("uv run pytest -q", False) == ("uv", "run", "pytest", "-q")


def test_capsule_ranges_and_windows() -> None:
    assert parse_range("3:5") == LineRange(3, 5)
    assert parse_range(":4") == LineRange(1, 4)
    lines = [f"line {index}" for index in range(1, 101)]
    assert slice_lines(lines, LineRange(99, 0)) == [(99, "line 99"), (100, "line 100")]
    lines[60] = "Traceback (most recent call last):"
    window = failure_window(lines)
    assert len(window) == 40
    assert window[0][0] == 51
    quiet = failure_window(["ok"] * 10)
    assert [number for number, _ in quiet] == list(range(1, 11))
