from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from pathlib import Path
from typing import Any

from cuanta.adapters.system.sandbox import LocalSandbox
from cuanta.ports.sandbox import SandboxCopy
from dev.results import Report
from dev.spec import Trial, relative


def safe_path(root: Path, name: str) -> Path:
    target = root / relative(name)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Path escapes the owned copy")
    return target


def after_images(project: Path, run_id: str, root: Path) -> list[str]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise ValueError("Unsafe run ID")
    folder = project / ".cuanta" / "trials" / run_id
    if not folder.resolve().is_relative_to((project / ".cuanta" / "trials").resolve()):
        raise ValueError("Trial directory escapes storage")
    data = json.loads((folder / "trial.json").read_text(encoding="utf-8"))
    if data.get("dependencies_changed_count", 0) or data.get("state_changed_count", 0):
        raise ValueError("Stored trial guard tripped")
    prepared: list[tuple[Path, bytes | None]] = []
    for change in data["changes"]:
        target = safe_path(root, change["path"])
        if change["kind"] == "deleted":
            prepared.append((target, None))
        elif change["kind"] in {"added", "modified"}:
            source = safe_path(folder / "files", change["path"])
            content = source.read_bytes()
            if hashlib.sha256(content).hexdigest() != change["after"]:
                raise ValueError("After-image hash mismatch")
            prepared.append((target, content))
        else:
            raise ValueError("Unknown change kind")
    for target, image in prepared:
        if image is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(image)
    return [str(target.relative_to(root)) for target, _ in prepared]


def environment_for(copy: SandboxCopy) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("CUANTA_STATE_ROOT", None)
    environment["PYTHONUTF8"] = "1"
    python_paths = copy.python_path or environment.get("PYTHONPATH", "")
    remapped: list[str] = []
    for entry in python_paths.split(os.pathsep):
        if not entry:
            continue
        path = Path(entry).resolve()
        mapped = (
            str(copy.root / path.relative_to(copy.origin.resolve()))
            if path.is_relative_to(copy.origin.resolve())
            else entry
        )
        remapped.append(mapped)
    if remapped:
        environment["PYTHONPATH"] = os.pathsep.join(remapped)
    else:
        environment.pop("PYTHONPATH", None)
    return environment


def verify(report: Report, trial: Trial, project: Path, run_id: str) -> dict[str, Any]:
    sandbox = LocalSandbox()
    copy = sandbox.create(project)
    result: dict[str, Any] = {"commands": [], "checks": [], "passed": False}
    try:
        environment = environment_for(copy)
        result["files"] = after_images(project, run_id, copy.root)
        for index, command in enumerate(trial.acceptance):
            argv = command if os.name == "nt" else shlex.split(command)
            step = report.run(
                f"{trial.name}-accept-{index + 1}",
                argv,
                cwd=copy.root,
                timeout=900,
                environment=environment,
            )
            result["commands"].append({"command": command, "code": step.code})
            if step.code:
                break
        for check in trial.checks:
            file = safe_path(copy.root, check.file)
            matches = file.is_file() and bool(
                re.search(check.regex, file.read_text(encoding="utf-8", errors="replace"), re.S)
            )
            result["checks"].append({"file": check.file, "matches": matches})
        result["dependencies_changed"] = list(sandbox.dependencies_changed(copy))
        result["state_changed"] = list(sandbox.state_changed(copy))
        result["passed"] = (
            len(result["commands"]) == len(trial.acceptance)
            and all(command["code"] == 0 for command in result["commands"])
            and all(check["matches"] for check in result["checks"])
            and not result["dependencies_changed"]
            and not result["state_changed"]
        )
    finally:
        result["removed"] = sandbox.remove(copy)
    if not result["removed"]:
        result["passed"] = False
    return result
