from __future__ import annotations

import importlib
import io
import json
import subprocess
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.error import HTTPError, URLError

import pytest


@pytest.fixture
def publisher(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = importlib.import_module("scripts.npm.publish")
    monkeypatch.setattr(module, "npm_command", lambda: ["fake-npm"])
    return module


def registry(monkeypatch: pytest.MonkeyPatch, publisher: ModuleType, value: object) -> None:
    def lookup(*args: Any, **kwargs: Any) -> io.BytesIO:
        if isinstance(value, Exception):
            raise value
        return io.BytesIO(json.dumps(value).encode())

    monkeypatch.setattr(publisher, "urlopen", lookup)


def test_existing_version_skips_publication(
    publisher: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry(monkeypatch, publisher, {"version": "0.5.0"})
    monkeypatch.setattr(publisher.subprocess, "run", lambda *a, **k: pytest.fail("npm ran"))
    assert publisher.publish(tmp_path / "cuanta.tgz", "cuanta", "0.5.0") is False


@pytest.mark.parametrize("version", ["11.5.1", "11.15.0", "12.0.0"])
def test_missing_version_publishes_once_with_supported_npm(
    publisher: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, version: str
) -> None:
    registry(monkeypatch, publisher, HTTPError("fixture", 404, "missing", Message(), None))
    calls: list[list[str]] = []
    original = subprocess.run

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command[0] != "fake-npm":
            return original(command, **kwargs)
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, version, "")

    monkeypatch.setattr(publisher.subprocess, "run", run)
    tarball = tmp_path / "cuanta.tgz"
    assert publisher.publish(tarball, "cuanta", "0.5.0") is True
    assert calls == [
        ["fake-npm", "--version"],
        ["fake-npm", "publish", str(tarball), "--provenance", "--access", "public"],
    ]


@pytest.mark.parametrize(
    "error", [HTTPError("fixture", 503, "unavailable", Message(), None), URLError("offline")]
)
def test_registry_failure_never_attempts_publication(
    publisher: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception
) -> None:
    registry(monkeypatch, publisher, error)
    monkeypatch.setattr(publisher.subprocess, "run", lambda *a, **k: pytest.fail("npm ran"))
    with pytest.raises((HTTPError, URLError)):
        publisher.publish(tmp_path / "cuanta.tgz", "cuanta", "0.5.0")


@pytest.mark.parametrize("version", ["11.5.0", "10.9.0", "bad"])
def test_old_or_invalid_npm_cannot_publish(
    publisher: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, version: str
) -> None:
    registry(monkeypatch, publisher, HTTPError("fixture", 404, "missing", Message(), None))
    calls: list[list[str]] = []
    original = subprocess.run

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command[0] != "fake-npm":
            return original(command, **kwargs)
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, version, "")

    monkeypatch.setattr(publisher.subprocess, "run", run)
    with pytest.raises(ValueError, match=r"11\.5\.1"):
        publisher.publish(tmp_path / "cuanta.tgz", "cuanta", "0.5.0")
    assert calls == [["fake-npm", "--version"]]
