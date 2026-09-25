from __future__ import annotations

import argparse
import sys

from git_workflow import (
    CONVENTIONAL,
    WorkflowError,
    clean_message,
    git,
    is_ai_marker,
    require_hook,
    require_main,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage and commit on main without AI attribution.")
    parser.add_argument("-m", "--message", required=True)
    parser.add_argument("paths", nargs="*", help="Paths to stage; omitted means all changes.")
    args = parser.parse_args()
    try:
        require_main()
        require_hook()
        subject = clean_message(args.message).splitlines()[0]
        if not CONVENTIONAL.fullmatch(subject):
            raise WorkflowError("Use a conventional message, for example feat(git): add workflow.")
        before = git("rev-parse", "--verify", "HEAD", check=False)
        if args.paths:
            git("add", "--", *args.paths)
        else:
            git("add", "--all")
        archives = [
            path for path in git("ls-files", "-z").split("\0") if path.lower().endswith(".zip")
        ]
        if archives:
            raise WorkflowError("ZIP archives cannot be committed: " + ", ".join(archives))
        print(git("commit", "--file=-", input_text=args.message))
        created = git("rev-parse", "HEAD")
        if created == before:
            raise WorkflowError("No new commit was created; nothing will be amended.")
        message = git("show", "-s", "--format=%B", created)
        if any(is_ai_marker(line) for line in message.splitlines()):
            if git("for-each-ref", f"--contains={created}", "refs/remotes"):
                raise WorkflowError("The new commit is already in a remote ref; amend refused.")
            if git("rev-parse", "HEAD") != created:
                raise WorkflowError("HEAD changed; amend refused.")
            git(
                "commit",
                "--amend",
                "--only",
                "--no-verify",
                "--file=-",
                input_text=clean_message(message),
            )
            message = git("show", "-s", "--format=%B", "HEAD")
            if any(is_ai_marker(line) for line in message.splitlines()):
                raise WorkflowError("AI attribution remains in the local commit. Do not push.")
        print(git("log", "-1", "--format=fuller"))
    except (OSError, WorkflowError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
