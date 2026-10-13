from __future__ import annotations

import json

from cuanta.adapters.engines.claude_code import build_command
from cuanta.domain.engine import EngineRequest
from cuanta.domain.output_schema import RoleOutput, dataclass_schema, role_output_schema
from cuanta.domain.role_handoff import HandoffSource, HandoffStatus, build_handoff


def test_the_role_output_schema_comes_from_the_domain_dataclasses() -> None:
    schema = dataclass_schema(RoleOutput)
    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert schema["required"] == ["status", "summary"]
    assert properties["status"] == {"type": "string", "enum": ["done", "partial", "blocked"]}
    facts = properties["facts"]
    assert isinstance(facts, dict)
    item = facts["items"]
    assert isinstance(item, dict)
    assert item["required"] == ["path", "start", "end", "claim"]
    plan = properties["plan"]
    assert isinstance(plan, dict) and set(plan["properties"]) == {"edit", "read", "verify"}
    request = properties["next_request"]
    assert isinstance(request, dict)
    assert {"type", "what", "where", "why"} <= set(request["properties"])
    assert json.loads(role_output_schema()) == schema


def test_a_structured_result_becomes_the_role_handoff_and_reaches_the_command() -> None:
    text = json.dumps(
        {
            "status": "done",
            "summary": "Fixed the total",
            "facts": [{"path": "src/cart.py", "start": 3, "end": 9, "claim": "sum twice"}],
            "plan": {"edit": ["src/cart.py"], "read": [], "verify": ["pytest"]},
            "next_step": "",
        }
    )
    handoff = build_handoff(text, "senior")
    assert (handoff.status, handoff.source) == (HandoffStatus.DONE, HandoffSource.JSON)
    assert handoff.facts[0].anchor == "src/cart.py:3-9"
    command = build_command(
        ("claude",), EngineRequest("p", ".", {}, json_schema=role_output_schema())
    )
    assert json.loads(command[command.index("--json-schema") + 1])["type"] == "object"
