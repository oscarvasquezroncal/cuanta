from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from cuanta.domain.messages import Message, english, msg

LOCALHOST = "127.0.0.1"
MAIN_AGENT = "main"
REDACTED_AGENT = "custom"


def endpoint(port: int) -> str:
    return f"http://{LOCALHOST}:{port}"


def project_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


def resource_attributes(project: str, run_id: str = "") -> str:
    parts = []
    if run_id:
        parts.append(f"cuanta.run_id={run_id}")
    parts.append(f"cuanta.project={project_slug(project)}")
    return ",".join(parts)


def claude_env(port: int, project: str, run_id: str = "", traceparent: str = "") -> dict[str, str]:
    env = {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint(port),
        "OTEL_LOGS_EXPORT_INTERVAL": "1000",
        "OTEL_METRIC_EXPORT_INTERVAL": "10000",
        "OTEL_LOG_TOOL_DETAILS": "1",
        "OTEL_RESOURCE_ATTRIBUTES": resource_attributes(project, run_id),
    }
    if traceparent:
        env["TRACEPARENT"] = traceparent
    return env


def run_env(run_id: str, traceparent: str) -> dict[str, str]:
    return {"CUANTA_RUN_ID": run_id, "TRACEPARENT": traceparent}


def codex_otel_table(port: int) -> dict[str, object]:
    base = endpoint(port)
    return {
        "log_user_prompt": False,
        "exporter": {"otlp-http": {"endpoint": f"{base}/v1/logs", "protocol": "json"}},
        "trace_exporter": {"otlp-http": {"endpoint": f"{base}/v1/traces", "protocol": "json"}},
        "metrics_exporter": {"otlp-http": {"endpoint": f"{base}/v1/metrics", "protocol": "json"}},
    }


MAIN_SOURCES = frozenset({"", "main", "repl_main_thread", "sdk", "print", "user", "compact"})


@dataclass(frozen=True, slots=True)
class AgentHint:
    name: str
    certain: bool


def agent_from_signals(agent_name: str, query_source: str) -> AgentHint:
    if agent_name and agent_name != REDACTED_AGENT:
        return AgentHint(agent_name, True)
    source = query_source.strip()
    lowered = source.lower()
    for prefix in ("agent:", "subagent:", "agent_", "subagent_"):
        if lowered.startswith(prefix) and len(source) > len(prefix):
            name = source[len(prefix) :]
            return AgentHint(name, name != REDACTED_AGENT)
    if lowered in MAIN_SOURCES or lowered.startswith("repl_main"):
        return AgentHint(MAIN_AGENT, True)
    if agent_name == REDACTED_AGENT or lowered in {"agent", "subagent", "task"}:
        return AgentHint(REDACTED_AGENT, False)
    return AgentHint(source, False)


class WiringState(StrEnum):
    ON = "on"
    OFF = "off"
    OTHER = "other"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class WiringReport:
    engine: str
    state: WiringState
    message: Message = field(default_factory=lambda: msg("wiring.none"))
    path: str = ""

    @property
    def detail(self) -> str:
        return english(self.message)


@dataclass(frozen=True, slots=True)
class WiringPlan:
    engine: str
    target: str
    backup: str
    exists: bool
