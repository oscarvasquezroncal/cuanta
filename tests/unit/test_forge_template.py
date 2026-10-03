from __future__ import annotations

from pathlib import Path

from cuanta.domain.fixes import FixAction, FixKind, classify
from cuanta.domain.forge_template import (
    MANDATE_TEMPLATE,
    TEMPLATE_FIX,
    UNUSABLE_STATES,
    PendingTemplate,
    TemplateState,
    TemplateStatus,
    filled_template,
    template_status,
    unusable_failure,
    unusable_message,
)
from cuanta.domain.forge_verify import missing_agents
from cuanta.domain.mandate import extract_block
from cuanta.domain.messages import english

VENDORED = (
    Path(__file__).parents[2]
    / "src/cuanta/assets/forge/skills/agent-system-init/templates/MANDATE_TEMPLATE.template.md"
)
TEAM = ("architecture-analyst.md", "backend-senior.md", "docs-updater.md", "tester.md")
USABLE = "# Mandate\n\n```\n# MANDATE\n\n=== REQUEST ===\n\nTYPE: x\n```\n"


def test_the_real_run_team_without_a_template_can_have_it_restored() -> None:
    status = template_status(None, TEAM)
    assert status == TemplateStatus(TemplateState.RESTORABLE, "backend-senior")
    assert status.state not in UNUSABLE_STATES


def test_a_present_template_is_ready_broken_or_unreadable_whatever_the_team() -> None:
    assert template_status(USABLE, TEAM).state is TemplateState.READY
    assert template_status(USABLE, ()).state is TemplateState.READY
    assert template_status("# no request block\n", TEAM).state is TemplateState.BROKEN
    assert template_status("", TEAM).state is TemplateState.BROKEN
    assert template_status("", ()).state is TemplateState.BROKEN
    assert template_status(None, TEAM, readable=False).state is TemplateState.UNREADABLE
    assert template_status(None, (), readable=False).state is TemplateState.UNREADABLE
    assert template_status(USABLE, TEAM, readable=False).state is TemplateState.UNREADABLE
    assert {TemplateState.BROKEN, TemplateState.UNREADABLE} == UNUSABLE_STATES


def test_doctor_and_the_run_share_one_message_for_an_unusable_template() -> None:
    hint = "fix it, or delete it and run cuanta init --template"
    broken = "docs/MANDATE_TEMPLATE.md has no fenced === REQUEST === block"
    unreadable = "docs/MANDATE_TEMPLATE.md cannot be read as a plain file inside the project"
    for state, problem in ((TemplateState.BROKEN, broken), (TemplateState.UNREADABLE, unreadable)):
        assert english(unusable_message(state)) == f"{problem}: {hint}"
        failure = unusable_failure(state)
        assert (failure.message, failure.hint) == (problem, hint)


def test_a_pending_template_names_what_happens_to_it() -> None:
    pending = PendingTemplate("text", "0.4.0 (8b8a490)")
    assert english(pending.planned) == (
        "write: docs/MANDATE_TEMPLATE.md from the vendored Forge 0.4.0 (8b8a490)"
    )
    assert english(pending.written) == (
        "docs/MANDATE_TEMPLATE.md was missing: wrote it from the vendored Forge 0.4.0 (8b8a490)"
    )
    assert english(pending.borrowed) == (
        "docs/MANDATE_TEMPLATE.md is missing: this run uses the vendored Forge 0.4.0 (8b8a490) "
        "copy; cuanta init --template keeps it"
    )


def test_an_incomplete_team_names_what_is_missing_and_no_team_is_absent() -> None:
    partial = template_status(None, ("architecture-analyst.md", "python-senior.md"))
    assert partial.state is TemplateState.INCOMPLETE
    assert partial.missing == ("tester.md", "docs-updater.md")
    seniorless = template_status(None, ("architecture-analyst.md", "tester.md", "docs-updater.md"))
    assert seniorless.state is TemplateState.INCOMPLETE
    assert seniorless.missing == ("<lang>-senior.md",)
    absent = template_status(None, ("README.md", "notes.md"))
    assert absent.state is TemplateState.ABSENT
    assert template_status(None, ()).state is TemplateState.ABSENT


def test_suggested_copies_beside_the_agents_never_count_as_the_team() -> None:
    suggested = (
        "architecture-analyst.md",
        "tester.md",
        "docs-updater.md",
        "backend-senior.new.md",
        "tester.new.md",
    )
    status = template_status(None, suggested)
    assert status.state is TemplateState.INCOMPLETE
    assert status.missing == ("<lang>-senior.md",)
    assert missing_agents(suggested) == ("<lang>-senior.md",)
    assert missing_agents(TEAM) == ()


def test_the_senior_is_the_first_senior_agent_by_name() -> None:
    team = (*TEAM, "frontend-senior.md")
    assert template_status(None, team).senior == "backend-senior"
    assert template_status(None, reversed(team)).senior == "backend-senior"


def test_the_vendored_template_fills_into_a_usable_mandate() -> None:
    vendored = VENDORED.read_text(encoding="utf-8")
    assert "{{PROJECT_NAME}}" in vendored and "{{SENIOR_NAME}}" in vendored
    filled = filled_template(vendored, "shop-api", "backend-senior")
    assert "{{" not in filled
    assert filled.startswith("# Mandate template — shop-api\n")
    block = extract_block(filled)
    assert "# MANDATE — shop-api" in block
    assert "architecture-analyst → backend-senior → tester" in block
    assert "2. **backend-senior** — implements ONE phase per invocation" in block
    assert filled.replace("shop-api", "{{PROJECT_NAME}}").replace(
        "backend-senior", "{{SENIOR_NAME}}"
    ) == vendored.replace("\r\n", "\n")


def test_the_template_fix_runs_in_place_and_never_opens_init() -> None:
    assert MANDATE_TEMPLATE == "docs/MANDATE_TEMPLATE.md"
    fix = classify(TEMPLATE_FIX)
    assert (fix.kind, fix.action, fix.command) == (FixKind.RUN, FixAction.TEMPLATE, TEMPLATE_FIX)
    assert classify("cuanta init").action is FixAction.INIT
    assert classify("cuanta init --skip-forge").action is FixAction.INIT
