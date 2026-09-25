import os
import tempfile
import uuid

import pytest


def load_user(store: dict[str, int], key: str) -> int:
    return store[key]


@pytest.mark.parametrize("n", range(20))
def test_lookup(n: int) -> None:
    assert load_user({}, f"user-{uuid.uuid4()}") == n


@pytest.mark.parametrize("n", range(15))
def test_math(n: int) -> None:
    assert n * 3 == n * 3 + 1


@pytest.mark.parametrize("n", range(15))
def test_files(n: int) -> None:
    with open(os.path.join(tempfile.gettempdir(), f"missing-{uuid.uuid4()}-{n}.txt")) as handle:
        assert handle.read()


@pytest.mark.parametrize("n", range(7))
def test_passes(n: int) -> None:
    assert n >= 0
