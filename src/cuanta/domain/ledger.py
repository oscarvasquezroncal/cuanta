from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Run:
    id: str
    kind: str
    engine: str = ""
    model: str = ""
    started_at: str = ""
    ended_at: str = ""
    status: str = "running"
    trace_id: str = ""
    hu_ref: str = ""
    scope: str = ""
    prompt_hash: str = ""
    cost_usd: float | None = 0.0
    parent_id: str = ""
    task_type: str = ""
    depth: str = ""


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    run_id: str = ""
    source: str = ""
    session_id: str = ""
    prompt_id: str = ""
    trace_id: str = ""
    agent: str = ""
    kind: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    tool_name: str = ""
    tool_use_id: str = ""
    tool_result_bytes: int = 0
    tool_input_bytes: int = 0
    duration_ms: int = 0
    success: bool | None = None
    query_source: str = ""
    file_path: str = ""
    command: str = ""
    ts: str = ""
    raw: str = ""
    effort: str = ""
    ttft_ms: int = 0
    id: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
            + self.reasoning_tokens
        )


@dataclass(frozen=True, slots=True)
class TestRunRecord:
    id: str
    run_id: str
    runner: str
    command: str
    status: str
    passed: int
    failed: int
    errored: int
    skipped: int
    duration_s: float
    capsule_id: str
    started_at: str
    exit_code: int = 0


@dataclass(frozen=True, slots=True)
class SignatureRecord:
    test_run_id: str
    signature_id: str
    error: str
    verbatim: str
    first_test: str
    location: str
    tests: int


@dataclass(frozen=True, slots=True)
class Capsule:
    id: str
    sha256: str
    path: str
    kind: str
    size_bytes: int
    lines: int
    summary: str
    created_at: str
    run_id: str = ""


@dataclass(frozen=True, slots=True)
class Decision:
    run_id: str
    backend: str
    primitive: str
    question: str
    options: str
    answer: str
    confidence: float
    latency_ms: int
    cost_usd: float = 0.0
    outcome: str = ""
    created_at: str = ""
    preview: bool = False
    request_hash: str = ""
    fallback_error: str = ""
    fallback_from: str = ""
    id: int = 0


@dataclass(frozen=True, slots=True)
class Snapshot:
    run_id: str
    phase: str
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class Baseline:
    run_id: str
    kind: str
    name: str
    path: str
    bytes: int
    tokens: int
    created_at: str = ""


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    run_id: str
    task_type: str
    role: str
    tier: str
    engine: str
    model: str
    reason: str
    confidence: float | None = None
    backend: str = ""
    outcome: str = ""
    cost_usd: float | None = None
    retries: int = 0
    accepted: int | None = None
    created_at: str = ""
    id: int = 0


@dataclass(frozen=True, slots=True)
class RouteAudit:
    run_id: str
    agent: str
    role: str
    planned: str
    actual: str
    status: str
    cause: str
    created_at: str = ""
