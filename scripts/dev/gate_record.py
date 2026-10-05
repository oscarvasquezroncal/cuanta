from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

HASH = re.compile(r"[0-9a-f]{40}\Z")


def git(root: Path, *args: str, environment: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    ).stdout.strip()


def worktree(root: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="cuanta-gate-index-") as folder:
        environment = {**os.environ, "GIT_INDEX_FILE": str(Path(folder) / "index")}
        git(root, "read-tree", "HEAD", environment=environment)
        git(root, "add", "--all", "--", ".", environment=environment)
        return git(root, "write-tree", environment=environment)


def validate(value: object, tree: str) -> dict[str, object]:
    if not isinstance(value, dict) or value.get("tree") != tree or value.get("status") != "green":
        raise ValueError("Local gate record is missing, failed or belongs to another tree")
    head, finished, counts = value.get("head"), value.get("finished_at"), value.get("counts")
    if not HASH.fullmatch(tree) or not isinstance(head, str) or not HASH.fullmatch(head):
        raise ValueError("Local gate record has invalid hashes")
    if not isinstance(finished, str) or not isinstance(counts, dict):
        raise ValueError("Local gate record is incomplete")
    try:
        timestamp = datetime.fromisoformat(finished)
    except ValueError as error:
        raise ValueError("Local gate record has an invalid completion time") from error
    if timestamp.tzinfo is None:
        raise ValueError("Local gate record needs a timezone")
    for key, minimum in (("static", 3), ("functional", 1), ("performance", 1)):
        number = counts.get(key)
        if isinstance(number, bool) or not isinstance(number, int) or number < minimum:
            raise ValueError("Local gate record does not prove all required phases")
    return value


def write_green(root: Path, tree: str, head: str, counts: Mapping[str, object]) -> Path:
    record = validate(
        {
            "tree": tree,
            "head": head,
            "status": "green",
            "finished_at": datetime.now(UTC).isoformat(),
            "counts": dict(counts),
        },
        tree,
    )
    folder = root / ".cuanta" / "gates"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{tree}.json"
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=folder, delete=False
    ) as stream:
        temporary = Path(stream.name)
        json.dump(record, stream, indent=2)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def require_green(root: Path, tree: str) -> dict[str, object]:
    if not HASH.fullmatch(tree):
        raise ValueError("Local gate tree hash is invalid")
    try:
        record = json.loads((root / ".cuanta/gates" / f"{tree}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("A complete green local gate is required for this exact tree") from error
    return validate(record, tree)
