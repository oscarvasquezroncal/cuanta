from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from cuanta.domain.detection import has_forge_agents
from cuanta.domain.errors import DomainFailure
from cuanta.domain.forge_verify import SENIOR_FILE, missing_agents
from cuanta.domain.mandate import TemplateError, extract_block
from cuanta.domain.messages import Message, english, msg

MANDATE_TEMPLATE = "docs/MANDATE_TEMPLATE.md"
AGENTS_DIR = ".claude/agents"
TEMPLATE_FIX = "cuanta init --template"
PROJECT_PLACEHOLDER = "{{PROJECT_NAME}}"
SENIOR_PLACEHOLDER = "{{SENIOR_NAME}}"
UNUSABLE_HINT = "template.unusable_hint"


class TemplateState(StrEnum):
    READY = "ready"
    BROKEN = "broken"
    UNREADABLE = "unreadable"
    RESTORABLE = "restorable"
    INCOMPLETE = "incomplete"
    ABSENT = "absent"


UNUSABLE_STATES = frozenset({TemplateState.BROKEN, TemplateState.UNREADABLE})


@dataclass(frozen=True, slots=True)
class TemplateStatus:
    state: TemplateState
    senior: str = ""
    missing: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PendingTemplate:
    text: str
    version: str

    @property
    def planned(self) -> Message:
        return msg("plan.template", path=MANDATE_TEMPLATE, version=self.version)

    @property
    def written(self) -> Message:
        return msg("mandate.template_written", path=MANDATE_TEMPLATE, version=self.version)

    @property
    def borrowed(self) -> Message:
        return msg("mandate.template_borrowed", path=MANDATE_TEMPLATE, version=self.version)

    def unwritable(self, error: str) -> Message:
        return msg(
            "mandate.template_unwritable",
            path=MANDATE_TEMPLATE,
            error=error,
            version=self.version,
        )


def senior_agent(agent_files: Iterable[str]) -> str:
    seniors = sorted(name for name in agent_files if name.endswith(SENIOR_FILE))
    return seniors[0].removesuffix(".md") if seniors else ""


def template_status(
    text: str | None, agent_files: Iterable[str], readable: bool = True
) -> TemplateStatus:
    names = tuple(agent_files)
    senior = senior_agent(names)
    if not readable:
        return TemplateStatus(TemplateState.UNREADABLE, senior)
    if text is not None:
        try:
            extract_block(text)
        except TemplateError:
            return TemplateStatus(TemplateState.BROKEN, senior)
        return TemplateStatus(TemplateState.READY, senior)
    missing = missing_agents(names)
    if not missing:
        return TemplateStatus(TemplateState.RESTORABLE, senior)
    if has_forge_agents(names):
        return TemplateStatus(TemplateState.INCOMPLETE, senior, missing)
    return TemplateStatus(TemplateState.ABSENT, senior, missing)


def filled_template(text: str, project: str, senior: str) -> str:
    return text.replace(PROJECT_PLACEHOLDER, project).replace(SENIOR_PLACEHOLDER, senior)


def template_problem(state: TemplateState) -> Message:
    key = "template.unreadable" if state is TemplateState.UNREADABLE else "template.broken"
    return msg(key, path=MANDATE_TEMPLATE)


def unusable_message(state: TemplateState) -> Message:
    return msg("doctor.template.unusable", problem=template_problem(state), hint=msg(UNUSABLE_HINT))


def unusable_failure(state: TemplateState) -> DomainFailure:
    return DomainFailure(english(template_problem(state)), english(msg(UNUSABLE_HINT)))


def missing_failure() -> DomainFailure:
    return DomainFailure(f"{MANDATE_TEMPLATE} not found", "run cuanta init first")
