from __future__ import annotations

import os
from pathlib import Path

from git_workflow import WorkflowError, git
from tag_release import release_tag


def validate_reference(tag: str, event: str, ref_type: str, ref_name: str) -> str:
    if event not in {"push", "workflow_dispatch"}:
        raise WorkflowError("Release requires a tag push or workflow_dispatch")
    if event == "push" and (ref_type != "tag" or ref_name != tag):
        raise WorkflowError(f"Release requires tag {tag}")
    if event == "workflow_dispatch" and ref_type == "tag" and ref_name != tag:
        raise WorkflowError(f"Release requires tag {tag}")
    return tag.removeprefix("v")


def main() -> int:
    try:
        sha = os.environ.get("GITHUB_SHA") or git("rev-parse", "HEAD")
        version = validate_reference(
            release_tag(sha),
            os.environ.get("GITHUB_EVENT_NAME", "workflow_dispatch"),
            os.environ.get("GITHUB_REF_TYPE", "branch"),
            os.environ.get("GITHUB_REF_NAME", "main"),
        )
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            with Path(output).open("a", encoding="utf-8") as stream:
                stream.write(f"version={version}\n")
        else:
            print(version)
    except WorkflowError as error:
        raise SystemExit(str(error)) from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
