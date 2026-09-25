from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from cuanta.domain.forge_state import FORGE_PHASES
from cuanta.domain.messages import Message, english, msg
from cuanta.domain.progress import Status

ROOT_CEILING = 300
DIRECTORY_CAP = 40
OVER_CEILING = "Over ceiling"
OVER_CAP = "Over cap"
NO_GRAPH_LINE = "No code graph, deliberately"
AGENT_FILES = ("architecture-analyst.md", "tester.md", "docs-updater.md")
SUPPORT_ARTIFACTS = (
    "docs/CHANGELOG_INTERNAL.md",
    "docs/FLAGS.md",
    "docs/MANDATE_TEMPLATE.md",
    "AGENTS_GUIDE.md",
)
HARNESS_ARTIFACTS = ("docs/RUN_LOG.md", "docs/IMPROVEMENTS.md", "HISTORIAS.md", "docs/LOOP.md")
FORGE_DOCS = (
    "docs/CHANGELOG_INTERNAL.md",
    "docs/FLAGS.md",
    "docs/MANDATE_TEMPLATE.md",
    "docs/RUN_LOG.md",
    "docs/IMPROVEMENTS.md",
    "docs/LOOP.md",
)


GATEWAY_FILES = ("docs/MANDATE_TEMPLATE.md", ".claude/agents/tester.md")
GATEWAY_RUN = "cuanta test --json"
GATEWAY_READ = re.compile(r"cuanta cat\s+\S+\s+--level\s+L2")
PIPED_GATEWAY = re.compile(r"cuanta test[^\n|]*\|\s*(tail|head)\b")


ROOT_MISSING = "verify.root_missing"
GAP_MISSING = "missing"
GAP_RUN = "run"
GAP_READ = "read"
GAP_PIPED = "piped"
GAP_COMMANDS = {GAP_RUN: GATEWAY_RUN, GAP_READ: "cuanta cat <capsule> --level L2"}


@dataclass(frozen=True, slots=True)
class Finding:
    status: Status
    message: Message

    @property
    def text(self) -> str:
        return english(self.message)

    @property
    def missing(self) -> bool:
        return self.message.key == ROOT_MISSING


def finding(status: Status, key: str, **params: object) -> Finding:
    return Finding(status, msg(key, **params))


@dataclass(frozen=True, slots=True)
class ForgeTree:
    texts: Mapping[str, str]
    agent_names: tuple[str, ...]
    docs_names: tuple[str, ...]
    per_directory: Mapping[str, str]
    graph_wired: bool
    phases_completed: tuple[str, ...]
    new_files: tuple[str, ...]
    gateway: bool = False
    gateway_texts: Mapping[str, str] = field(default_factory=dict)


def line_count(text: str) -> int:
    return len(text.splitlines())


def placeholders(texts: Mapping[str, str]) -> tuple[str, ...]:
    hits: list[str] = []
    for path, text in sorted(texts.items()):
        if "{{" in text:
            start = text.index("{{")
            end = text.find("}}", start)
            token = text[start : end + 2] if end != -1 else text[start : start + 30]
            hits.append(f"{path}:{token}")
    return tuple(hits)


def check_ceilings(root: str | None, per_directory: Mapping[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    if root is None:
        findings.append(finding(Status.FAIL, ROOT_MISSING))
    else:
        lines = line_count(root)
        if lines <= ROOT_CEILING:
            findings.append(finding(Status.OK, "verify.root_ok", lines=lines, cap=ROOT_CEILING))
        elif OVER_CEILING in root:
            findings.append(
                finding(Status.WARN, "verify.root_flagged", lines=lines, cap=ROOT_CEILING)
            )
        else:
            findings.append(finding(Status.FAIL, "verify.root_over", lines=lines, cap=ROOT_CEILING))
    for path, text in sorted(per_directory.items()):
        lines = line_count(text)
        if lines > DIRECTORY_CAP:
            status = Status.WARN if OVER_CAP in text else Status.FAIL
            findings.append(
                finding(status, "verify.directory_over", path=path, lines=lines, cap=DIRECTORY_CAP)
            )
    return findings


def _listed(status_ok: bool, missing: list[str], bad: str, good: str) -> Finding:
    if missing:
        return finding(Status.FAIL, bad, items=", ".join(missing))
    return finding(Status.OK if status_ok else Status.FAIL, good)


def check_artifacts(tree: ForgeTree) -> list[Finding]:
    findings: list[Finding] = []
    root = tree.texts.get("CLAUDE.md")
    graph_ok = tree.graph_wired or (root is not None and NO_GRAPH_LINE in root)
    findings.append(finding(Status.OK if graph_ok else Status.FAIL, "verify.graph"))
    senior = [name for name in tree.agent_names if name.endswith("-senior.md")]
    missing_agents = [name for name in AGENT_FILES if name not in tree.agent_names]
    if not senior:
        missing_agents.append("<lang>-senior.md")
    findings.append(_listed(True, missing_agents, "verify.agents_missing", "verify.agents_ok"))
    ground_truth = any(
        name.startswith("GROUND_TRUTH") and name.endswith(".md") for name in tree.docs_names
    )
    missing_support = [path for path in SUPPORT_ARTIFACTS if path not in tree.texts]
    if not ground_truth:
        missing_support.append("docs/GROUND_TRUTH*.md")
    findings.append(_listed(True, missing_support, "verify.support_missing", "verify.support_ok"))
    missing_harness = [path for path in HARNESS_ARTIFACTS if path not in tree.texts]
    guide = tree.texts.get("AGENTS_GUIDE.md", "")
    if guide and "harness" not in guide.lower():
        missing_harness.append("AGENTS_GUIDE.md §Harness")
    findings.append(_listed(True, missing_harness, "verify.harness_missing", "verify.harness_ok"))
    incomplete = [phase for phase in FORGE_PHASES if phase not in tree.phases_completed]
    if incomplete:
        findings.append(
            finding(Status.WARN, "verify.phases_incomplete", items=", ".join(incomplete))
        )
    else:
        findings.append(finding(Status.OK, "verify.phases_ok"))
    return findings


def gateway_gaps(text: str | None) -> tuple[str, ...]:
    if text is None:
        return (GAP_MISSING,)
    gaps: list[str] = []
    if GATEWAY_RUN not in text:
        gaps.append(GAP_RUN)
    if not GATEWAY_READ.search(text):
        gaps.append(GAP_READ)
    if PIPED_GATEWAY.search(text):
        gaps.append(GAP_PIPED)
    return tuple(gaps)


def check_gateway(tree: ForgeTree) -> list[Finding]:
    if not tree.gateway:
        return []
    findings: list[Finding] = []
    for path in GATEWAY_FILES:
        gaps = gateway_gaps(tree.gateway_texts.get(path))
        if not gaps:
            findings.append(finding(Status.OK, "verify.gateway_ok", path=path))
            continue
        if GAP_MISSING in gaps:
            findings.append(finding(Status.FAIL, "verify.gateway_missing", path=path))
        commands = [GAP_COMMANDS[gap] for gap in gaps if gap in GAP_COMMANDS]
        if commands:
            items = ", ".join(commands)
            findings.append(finding(Status.FAIL, "verify.gateway_lacks", path=path, items=items))
        if GAP_PIPED in gaps:
            findings.append(finding(Status.FAIL, "verify.gateway_piped", path=path))
    return findings


def verify_tree(tree: ForgeTree, placeholder_texts: Mapping[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    hits = placeholders(placeholder_texts)
    if hits:
        findings.append(finding(Status.FAIL, "verify.placeholders", items=", ".join(hits[:5])))
    else:
        findings.append(finding(Status.OK, "verify.placeholders_clean"))
    findings.extend(check_ceilings(tree.texts.get("CLAUDE.md"), tree.per_directory))
    findings.extend(check_artifacts(tree))
    findings.extend(check_gateway(tree))
    findings.extend(finding(Status.WARN, "verify.kept", path=path) for path in tree.new_files)
    return findings


def estimated_tokens(size_bytes: int) -> int:
    return size_bytes // 4


def passed(findings: Sequence[Finding]) -> bool:
    return all(finding.status is not Status.FAIL for finding in findings)
