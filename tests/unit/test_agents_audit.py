from __future__ import annotations

import json

from cuanta.domain.agents import AgentRoute, build_agents, parse_agent, role_of
from cuanta.domain.audit import AuditStatus, audit, normalized, same_model, summary
from cuanta.domain.messages import english
from cuanta.domain.routing import Role

SENIOR = """---
name: python-senior
description: "Senior engineer: implements one phase."
tools: Read, Write, Edit
disallowedTools:
  - Bash(git *)
model: opus
maxTurns: 40
background: false
color: blue
---

You are the senior engineer.
Keep every line.
"""
TESTER = """---
name: tester
description: Test engineer
tools: [Read, Bash]
model: sonnet
---
Run the tests.
"""
STRAY = """---
name: helper
description: Not a Forge role
---
Help.
"""


def test_parse_agent_reads_scalars_lists_and_body() -> None:
    agent = parse_agent(SENIOR)
    assert agent is not None
    assert agent.name == "python-senior"
    assert agent.fields["description"] == "Senior engineer: implements one phase."
    assert agent.fields["tools"] == ["Read", "Write", "Edit"]
    assert agent.fields["disallowedTools"] == ["Bash(git *)"]
    assert agent.fields["maxTurns"] == 40
    assert agent.fields["background"] is False
    assert agent.prompt == "You are the senior engineer.\nKeep every line."
    assert parse_agent("no frontmatter") is None
    assert parse_agent("---\ndescription: x\n---\nbody") is None


def test_roles_follow_forge_names() -> None:
    assert role_of("architecture-analyst") is Role.ANALYST
    assert role_of("go-senior") is Role.SENIOR
    assert role_of("tester") is Role.TESTER
    assert role_of("docs-updater") is Role.DOCS
    assert role_of("helper") is None


def test_build_agents_overrides_only_model_and_effort() -> None:
    definitions = [parse_agent(text) for text in (SENIOR, TESTER, STRAY)]
    plan = build_agents(
        [item for item in definitions if item is not None],
        {
            Role.SENIOR: AgentRoute("claude-opus-5-5", "high"),
            Role.TESTER: AgentRoute("claude-sonnet-5"),
        },
    )
    assert set(plan.agents) == {"python-senior", "tester"}
    senior = plan.agents["python-senior"]
    assert senior["model"] == "claude-opus-5-5"
    assert senior["effort"] == "high"
    assert senior["tools"] == ["Read", "Write", "Edit"]
    assert senior["disallowedTools"] == ["Bash(git *)"]
    assert senior["maxTurns"] == 40
    assert "color" not in senior
    assert "name" not in senior
    assert str(senior["prompt"]).startswith("You are the senior engineer.")
    tester = plan.agents["tester"]
    assert tester["model"] == "claude-sonnet-5"
    assert "effort" not in tester
    assert plan.roles == {"python-senior": Role.SENIOR, "tester": Role.TESTER}
    assert json.loads(plan.to_json())["tester"]["prompt"] == "Run the tests."


def test_model_ids_compare_across_dates_and_variants() -> None:
    assert normalized("claude-opus-5-5-20260901") == "claude-opus-5-5"
    assert normalized("anthropic/claude-sonnet-5[1m]") == "claude-sonnet-5"
    assert same_model("claude-opus-5-5", "claude-opus-5-5-20260901")
    assert not same_model("claude-opus-5-5", "claude-opus-5")


def test_audit_reports_matches_and_likely_causes() -> None:
    planned = {
        "python-senior": ("senior", "claude-opus-5-5"),
        "tester": ("tester", "claude-sonnet-5"),
        "architecture-analyst": ("analyst", "claude-sonnet-5"),
        "docs-updater": ("docs", "claude-haiku-4-5"),
        "main": ("orchestrator", "claude-sonnet-5"),
    }
    observed = {
        "python-senior": ("claude-opus-5-5-20260901",),
        "tester": ("claude-sonnet-4-6",),
        "architecture-analyst": ("claude-haiku-4-5",),
        "main": ("claude-sonnet-5",),
        "docs": ("claude-haiku-4-5",),
    }
    rows = {
        row.agent: row for row in audit(planned, observed, "", {"architecture-analyst": "haiku"})
    }
    assert rows["python-senior"].status is AuditStatus.MATCH
    assert rows["main"].ok
    assert rows["tester"].status is AuditStatus.MISMATCH
    assert "same family, other version" in english(rows["tester"].cause)
    assert "Claude passed model haiku" in english(rows["architecture-analyst"].cause)
    assert rows["docs-updater"].status is AuditStatus.NOT_SEEN
    assert "telemetry saw docs" in english(rows["docs-updater"].cause)
    forced = audit(
        {"tester": ("tester", "claude-opus-5-5")}, {"tester": ("claude-haiku-4-5",)}, "X"
    )
    assert "X is set" in english(forced[0].cause)
    unseen = audit({"tester": ("tester", "claude-opus-5-5")}, {})
    assert "no telemetry" in english(unseen[0].cause)


def test_agents_that_were_never_delegated_are_neutral() -> None:
    planned = {
        "tester": ("tester", "claude-opus-5-5"),
        "python-senior": ("senior", "claude-opus-5-5"),
        "main": ("orchestrator", "claude-sonnet-5"),
    }
    observed = {"tester": ("claude-opus-5-5",), "main": ("claude-sonnet-5",)}
    rows = {row.agent: row for row in audit(planned, observed, delegated=frozenset({"tester"}))}
    assert rows["python-senior"].status is AuditStatus.NOT_RUN
    assert "not delegated" in english(rows["python-senior"].cause)
    assert rows["tester"].ok and rows["main"].ok
    assert english(summary(tuple(rows.values()))) == "2/2 agents ran on their planned model"
