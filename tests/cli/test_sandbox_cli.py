from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from cuanta.adapters.system.process_runner import SubprocessRunner
from cuanta.adapters.system.sandbox_cleanup import BackgroundCleanup, finish_cleanup
from cuanta.bootstrap import Container
from tests.fakes import FakeRunner, copy_repo
from tests.support import FIXTURES, invoke

pytestmark = pytest.mark.xdist_group("local-listener")
FAKE = FIXTURES / "fake_claude.py"
GIT = shutil.which("git")
TARGET = "src/calc/__init__.py"
FIXED = f"def add(left: int, right: int) -> int:{os.linesep}    return left + right{os.linesep}"
BROKEN = f"def add(left: int, right: int) -> int:{os.linesep}    return left - right{os.linesep}"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, fake_runner: FakeRunner, tmp_path: Path) -> dict[str, str]:
    original = Container.for_project

    def build(cls: type[Container], project: Path, verbose: bool = False) -> Container:
        container = original(project, verbose)
        container.runner = SubprocessRunner()
        return container

    monkeypatch.setattr(Container, "for_project", classmethod(build))

    def cleanup(job: BackgroundCleanup) -> None:
        assert finish_cleanup(job._path)

    monkeypatch.setattr(BackgroundCleanup, "__call__", cleanup)
    scratch = tmp_path / "temp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    values = {
        "CUANTA_CLAUDE_BIN": f'"{sys.executable}" "{FAKE}"',
        "CUANTA_PORT": "47440",
        "FAKE_CLAUDE_FIX": f"{TARGET}|left - right|left + right",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def _project(tmp_path: Path) -> Path:
    root = copy_repo("bugfix", tmp_path)
    (root / TARGET).write_bytes(BROKEN.encode())
    return root


def _sandbox_fix(root: Path, env: dict[str, str], *extra: str) -> dict[str, object]:
    result = invoke(
        [
            "mandate",
            "--type",
            "bug",
            "--what",
            "Fix add so it adds",
            "--why",
            "add(2, 3) returns -1",
            "--tests",
            "tests/test_calc.py passes",
            "--out-of-scope",
            "nothing else",
            "--simple",
            "--sandbox",
            *extra,
            "--json",
            "--project",
            str(root),
        ],
        env=env,
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return payload


def _outcome(root: Path, run_id: str) -> tuple[str, str]:
    container = Container.for_project(root)
    try:
        run = container.shared_ledger().get_run(run_id)
    finally:
        container.close()
    assert run is not None
    return run.mode, run.outcome


def test_a_sandbox_fix_leaves_the_project_untouched_and_applies_cleanly(
    tmp_path: Path, env: dict[str, str]
) -> None:
    root = _project(tmp_path)
    payload = _sandbox_fix(root, env)
    run_id = str(payload["run_id"])
    sandbox = payload["sandbox"]
    assert isinstance(sandbox, dict)
    assert sandbox["removed"] is False and sandbox["cleanup_pending"] is True
    assert (Path(str(sandbox["copy_root"])) / TARGET).read_bytes() == BROKEN.encode()
    trial = sandbox["trial"]
    assert isinstance(trial, dict)
    assert [(item["path"], item["kind"]) for item in trial["changes"]] == [(TARGET, "modified")]
    assert (root / TARGET).read_bytes() == BROKEN.encode()
    patch = (root / ".cuanta" / "trials" / run_id / "change.patch").read_bytes().decode()
    ending = os.linesep
    assert f"-    return left - right{ending}+    return left + right{ending}" in patch
    assert "-def add" not in patch
    if GIT is not None:
        checked = subprocess.run(
            ["git", "-c", "core.autocrlf=false", "apply", "--check", "--verbose", "-"],
            input=patch.encode(),
            cwd=root,
            capture_output=True,
            check=False,
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
        )
        assert checked.returncode == 0, checked.stderr.decode(errors="replace")
    assert _outcome(root, run_id) == ("sandbox", "")
    preview = invoke(["runs", "apply", run_id, "--json", "--project", str(root)], env=env)
    assert preview.exit_code == 0, preview.stdout
    assert json.loads(preview.stdout)["applied"] is False
    assert (root / TARGET).read_bytes() == BROKEN.encode()
    applied = invoke(["runs", "apply", run_id, "--yes", "--json", "--project", str(root)], env=env)
    assert applied.exit_code == 0, applied.stdout
    assert json.loads(applied.stdout)["files"] == [TARGET]
    assert (root / TARGET).read_bytes() == FIXED.encode()
    assert _outcome(root, run_id) == ("sandbox", "accepted")
    again = invoke(["runs", "apply", run_id, "--yes", "--json", "--project", str(root)], env=env)
    assert again.exit_code == 1
    assert "already applied" in json.loads(again.stdout)["error"]["message"]


def test_apply_refuses_a_target_changed_since_the_copy(tmp_path: Path, env: dict[str, str]) -> None:
    root = _project(tmp_path)
    run_id = str(_sandbox_fix(root, env)["run_id"])
    edited = BROKEN + "# the user kept working\n"
    (root / TARGET).write_bytes(edited.encode())
    refused = invoke(["runs", "apply", run_id, "--yes", "--json", "--project", str(root)], env=env)
    assert refused.exit_code == 1
    error = json.loads(refused.stdout)["error"]
    assert "changed since the copy" in error["message"]
    assert TARGET in error["message"]
    assert (root / TARGET).read_bytes() == edited.encode()
    assert _outcome(root, run_id) == ("sandbox", "")


def test_branch_hand_off_and_discard(tmp_path: Path, env: dict[str, str]) -> None:
    root = _project(tmp_path)
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/master\n", encoding="utf-8")
    run_id = str(_sandbox_fix(root, env)["run_id"])
    branches = invoke(
        ["runs", "branch", run_id, "--shell", "bash", "--json", "--project", str(root)], env=env
    )
    assert branches.exit_code == 0, branches.stdout
    handoff = json.loads(branches.stdout)["handoff"]
    assert handoff["workflow"] == "branches"
    branch = f"fix/fix-add-so-it-adds-{run_id[-4:].lower()}"
    assert handoff["branch"] == branch
    assert handoff["current_branch"] == "master"
    assert handoff["commands"][1] == f"git switch -c {branch}"
    assert handoff["one_line"] == " && ".join(handoff["commands"])
    assert f"cuanta runs apply {run_id} --yes" in handoff["commands"]
    trunk = invoke(
        [
            "runs",
            "branch",
            run_id,
            "--workflow",
            "trunk",
            "--shell",
            "cmd",
            "--json",
            "--project",
            str(root),
        ],
        env=env,
    )
    trunk_handoff = json.loads(trunk.stdout)["handoff"]
    assert trunk_handoff["branch"] is None
    assert trunk_handoff["commands"][0] == f'cd /d "{root}"'
    bad = invoke(["runs", "branch", run_id, "--shell", "tcsh", "--project", str(root)], env=env)
    assert bad.exit_code == 1
    discarded = invoke(["runs", "discard", run_id, "--json", "--project", str(root)], env=env)
    assert discarded.exit_code == 0, discarded.stdout
    assert json.loads(discarded.stdout)["rejected"] is True
    assert _outcome(root, run_id) == ("sandbox", "rejected")
    assert (root / TARGET).read_bytes() == BROKEN.encode()
    late = invoke(["runs", "apply", run_id, "--yes", "--json", "--project", str(root)], env=env)
    assert late.exit_code == 1


def test_keep_leaves_the_copy_and_needs_sandbox(tmp_path: Path, env: dict[str, str]) -> None:
    root = _project(tmp_path)
    payload = _sandbox_fix(root, env, "--keep")
    sandbox = payload["sandbox"]
    assert isinstance(sandbox, dict)
    kept = Path(str(sandbox["copy_root"]))
    assert sandbox["removed"] is False
    assert (kept / TARGET).read_bytes() == FIXED.encode()
    refused = invoke(
        [
            "mandate",
            "--type",
            "bug",
            "--what",
            "x",
            "--why",
            "y",
            "--tests",
            "t",
            "--out-of-scope",
            "z",
            "--simple",
            "--keep",
            "--json",
            "--project",
            str(root),
        ],
        env=env,
    )
    assert refused.exit_code == 1
    assert "--sandbox" in json.loads(refused.stdout)["error"]["hint"]


def test_sandbox_dry_run_creates_no_copy_and_shows_the_owned_copy_flag(
    tmp_path: Path, env: dict[str, str]
) -> None:
    root = _project(tmp_path)
    result = invoke(
        [
            "mandate",
            "--type",
            "bug",
            "--what",
            "x",
            "--why",
            "y",
            "--tests",
            "t",
            "--out-of-scope",
            "z",
            "--simple",
            "--sandbox",
            "--dry-run",
            "--engine",
            "codex",
            "--json",
            "--project",
            str(root),
        ],
        env=env,
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["sandbox"] is True
    assert "--skip-git-repo-check" in payload["command"]
    assert not (Path(tempfile.gettempdir()) / "cuanta-sandbox").exists()
    plain = invoke(
        [
            "mandate",
            "--type",
            "bug",
            "--what",
            "x",
            "--why",
            "y",
            "--tests",
            "t",
            "--out-of-scope",
            "z",
            "--simple",
            "--dry-run",
            "--engine",
            "codex",
            "--json",
            "--project",
            str(root),
        ],
        env=env,
    )
    assert "--skip-git-repo-check" not in json.loads(plain.stdout)["command"]


def test_apply_on_a_run_without_a_copy_is_refused(tmp_path: Path, env: dict[str, str]) -> None:
    root = _project(tmp_path)
    ran = invoke(
        [
            "mandate",
            "--type",
            "bug",
            "--what",
            "Fix add",
            "--why",
            "wrong",
            "--tests",
            "t",
            "--out-of-scope",
            "z",
            "--simple",
            "--json",
            "--project",
            str(root),
        ],
        env=env,
    )
    assert ran.exit_code == 0, ran.stdout
    run_id = json.loads(ran.stdout)["run_id"]
    refused = invoke(["runs", "apply", run_id, "--yes", "--json", "--project", str(root)], env=env)
    assert refused.exit_code == 1
    assert "no isolated-copy changes" in json.loads(refused.stdout)["error"]["message"]
    discard = invoke(["runs", "discard", run_id, "--json", "--project", str(root)], env=env)
    assert discard.exit_code == 1


def test_a_run_that_changes_the_project_node_modules_stops_everything(
    tmp_path: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    shared = root / "node_modules" / "lib" / "index.js"
    shared.parent.mkdir(parents=True)
    shared.write_bytes(b"module.exports = 'a'\n")
    monkeypatch.setenv("FAKE_CLAUDE_FIX", "node_modules/lib/index.js|'a'|'b'")
    ran = invoke(
        [
            "mandate",
            "--type",
            "bug",
            "--what",
            "Fix add",
            "--why",
            "wrong",
            "--tests",
            "t",
            "--out-of-scope",
            "z",
            "--simple",
            "--sandbox",
            "--json",
            "--project",
            str(root),
        ],
        env={**env, "FAKE_CLAUDE_FIX": "node_modules/lib/index.js|'a'|'b'"},
    )
    assert ran.exit_code == 1, ran.stdout
    payload = json.loads(ran.stdout)
    trial = payload["sandbox"]["trial"]
    assert trial["dependencies_changed"] == ["node_modules/lib/index.js"]
    run_id = payload["run_id"]
    branch = invoke(["runs", "branch", run_id, "--json", "--project", str(root)], env=env)
    assert branch.exit_code == 1
    assert json.loads(branch.stdout)["trial"]["guard_tripped"] is True
    show = invoke(["runs", "show", run_id, "--json", "--project", str(root)], env=env)
    assert json.loads(show.stdout)["trial"]["guard_tripped"] is True
    applied = invoke(["runs", "apply", run_id, "--yes", "--json", "--project", str(root)], env=env)
    assert applied.exit_code == 1
    assert "npm ci" in json.loads(applied.stdout)["error"]["hint"]


def test_accepting_a_sandbox_run_only_marks_it(tmp_path: Path, env: dict[str, str]) -> None:
    root = _project(tmp_path)
    payload = _sandbox_fix(root, env)
    run_id = str(payload["run_id"])
    accepted = invoke(["runs", "accept", run_id, "--project", str(root)], env=env)
    assert accepted.exit_code == 0, accepted.stdout
    assert f"cuanta runs apply {run_id}" in accepted.stdout
    assert (root / TARGET).read_bytes() == BROKEN.encode()
    assert _outcome(root, run_id) == ("sandbox", "accepted")
    applied = invoke(["runs", "apply", run_id, "--yes", "--project", str(root)], env=env)
    assert applied.exit_code == 0, applied.stdout
    assert (root / TARGET).read_bytes() == FIXED.encode()
    shown = invoke(["runs", "show", run_id, "--project", str(root)], env=env).stdout
    assert "isolated copy" in shown and shown.count("outcome") == 1


def test_rejecting_a_kept_sandbox_run_points_at_the_copy(
    tmp_path: Path, env: dict[str, str]
) -> None:
    root = _project(tmp_path)
    payload = _sandbox_fix(root, env, "--keep")
    run_id = str(payload["run_id"])
    rejected = invoke(
        ["runs", "reject", run_id, "--reason", "not needed", "--json", "--project", str(root)],
        env=env,
    )
    assert rejected.exit_code == 0, rejected.stdout
    data = json.loads(rejected.stdout)
    assert data["kept_copy"] and data["reason"] == "not needed"
    assert _outcome(root, run_id) == ("sandbox", "rejected")


def test_accepting_after_the_project_changed_says_nothing_was_applied(
    tmp_path: Path, env: dict[str, str]
) -> None:
    root = _project(tmp_path)
    payload = _sandbox_fix(root, env)
    run_id = str(payload["run_id"])
    (root / TARGET).write_bytes(b"def add(left, right):\n    return 0\n")
    accepted = invoke(["runs", "accept", run_id, "--project", str(root)], env=env)
    assert accepted.exit_code == 0, accepted.stdout
    assert "nothing was applied; the project changed since the copy" in accepted.stdout
    assert TARGET in accepted.stdout
