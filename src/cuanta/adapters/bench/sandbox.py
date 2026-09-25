from __future__ import annotations

import hashlib
import io
import shutil
import tarfile
import tempfile
from collections.abc import Callable
from pathlib import Path

from cuanta.domain.bench import BenchTask, Source, command_args
from cuanta.domain.errors import DomainFailure
from cuanta.ports.system import ProcessRunner

SKIPPED = shutil.ignore_patterns(".cuanta", "__pycache__", ".pytest_cache", ".venv", ".mypy_cache")
SETUP_TIMEOUT_S = 600.0
ACCEPT_TIMEOUT_S = 900.0
TAIL_CHARS = 2_000
DOWNLOAD_TIMEOUT_S = 120.0


def download(url: str) -> bytes:
    import httpx

    response = httpx.get(url, timeout=DOWNLOAD_TIMEOUT_S, follow_redirects=True)
    response.raise_for_status()
    return response.content


def unpack(archive: bytes, target: Path) -> None:
    target.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        members = []
        for member in bundle.getmembers():
            parts = Path(member.name).parts
            if len(parts) < 2:
                continue
            member.name = str(Path(*parts[1:]))
            members.append(member)
        bundle.extractall(target, members=members, filter="data")


def _edit(root: Path, edits: tuple[tuple[str, str, str], ...], task: str) -> None:
    for relative, old, new in edits:
        path = root / relative
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if old not in text:
            raise DomainFailure(
                f"bench edit for {task} no longer applies to {relative}",
                "the pinned source changed; update the task",
            )
        path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _write(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


class LocalBenchSandbox:
    def __init__(
        self,
        fixtures: Path,
        kit: Path,
        runner: ProcessRunner,
        python: str,
        scratch: Path | None = None,
        keep: bool = False,
        fetch: Callable[[str], bytes] = download,
    ) -> None:
        self._fetch = fetch
        self._fixtures = fixtures
        self._kit = kit
        self._runner = runner
        self._python = python
        self._scratch = scratch
        self._keep = keep

    def _archive(self, source: Source) -> bytes:
        cache = (self._scratch or Path(tempfile.gettempdir())) / "cuanta-bench-cache"
        cached = cache / f"{source.sha256}.tar.gz"
        if cached.is_file():
            return cached.read_bytes()
        try:
            data = self._fetch(source.url)
        except Exception as error:
            raise DomainFailure(f"could not download {source.url}", str(error)) from error
        digest = hashlib.sha256(data).hexdigest()
        if digest != source.sha256:
            raise DomainFailure(
                f"checksum mismatch for {source.url}", f"expected {source.sha256}, got {digest}"
            )
        cache.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
        return data

    def prepare(self, task: BenchTask, with_kit: bool, label: str) -> str:
        fixture = self._fixtures / task.fixture
        if task.source is None and not fixture.is_dir():
            raise DomainFailure(
                f"bench fixture {task.fixture} not found in {self._fixtures}",
                "run the bench from the cuanta source tree or pass --fixtures",
            )
        archive = self._archive(task.source) if task.source is not None else b""
        if self._scratch is not None:
            self._scratch.mkdir(parents=True, exist_ok=True)
        base = Path(tempfile.mkdtemp(prefix=f"cuanta-bench-{label}-", dir=self._scratch))
        root = base / task.fixture
        try:
            if task.source is None:
                shutil.copytree(fixture, root, ignore=SKIPPED)
            else:
                unpack(archive, root)
            _write(root, dict(task.files))
            _edit(root, task.edits, task.name)
            if with_kit and self._kit.is_dir():
                shutil.copytree(self._kit, root, dirs_exist_ok=True)
            for command in task.setup:
                done = self._runner.run(
                    command_args(command, self._python), cwd=root, timeout=SETUP_TIMEOUT_S
                )
                if not done.ok:
                    raise DomainFailure(
                        f"bench setup failed for {task.name}: {command}",
                        (done.stderr or done.stdout)[-TAIL_CHARS:],
                    )
        except BaseException:
            shutil.rmtree(base, ignore_errors=True)
            raise
        return str(root)

    def accept(self, task: BenchTask, root: str) -> tuple[bool, str]:
        folder = Path(root)
        _write(folder, dict(task.hidden))
        args = command_args(task.accept, self._python, tuple(task.hidden))
        done = self._runner.run(args, cwd=folder, timeout=ACCEPT_TIMEOUT_S)
        return done.ok, (done.stdout + done.stderr)[-TAIL_CHARS:]

    def discard(self, root: str) -> None:
        if not self._keep:
            shutil.rmtree(Path(root).parent, ignore_errors=True)
