from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SizeTier(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    XL = "xl"


class DocsState(StrEnum):
    EXISTS = "exists"
    PARTIAL = "partial"
    ABSENT = "absent"


class ForgeState(StrEnum):
    FRESH = "fresh"
    INITIALIZED = "initialized"


class VerifyTier(StrEnum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"


class GraphMode(StrEnum):
    CLI = "cli"
    MCP = "mcp"
    BROKEN = "broken"
    NONE = "none"


EXCLUDED_DIRS = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        "vendor",
        "dist",
        "build",
        ".next",
        "out",
        "target",
        "__pycache__",
        ".git",
    }
)
EXCLUDED_SUFFIX_DIRS = (".egg-info",)
EXCLUDED_FILES = frozenset({"package-lock.json", "poetry.lock", "go.sum"})
EXCLUDED_FILE_SUFFIXES = (".lock", ".min.js", ".min.css", ".bundle.js", ".min.mjs")

SOURCE_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".mts",
        ".cts",
        ".vue",
        ".svelte",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".kts",
        ".scala",
        ".groovy",
        ".rb",
        ".php",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".hpp",
        ".cs",
        ".fs",
        ".swift",
        ".m",
        ".mm",
        ".dart",
        ".ex",
        ".exs",
        ".erl",
        ".clj",
        ".lua",
        ".r",
        ".jl",
        ".sh",
        ".ps1",
        ".sql",
        ".zig",
        ".nim",
        ".hs",
        ".ml",
    }
)


def is_excluded_dir(name: str, extra: frozenset[str] = frozenset()) -> bool:
    return name in EXCLUDED_DIRS or name in extra or name.endswith(EXCLUDED_SUFFIX_DIRS)


def is_source_file(name: str) -> bool:
    lowered = name.lower()
    if lowered in EXCLUDED_FILES or lowered.endswith(EXCLUDED_FILE_SUFFIXES):
        return False
    dot = lowered.rfind(".")
    return dot > 0 and lowered[dot:] in SOURCE_SUFFIXES


def size_tier(file_count: int) -> SizeTier:
    if file_count < 100:
        return SizeTier.SMALL
    if file_count < 500:
        return SizeTier.MEDIUM
    if file_count < 2000:
        return SizeTier.LARGE
    return SizeTier.XL


def docs_state(root_claude_md: bool, nested_claude_md: int) -> DocsState:
    if not root_claude_md:
        return DocsState.ABSENT
    return DocsState.EXISTS if nested_claude_md > 0 else DocsState.PARTIAL


FORGE_AGENT_NAMES = frozenset({"architecture-analyst.md", "docs-updater.md", "tester.md"})
FORGE_ARTIFACTS = ("AGENTS_GUIDE.md", "docs/MANDATE_TEMPLATE.md")


def has_forge_agents(agent_files: tuple[str, ...]) -> bool:
    return any(name in FORGE_AGENT_NAMES or name.endswith("-senior.md") for name in agent_files)


def forge_state(
    agent_files: tuple[str, ...],
    artifacts_present: bool,
    ground_truth_present: bool,
    completed_state_file: bool,
) -> ForgeState:
    if (
        has_forge_agents(agent_files)
        or artifacts_present
        or ground_truth_present
        or completed_state_file
    ):
        return ForgeState.INITIALIZED
    return ForgeState.FRESH


@dataclass(frozen=True, slots=True)
class VerifySignals:
    test_runners: tuple[str, ...] = ()
    typecheckers: tuple[str, ...] = ()
    linters: tuple[str, ...] = ()
    builders: tuple[str, ...] = ()
    placeholder_tests: tuple[str, ...] = ()


NO_TYPECHECK = "no typecheck"


@dataclass(frozen=True, slots=True)
class VerifyDecision:
    tier: VerifyTier
    evidence: str


def verify_tier(signals: VerifySignals) -> VerifyDecision:
    runners = signals.test_runners
    checks = signals.typecheckers + signals.builders
    if runners and checks:
        return VerifyDecision(VerifyTier.STRONG, " + ".join((*runners, *checks)))
    if runners:
        return VerifyDecision(
            VerifyTier.STRONG, " + ".join(runners + signals.linters) + ", " + NO_TYPECHECK
        )
    soft = signals.typecheckers + signals.linters
    if soft:
        parts = soft + signals.builders
        note = ", no test runner"
        if signals.placeholder_tests:
            note = f", test script is a placeholder ({signals.placeholder_tests[0]})"
        return VerifyDecision(VerifyTier.MODERATE, " + ".join(parts) + note)
    if signals.builders:
        return VerifyDecision(VerifyTier.WEAK, " + ".join(signals.builders) + " (build only)")
    if signals.placeholder_tests:
        return VerifyDecision(
            VerifyTier.WEAK, f"test script is a placeholder ({signals.placeholder_tests[0]})"
        )
    return VerifyDecision(VerifyTier.WEAK, "nothing verifiable detected")


@dataclass(frozen=True, slots=True)
class Stack:
    language: str = "unknown"
    language_version: str = ""
    framework: str = ""
    framework_version: str = ""
    package_manager: str = ""
    test_runner: str = ""
    entry_points: tuple[str, ...] = ()
    manifests: tuple[str, ...] = ()
    verified: bool = True
    signals: VerifySignals = field(default_factory=VerifySignals)
    test_command: str = ""
    typecheck_command: str = ""
    build_command: str = ""


@dataclass(frozen=True, slots=True)
class EngineInfo:
    name: str
    path: str
    version: str


@dataclass(frozen=True, slots=True)
class Detection:
    root: str
    project_name: str
    stack: Stack
    file_count: int
    size_tier: SizeTier
    docs_state: DocsState
    forge_state: ForgeState
    verify: VerifyDecision
    graph_mode: GraphMode
    graph_evidence: str
    vcs: bool
    engines: tuple[EngineInfo, ...]
    run_note: str
    telemetry_note: str = "not wired"

    @property
    def verify_tier(self) -> VerifyTier:
        return self.verify.tier

    def engine(self, name: str) -> EngineInfo | None:
        for engine in self.engines:
            if engine.name == name:
                return engine
        return None


def stack_line(stack: Stack) -> str:
    language = f"{stack.language} {stack.language_version}".strip()
    framework = f"{stack.framework} {stack.framework_version}".strip() or "no framework"
    tools = ", ".join(part for part in (stack.package_manager, stack.test_runner) if part)
    line = f"{language} / {framework}"
    if tools:
        line = f"{line} ({tools})"
    return line if stack.verified else f"{line} [UNVERIFIED]"


def summary_lines(detection: Detection, unicode: bool = True) -> tuple[tuple[str, str], ...]:
    arrow = "→" if unicode else "->"
    dash = "—" if unicode else "-"
    forge = f"FORGE_STATE={detection.forge_state}"
    if detection.forge_state is ForgeState.INITIALIZED:
        forge = f"{forge} {dash} running refresh semantics"
    engines = " · " if unicode else ", "
    engine_text = engines.join(
        f"{engine.name} {engine.version or 'present'}" for engine in detection.engines
    )
    return (
        ("stack:", stack_line(detection.stack)),
        ("files:", f"{detection.file_count} source files {arrow} SIZE_TIER={detection.size_tier}"),
        ("docs:", f"DOCS_STATE={detection.docs_state}"),
        ("forge:", forge),
        ("graph:", f"GRAPH_MODE={detection.graph_mode}"),
        ("verify:", f"VERIFY_TIER={detection.verify_tier} {dash} {detection.verify.evidence}"),
        ("vcs:", "git working tree" if detection.vcs else "no vcs"),
        ("entry:", ", ".join(detection.stack.entry_points) or "none found"),
        ("run:", detection.run_note),
        ("engines:", engine_text or "none found"),
        ("telemetry:", detection.telemetry_note),
    )
