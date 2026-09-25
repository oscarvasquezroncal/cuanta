from __future__ import annotations

import hashlib
import io
import sys
import tarfile
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from cuanta.adapters.bench.sandbox import LocalBenchSandbox
from cuanta.adapters.bench.tasks import load_tasks, parse_task
from cuanta.adapters.system.process_runner import SubprocessRunner
from cuanta.application.mandate_flow import validate
from cuanta.domain.bench import DEFAULT_ACCEPT, BenchTask, Source, select
from cuanta.domain.errors import DomainFailure
from tests.support import FIXTURES

TASKS = Path(__file__).parents[2] / "bench" / "tasks"
KIT = TASKS.parent / "kit"
MINI = select(load_tasks(TASKS), "mini")
PUBLIC = tuple(task for task in select(load_tasks(TASKS), "full") if task.source is not None)


def _sandbox(tmp_path: Path) -> LocalBenchSandbox:
    return LocalBenchSandbox(
        FIXTURES / "repos", KIT, SubprocessRunner(), sys.executable, tmp_path / "scratch"
    )


def _apply_reference(task: BenchTask, root: Path) -> None:
    data = tomllib.loads((TASKS / f"{task.name}.toml").read_text(encoding="utf-8"))
    reference = data.get("reference", {})
    edits = data.get("reference_edit", [])
    assert reference or edits
    for relative, text in reference.items():
        (root / relative).write_text(str(text).lstrip("\n"), encoding="utf-8")
    for edit in edits:
        path = root / edit["path"]
        original = path.read_text(encoding="utf-8")
        assert edit["old"] in original
        path.write_text(original.replace(edit["old"], edit["new"], 1), encoding="utf-8")


def _check_solvable(tmp_path: Path, task: BenchTask) -> None:
    sandbox = _sandbox(tmp_path)
    root = sandbox.prepare(task, False, task.name)
    assert not (Path(root) / ".claude").exists()
    failed, _ = sandbox.accept(task, root)
    assert failed is False
    _apply_reference(task, Path(root))
    passed, output = sandbox.accept(task, root)
    assert passed is True, output
    sandbox.discard(root)
    assert not Path(root).exists()


def _archive(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        for name, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(f"repo-abc/{name}")
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_the_mini_suite_has_five_complete_tasks() -> None:
    assert len(MINI) == 5
    for task in MINI:
        assert task.prompt
        assert task.hidden
        assert task.request.what
        assert task.accept == DEFAULT_ACCEPT
        assert task.source is None
        assert (FIXTURES / "repos" / task.fixture).is_dir()


def test_the_full_suite_adds_two_pinned_public_repos() -> None:
    full = select(load_tasks(TASKS), "full")
    assert len(full) == 7
    assert len(PUBLIC) == 2
    for task in PUBLIC:
        assert task.source is not None
        assert task.source.url.startswith("https://codeload.github.com/")
        assert len(task.source.sha256) == 64


def test_parse_task_rejects_broken_documents() -> None:
    assert parse_task("name = ") is None
    assert parse_task('name = "x"\nfixture = "bugfix"\n') is None


@pytest.mark.parametrize("task", MINI, ids=lambda task: task.name)
def test_hidden_tests_fail_before_and_pass_with_the_reference(
    tmp_path: Path, task: BenchTask
) -> None:
    _check_solvable(tmp_path, task)


@pytest.mark.live
@pytest.mark.parametrize("task", PUBLIC, ids=lambda task: task.name)
def test_public_tasks_are_solvable(tmp_path: Path, task: BenchTask) -> None:
    _check_solvable(tmp_path, task)


def test_cuanta_conditions_get_the_kit(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    root = Path(sandbox.prepare(MINI[0], True, "kit"))
    assert (root / ".claude" / "agents" / "tester.md").is_file()
    assert (root / "docs" / "MANDATE_TEMPLATE.md").is_file()
    sandbox.discard(str(root))


def test_missing_fixture_and_failed_setup_are_domain_failures(tmp_path: Path) -> None:
    task = MINI[0]
    sandbox = _sandbox(tmp_path)
    with pytest.raises(DomainFailure):
        sandbox.prepare(BenchTask("x", (), "nowhere", task.request, "", {}), False, "x")
    broken = replace(task, setup=("{python} -c raise_error",))
    with pytest.raises(DomainFailure):
        sandbox.prepare(broken, False, "y")


def test_archives_are_verified_cached_and_edited(tmp_path: Path) -> None:
    data = _archive({"pkg/mod.py": "VALUE = 1\n", "README": "x"})
    fetched: list[str] = []

    def fetch(url: str) -> bytes:
        fetched.append(url)
        return data

    task = BenchTask(
        "public",
        ("full",),
        "repo",
        MINI[0].request,
        "p",
        {},
        source=Source("https://example.test/repo.tar.gz", hashlib.sha256(data).hexdigest()),
        edits=(("pkg/mod.py", "VALUE = 1", "VALUE = 2"),),
    )
    sandbox = LocalBenchSandbox(
        FIXTURES / "repos", KIT, SubprocessRunner(), sys.executable, tmp_path, fetch=fetch
    )
    first = Path(sandbox.prepare(task, False, "a"))
    assert (first / "pkg" / "mod.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    sandbox.prepare(task, False, "b")
    assert fetched == ["https://example.test/repo.tar.gz"]
    stale = replace(task, edits=(("pkg/mod.py", "VALUE = 9", "VALUE = 3"),))
    with pytest.raises(DomainFailure, match="no longer applies"):
        sandbox.prepare(stale, False, "c")
    wrong = replace(task, source=Source("https://example.test/other.tar.gz", "0" * 64))
    with pytest.raises(DomainFailure, match="checksum mismatch"):
        sandbox.prepare(wrong, False, "d")


@pytest.mark.parametrize("task", select(load_tasks(TASKS), "full"), ids=lambda task: task.name)
def test_every_task_request_passes_mandate_validation(task: BenchTask) -> None:
    validate(task.request)
