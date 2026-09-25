from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from cuanta.domain.graph_policy import GRAPH_REFERENCE, graphless_prompt
from cuanta.domain.routing import Role
from cuanta.domain.stable import stable_json

FENCE = "---"
LIST_FIELDS = frozenset({"tools", "disallowedTools", "skills"})
NUMBER_FIELDS = frozenset({"maxTurns"})
BOOL_FIELDS = frozenset({"background", "omitClaudeMd"})
PASSTHROUGH = (
    "description",
    "tools",
    "disallowedTools",
    "model",
    "effort",
    "permissionMode",
    "mcpServers",
    "hooks",
    "maxTurns",
    "skills",
    "initialPrompt",
    "memory",
    "background",
    "omitClaudeMd",
    "isolation",
)
ROLE_NAMES: Mapping[str, Role] = {
    "architecture-analyst": Role.ANALYST,
    "tester": Role.TESTER,
    "docs-updater": Role.DOCS,
}
SENIOR_SUFFIX = "-senior"


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    name: str
    fields: Mapping[str, object]
    prompt: str
    path: str = ""

    @property
    def model(self) -> str:
        value = self.fields.get("model")
        return value if isinstance(value, str) else ""


@dataclass(frozen=True, slots=True)
class AgentRoute:
    model: str
    effort: str = ""


@dataclass(frozen=True, slots=True)
class AgentsPlan:
    agents: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    roles: Mapping[str, Role] = field(default_factory=dict)

    def to_json(self) -> str:
        return stable_json(self.agents)


def _scalar(value: str) -> object:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [str(_scalar(part)) for part in inner.split(",") if part.strip()] if inner else []
    return text


def _typed(key: str, value: object) -> object:
    if key in LIST_FIELDS and isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if key in NUMBER_FIELDS and isinstance(value, str) and value.strip().isdigit():
        return int(value)
    if key in BOOL_FIELDS and isinstance(value, str):
        return value.strip().lower() == "true"
    return value


def parse_agent(text: str, path: str = "") -> AgentDefinition | None:
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != FENCE:
        return None
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == FENCE)
    except StopIteration:
        return None
    fields: dict[str, object] = {}
    current = ""
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = re.match(r"^\s+-\s+(.*)$", line)
        if item and current:
            existing = fields.get(current)
            values = existing if isinstance(existing, list) else []
            values.append(str(_scalar(item.group(1))))
            fields[current] = values
            continue
        match = re.match(r"^([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(.*)$", line)
        if not match:
            continue
        current = match.group(1)
        raw = match.group(2)
        fields[current] = _typed(current, _scalar(raw)) if raw.strip() else []
    name = fields.pop("name", "")
    if not isinstance(name, str) or not name:
        return None
    prompt = "\n".join(lines[end + 1 :]).strip("\n")
    return AgentDefinition(name, fields, prompt, path)


def role_of(name: str) -> Role | None:
    if name in ROLE_NAMES:
        return ROLE_NAMES[name]
    return Role.SENIOR if name.endswith(SENIOR_SUFFIX) else None


def build_agents(
    definitions: Sequence[AgentDefinition],
    routes: Mapping[Role, AgentRoute],
    graph_available: bool = True,
) -> AgentsPlan:
    agents: dict[str, dict[str, object]] = {}
    roles: dict[str, Role] = {}
    for definition in definitions:
        role = role_of(definition.name)
        if role is None or role not in routes:
            continue
        spec: dict[str, object] = {
            key: definition.fields[key] for key in PASSTHROUGH if key in definition.fields
        }
        spec["description"] = str(definition.fields.get("description") or definition.name)
        if not graph_available:
            description = str(spec["description"])
            if GRAPH_REFERENCE.search(description) is not None:
                spec["description"] = definition.name
            for key in ("tools", "skills"):
                value = spec.get(key)
                if isinstance(value, list):
                    spec[key] = [
                        item for item in value if GRAPH_REFERENCE.search(str(item)) is None
                    ]
        spec["prompt"] = (
            definition.prompt if graph_available else graphless_prompt(definition.prompt)
        )
        initial = spec.get("initialPrompt")
        if not graph_available and isinstance(initial, str):
            spec["initialPrompt"] = graphless_prompt(initial)
        route = routes[role]
        spec["model"] = route.model
        if route.effort:
            spec["effort"] = route.effort
        agents[definition.name] = spec
        roles[definition.name] = role
    return AgentsPlan(agents, roles)
