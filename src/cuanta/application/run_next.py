from __future__ import annotations

from dataclasses import dataclass

from cuanta.application.run_reports import RunReports
from cuanta.domain.errors import DomainFailure
from cuanta.domain.mandate import MandateRequest, render_mandate
from cuanta.domain.messages import Message, english, msg
from cuanta.domain.report import next_step
from cuanta.ports.workspace import Workspace

NEXT_NAME = "next.md"


@dataclass(frozen=True, slots=True)
class NextMandate:
    run_id: str
    request: MandateRequest
    path: str
    notes: tuple[Message, ...] = ()

    @property
    def command(self) -> str:
        return f"cuanta run {self.path}"


def write_next(workspace: Workspace, run_id: str) -> NextMandate:
    reports = RunReports(workspace)
    text = reports.report(run_id)
    if text is None:
        raise DomainFailure(
            english(msg("runs_next.no_report", run=run_id)), english(msg("runs_next.hint"))
        )
    step = next_step(text)
    if step is None:
        raise DomainFailure(
            english(msg("runs_next.none", run=run_id)), english(msg("runs_next.hint"))
        )
    path = f"{reports.folder(run_id)}/{NEXT_NAME}"
    workspace.write_text(path, render_mandate(step.request))
    return NextMandate(run_id, step.request, path, step.notes)
