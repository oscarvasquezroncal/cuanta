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
    values = iter(value) if isinstance(value, list) else None

    def lookup(*args: Any, **kwargs: Any) -> io.BytesIO:
        response = next(values) if values is not None else value
        if isinstance(response, Exception):
            raise response
        return io.BytesIO(json.dumps(response).encode())

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
    registry(
        monkeypatch,
        publisher,
        [HTTPError("fixture", 404, "missing", Message(), None), {"name": "cuanta"}],
    )
    calls: list[list[str]] = []
    configs: list[Path] = []
    original = subprocess.run

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command[0] != "fake-npm":
            return original(command, **kwargs)
        calls.append(command)
        if "publish" in command:
            assert "--userconfig" in command
            config = Path(command[command.index("--userconfig") + 1])
            assert config.read_text(encoding="utf-8") == "registry=https://registry.npmjs.org/\n"
            configs.append(config)
        return subprocess.CompletedProcess(command, 0, version, "")

    monkeypatch.setattr(publisher.subprocess, "run", run)
    tarball = tmp_path / "cuanta.tgz"
    assert publisher.publish(tarball, "cuanta", "0.5.0") is True
    assert calls[0] == ["fake-npm", "--version"]
    assert calls[1] == [
        "fake-npm",
        "publish",
        str(tarball),
        "--provenance",
        "--access",
        "public",
        "--userconfig",
        str(configs[0]),
    ]
    assert not configs[0].exists()


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
    registry(
        monkeypatch,
        publisher,
        [HTTPError("fixture", 404, "missing", Message(), None), {"name": "cuanta"}],
    )
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


def test_missing_package_requires_manual_first_publication_before_any_npm_call(
    publisher: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry(monkeypatch, publisher, HTTPError("fixture", 404, "missing", Message(), None))
    monkeypatch.setattr(publisher.subprocess, "run", lambda *a, **k: pytest.fail("npm ran"))
    with pytest.raises(ValueError, match=r"first publication.*npm login.*2FA"):
        publisher.publish(tmp_path / "cuanta.tgz", "cuanta", "0.5.1")


def test_temporary_userconfig_is_cleaned_when_publication_fails(
    publisher: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry(
        monkeypatch,
        publisher,
        [HTTPError("fixture", 404, "missing", Message(), None), {"name": "cuanta"}],
    )
    configs: list[Path] = []
    original = subprocess.run

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command[0] != "fake-npm":
            return original(command, **kwargs)
        if "publish" in command:
            config = Path(command[command.index("--userconfig") + 1])
            configs.append(config)
            assert config.read_text(encoding="utf-8") == "registry=https://registry.npmjs.org/\n"
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, "11.5.1", "")

    monkeypatch.setattr(publisher.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        publisher.publish(tmp_path / "cuanta.tgz", "cuanta", "0.5.1")
    assert len(configs) == 1 and not configs[0].exists()


@pytest.mark.parametrize(
    "response",
    [
        HTTPError("fixture", 503, "offline", Message(), None),
        {"name": "other"},
        [],
    ],
)
def test_package_lookup_failure_cannot_reach_npm(
    publisher: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    response: object,
) -> None:
    registry(
        monkeypatch, publisher, [HTTPError("fixture", 404, "missing", Message(), None), response]
    )
    monkeypatch.setattr(publisher.subprocess, "run", lambda *a, **k: pytest.fail("npm ran"))
    with pytest.raises((HTTPError, ValueError)):
        publisher.publish(tmp_path / "cuanta.tgz", "cuanta", "0.5.1")
