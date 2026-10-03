from __future__ import annotations

from dataclasses import dataclass

from cuanta.domain.errors import DomainFailure
from cuanta.domain.forge_template import (
    AGENTS_DIR,
    MANDATE_TEMPLATE,
    UNUSABLE_STATES,
    PendingTemplate,
    TemplateState,
    TemplateStatus,
    filled_template,
    missing_failure,
    template_status,
    unusable_failure,
)
from cuanta.domain.manifests import MANIFEST_ORDER, ProjectFiles, detect_stack
from cuanta.domain.messages import Message, english, msg
from cuanta.domain.progress import Note, Status, note
from cuanta.ports.forge import ForgeKit
from cuanta.ports.workspace import Workspace


@dataclass(frozen=True, slots=True)
class TemplateWrite:
    state: TemplateState
    message: Message
    written: bool = False


class MandateTemplates:
    def __init__(self, workspace: Workspace, kit: ForgeKit) -> None:
        self._workspace = workspace
        self._kit = kit

    def status(self) -> TemplateStatus:
        return self._read()[1]

    def version(self) -> str:
        return self._kit.vendored_version()

    def load(self) -> tuple[str, PendingTemplate | None]:
        text, status = self._read()
        if status.state in UNUSABLE_STATES:
            raise unusable_failure(status.state)
        if text is not None:
            return text, None
        if status.state is not TemplateState.RESTORABLE:
            raise missing_failure()
        pending = self._pending(status)
        return pending.text, pending

    def keep(self, pending: PendingTemplate) -> Note | None:
        if self.status().state is not TemplateState.RESTORABLE:
            return None
        try:
            self._workspace.write_text(MANDATE_TEMPLATE, pending.text)
        except OSError as error:
            return note(Status.WARN, pending.unwritable(str(error)))
        return note(Status.INFO, pending.written)

    def write(self, dry_run: bool = False) -> TemplateWrite:
        status = self.status()
        if status.state is TemplateState.READY:
            return TemplateWrite(status.state, msg("doctor.template.ready", path=MANDATE_TEMPLATE))
        if status.state in UNUSABLE_STATES:
            raise unusable_failure(status.state)
        if status.state is not TemplateState.RESTORABLE:
            agents = ", ".join(status.missing)
            raise DomainFailure(
                english(msg("template.needs_team", path=MANDATE_TEMPLATE, agents=agents)),
                english(msg("template.needs_team_hint")),
            )
        pending = self._pending(status)
        if dry_run:
            return TemplateWrite(status.state, pending.planned)
        try:
            self._workspace.write_text(MANDATE_TEMPLATE, pending.text)
        except OSError as error:
            raise DomainFailure(
                english(msg("template.write_failed", path=MANDATE_TEMPLATE, error=str(error))),
                english(msg("template.write_failed_hint")),
            ) from error
        return TemplateWrite(status.state, pending.written, written=True)

    def _read(self) -> tuple[str | None, TemplateStatus]:
        workspace = self._workspace
        names = workspace.list_names(AGENTS_DIR)
        without_file = template_status(None, names)
        if without_file.state is TemplateState.ABSENT and not workspace.exists(MANDATE_TEMPLATE):
            return None, without_file
        if workspace.redirected(MANDATE_TEMPLATE):
            return None, template_status(None, names, readable=False)
        text = workspace.read_text(MANDATE_TEMPLATE)
        readable = text is not None or not workspace.exists(MANDATE_TEMPLATE)
        return text, template_status(text, names, readable)

    def _pending(self, status: TemplateStatus) -> PendingTemplate:
        vendored = self._kit.mandate_template()
        return PendingTemplate(
            filled_template(vendored, self._project_name(), status.senior), self.version()
        )

    def _project_name(self) -> str:
        texts: dict[str, str] = {}
        for name in MANIFEST_ORDER:
            text = self._workspace.read_text(name)
            if text is not None:
                texts[name] = text
        files = ProjectFiles(
            texts=texts, present=frozenset(texts), directory_name=self._workspace.root.name
        )
        return detect_stack(files)[1]
