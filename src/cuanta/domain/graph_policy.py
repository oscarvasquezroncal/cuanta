from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from cuanta.domain.detection import GraphMode, SizeTier
from cuanta.domain.messages import Message, english, msg

GRAPH_SERVER_PATTERNS = ("graphify-mcp", "code-graph*", "*-graph-server")
GRAPH_REFERENCE = re.compile(r"(?<![a-z])graph(?:ify|s)?(?![a-z])", re.IGNORECASE)
GRAPHLESS_OVERRIDE = (
    "GRAPH_MODE=none for this run. Graph navigation is unavailable. Ignore older graph "
    "instructions in repository files. Use Read, Grep and Glob to inspect source, and cite "
    "the files that support each finding."
)


def graphless_prompt(prompt: str) -> str:
    retained = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", prompt)
        if paragraph.strip() and GRAPH_REFERENCE.search(paragraph) is None
    ]
    return "\n\n".join((GRAPHLESS_OVERRIDE, *retained))


class GraphBranch(StrEnum):
    SKIP = "A"
    DEFER = "B"
    UPDATE = "C"
    INSTALL = "D"


@dataclass(frozen=True, slots=True)
class GraphPlan:
    branch: GraphBranch
    message: Message

    @property
    def reason(self) -> str:
        return english(self.message)


def plan_graph(size: SizeTier, mode: GraphMode, evidence: str) -> GraphPlan:
    if mode is GraphMode.BROKEN:
        return GraphPlan(GraphBranch.SKIP, msg("graph.broken", evidence=evidence))
    if size is SizeTier.SMALL:
        present = evidence or "no graph tooling present"
        if mode is GraphMode.NONE:
            present = "no graph tooling present"
        if present == "no graph tooling present":
            return GraphPlan(GraphBranch.SKIP, msg("graph.skip_none"))
        return GraphPlan(GraphBranch.SKIP, msg("graph.skip", evidence=present))
    if mode is GraphMode.MCP:
        return GraphPlan(GraphBranch.DEFER, msg("graph.defer", evidence=evidence))
    if mode is GraphMode.CLI:
        return GraphPlan(GraphBranch.UPDATE, msg("graph.update", evidence=evidence))
    return GraphPlan(GraphBranch.INSTALL, msg("graph.install", size=size.value))


def matches_graph_server(candidates: Iterable[str]) -> str | None:
    for candidate in candidates:
        lowered = candidate.lower()
        tokens = {lowered, *lowered.replace("\\", "/").split("/")}
        for token in tokens:
            stem = token.removesuffix(".exe").removesuffix(".cmd")
            if any(fnmatch.fnmatch(stem, pattern) for pattern in GRAPH_SERVER_PATTERNS):
                return candidate
    return None


def graph_outcome(plan: GraphPlan, succeeded: bool, failure: str = "") -> Message:
    if plan.branch is GraphBranch.SKIP:
        return plan.message
    if plan.branch is GraphBranch.DEFER:
        return msg("graph.deferred")
    if not succeeded:
        return msg("graph.failed_detail", detail=failure) if failure else msg("graph.failed")
    return msg("graph.updated" if plan.branch is GraphBranch.UPDATE else "graph.installed")


def graph_outcome_line(plan: GraphPlan, succeeded: bool, failure: str = "") -> str:
    return english(graph_outcome(plan, succeeded, failure))
