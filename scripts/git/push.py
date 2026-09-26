from __future__ import annotations

import argparse
import sys

from git_workflow import WorkflowError, check_outgoing, git, require_main
from tag_release import publish_tag


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish main or its verified release tag.")
    parser.add_argument("--tag", action="store_true", help="publish the verified version tag only")
    options = parser.parse_args()
    try:
        if options.tag:
            print(publish_tag())
            return 0
        require_main()
        if "origin" not in git("remote").splitlines():
            raise WorkflowError(
                "No origin remote configured. Add one with: git remote add origin <URL>"
            )
        git("fetch", "origin")
        remote = git("ls-remote", "--heads", "origin", "refs/heads/main")
        revision = "main"
        if remote:
            remote_head = remote.split()[0]
            ahead, behind = map(
                int, git("rev-list", "--left-right", "--count", f"main...{remote_head}").split()
            )
            if ahead and behind:
                raise WorkflowError("main and origin/main have diverged. Push stopped.")
            if behind:
                print(git("merge", "--ff-only", remote_head))
            revision = f"{remote_head}..main"
        checked_head = git("rev-parse", "main")
        check_outgoing(revision)
        require_main()
        if git("rev-parse", "main") != checked_head:
            raise WorkflowError("main changed during validation. Run push.cmd again.")
        print(git("push", "origin", "main"))
        print("Push completed: origin main.")
    except (OSError, WorkflowError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
