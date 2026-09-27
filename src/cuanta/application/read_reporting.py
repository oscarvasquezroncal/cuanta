from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from cuanta.application.run_reports import RunReports
from cuanta.application.trials import TrialStore
from cuanta.domain.ledger import Run
from cuanta.domain.sandbox import SANDBOX_MODE
from cuanta.ports.ledger import Ledger
from cuanta.ports.workspace import Workspace


class ReadReporting:
    def __init__(self, workspace: Workspace, ledger: Ledger, current_root: str = "") -> None:
        self._ledger = ledger
        self._roots = tuple(
            dict.fromkeys(root for root in (str(workspace.root), current_root) if root)
        )
        self._reports = RunReports(workspace)
        self._trials = TrialStore(workspace, ledger, lambda: "")

    def report(self, run_id: str) -> str | None:
        return self._reports.report(run_id)

    def roots(self, run: Run) -> tuple[str, ...]:
        roots = list(self._roots)
        parent = self._ledger.get_run(run.parent_id) if run.parent_id else None
        for owner in (run, parent):
            if owner is None or owner.mode != SANDBOX_MODE:
                continue
            trial = self._trials.load(owner.id)
            if trial is None or trial.run_id != owner.id:
                continue
            copy = trial.copy_root.replace("\\", "/")
            if (
                (PurePosixPath(copy).is_absolute() or PureWindowsPath(copy).is_absolute())
                and ".." not in PurePosixPath(copy).parts
                and "\0" not in copy
            ):
                roots.append(copy)
        return tuple(dict.fromkeys(roots))
