from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date

from cuanta.application.run_reports import RunReports
from cuanta.domain.ledger import Run
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.overhead import SessionOverhead, session_overhead
from cuanta.domain.report import (
    ContextSplit,
    FileRef,
    ReportSection,
    docs_path,
    file_refs,
    next_request,
    parse_sections,
    section,
    unified_diff,
)
from cuanta.ports.ledger import EventQuery, Ledger
from cuanta.ports.workspace import Workspace

START_PHASE = "start"


@dataclass(frozen=True, slots=True)
class RunFile:
    path: str
    before: str | None
    after: str | None

    @property
    def diff(self) -> str:
        if self.before is None or self.after is None:
            return ""
        return unified_diff(self.before, self.after, self.path)


@dataclass(frozen=True, slots=True)
class ResultView:
    run: Run
    task_type: str
    simple: bool
    text: str
    sections: tuple[ReportSection, ...]
    changed_files: tuple[str, ...]
    tokens_by_agent: Mapping[str, int]
    tests: str
    split: ContextSplit | None
    refs: tuple[FileRef, ...]
    follow_up: MandateRequest | None
    report_path: str
    overhead: SessionOverhead | None = None
    fallback_error: str = ""
    fallback_from: str = ""

    @property
    def duration_s(self) -> float | None:
        return _seconds_between(self.run.started_at, self.run.ended_at)

    @property
    def title(self) -> str:
        summary = section(self.sections, "summary")
        source = summary.body if summary is not None and summary.body else self.text
        for line in source.splitlines():
            cleaned = line.strip().lstrip("#*-0123456789. ").strip("*").strip()
            if cleaned:
                return cleaned
        return self.run.id


def _seconds_between(start: str, end: str) -> float | None:
    from datetime import datetime

    try:
        return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
    except ValueError:
        return None


def _strings(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in value) if isinstance(value, list) else ()


def _counts(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): int(item) for key, item in value.items() if isinstance(item, int)}


def run_markdown(view: ResultView) -> str:
    run = view.run
    cost = "n/a" if run.cost_usd is None else f"${run.cost_usd:,.2f}"
    duration = "n/a" if view.duration_s is None else f"{view.duration_s:,.0f} s"
    mode = "simple mode (one agent, no project knowledge)" if view.simple else "Forge pipeline"
    lines = [
        f"# Run {run.id}",
        "",
        f"- Type: {view.task_type or run.kind} · {mode}",
        f"- Status: {run.status} · engine {run.engine} · model {run.model or 'default'}",
        f"- Started {run.started_at} · duration {duration} · cost {cost}",
    ]
    if view.split is not None:
        lines.append(
            f"- Context of the first request: {view.split.first_request:,} tokens · "
            f"fixed session context ≈ {view.split.fixed:,} ({view.split.fixed_share:.0%}) · "
            f"your request ≈ {view.split.request:,}"
        )
    if view.changed_files:
        lines += ["", "## Changed files", "", *(f"- `{path}`" for path in view.changed_files)]
    if view.tokens_by_agent:
        lines += ["", "## Tokens by agent", "", "| Agent | Tokens |", "|---|---:|"]
        lines += [f"| {agent} | {tokens:,} |" for agent, tokens in view.tokens_by_agent.items()]
    lines += ["", "## Report", "", view.text.strip() or "_The engine returned no report._", ""]
    return "\n".join(lines)


class ResultQuery:
    def __init__(
        self,
        workspace: Workspace,
        ledger: Ledger,
        today: Callable[[], date],
    ) -> None:
        self._workspace = workspace
        self._ledger = ledger
        self._reports = RunReports(workspace)
        self._today = today

    def load(self, run_id: str) -> ResultView | None:
        run = self._ledger.get_run(run_id)
        if run is None:
            return None
        meta = self._reports.meta(run.id) or {}
        text = self._reports.report(run.id) or ""
        prompt_chars = meta.get("prompt_chars")
        events = self._ledger.events(EventQuery(run_id=run.id))
        fallback = next(
            (item for item in self._ledger.decisions(run_id=run.id) if item.fallback_error),
            None,
        )
        overhead = session_overhead(events, prompt_chars if isinstance(prompt_chars, int) else 0)
        return ResultView(
            run=run,
            task_type=str(meta.get("task_type") or ""),
            simple=meta.get("simple") is True,
            text=text,
            sections=parse_sections(text),
            changed_files=_strings(meta.get("changed_files")),
            tokens_by_agent=_counts(meta.get("tokens_by_agent")),
            tests=str(meta.get("tests") or ""),
            split=overhead.split,
            refs=file_refs(text),
            follow_up=next_request(text),
            report_path=self._reports.report_path(run.id) if text else "",
            overhead=overhead,
            fallback_error=fallback.fallback_error if fallback is not None else "",
            fallback_from=fallback.fallback_from if fallback is not None else "",
        )

    def before(self, run_id: str, path: str) -> str | None:
        for snapshot in self._ledger.snapshots(run_id, START_PHASE):
            if snapshot.path == path:
                return self._reports.blob(snapshot.sha256)
        return None

    def current(self, path: str) -> str | None:
        return self._workspace.read_text(path)

    def file(self, run_id: str, path: str) -> RunFile:
        return RunFile(path, self.before(run_id, path), self.current(path))

    def save_to_docs(self, view: ResultView) -> str:
        target = docs_path(view.task_type, view.title, self._today())
        self._workspace.write_text(target, view.text.strip() + "\n")
        return target

    def export_markdown(self, view: ResultView) -> str:
        target = f"{self._reports.folder(view.run.id)}/run-report.md"
        self._workspace.write_text(target, run_markdown(view))
        return target
