from __future__ import annotations

import dataclasses
import json
import types
import typing
from dataclasses import dataclass, field
from enum import StrEnum

from cuanta.domain.mandate import MandateRequest
from cuanta.domain.role_handoff import HandoffPlan, HandoffStatus

JSON_SCHEMA_FLAG = "--json-schema"


@dataclass(frozen=True, slots=True)
class CitedFact:
    path: str
    start: int
    end: int
    claim: str


@dataclass(frozen=True, slots=True)
class RoleOutput:
    status: HandoffStatus
    summary: str
    decisions: tuple[str, ...] = ()
    facts: tuple[CitedFact, ...] = ()
    plan: HandoffPlan = field(default_factory=HandoffPlan)
    open_questions: tuple[str, ...] = ()
    next_step: str = ""
    next_request: MandateRequest = field(default_factory=MandateRequest)


def _schema(annotation: object) -> dict[str, object]:
    origin = typing.get_origin(annotation)
    if origin is tuple:
        item = typing.get_args(annotation)[0]
        return {"type": "array", "items": _schema(item)}
    if origin in {typing.Union, types.UnionType}:
        options = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        return _schema(options[0])
    if isinstance(annotation, type) and issubclass(annotation, StrEnum):
        return {"type": "string", "enum": [member.value for member in annotation]}
    if isinstance(annotation, type) and dataclasses.is_dataclass(annotation):
        return dataclass_schema(annotation)
    simple = {str: "string", int: "integer", float: "number", bool: "boolean"}
    if isinstance(annotation, type) and annotation in simple:
        return {"type": simple[annotation]}
    raise TypeError(f"no JSON schema for {annotation!r}")


def dataclass_schema(cls: type) -> dict[str, object]:
    hints = typing.get_type_hints(cls)
    fields = dataclasses.fields(cls)
    return {
        "type": "object",
        "properties": {item.name: _schema(hints[item.name]) for item in fields},
        "required": [
            item.name
            for item in fields
            if item.default is dataclasses.MISSING and item.default_factory is dataclasses.MISSING
        ],
        "additionalProperties": False,
    }


def role_output_schema() -> str:
    return json.dumps(dataclass_schema(RoleOutput), separators=(",", ":"), sort_keys=True)
