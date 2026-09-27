from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import cast

from cuanta.adapters.graph.file_graph import graph_path
from cuanta.adapters.system.index_inventory import _linked
from cuanta.domain.code_index import IndexHistory, IndexReport, index_path
from cuanta.domain.index_facts import report_facts
from cuanta.domain.ledger import Run
from cuanta.domain.sandbox import SANDBOX_MODE
from cuanta.domain.spectrum import EDIT_TOOLS, READ_TOOLS
from cuanta.ports.ledger import EventQuery, Ledger

_DIGEST = re.compile(r"[0-9a-f]{64}")
_READ = READ_TOOLS | {"read_file", "cuanta.page", "mcp__cuanta__page"}
_EDIT = EDIT_TOOLS | {"apply_patch", "write_file", "edit_file"}


def _object(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _bytes(root: Path, relative: str) -> bytes | None:
    path = graph_path(root, relative)
    try:
        return path.read_bytes() if path is not None else None
    except OSError:
        return None


def _text(root: Path, relative: str) -> str | None:
    payload = _bytes(root, relative)
    try:
        return payload.decode("utf-8") if payload is not None and b"\0" not in payload else None
    except UnicodeError:
        return None


def _json(root: Path, relative: str) -> dict[str, object]:
    text = _text(root, relative)
    try:
        return _object(json.loads(text)) if text is not None else {}
    except ValueError:
        return {}


def _folder(root: Path, relative: str) -> Path | None:
    try:
        current = root
        for part in index_path(relative).split("/"):
            current /= part
            if _linked(current):
                return None
        return current if current.is_dir() and current.resolve().is_relative_to(root) else None
    except (OSError, ValueError):
        return None


def _reports(root: Path, relative: str) -> tuple[str, ...]:
    folder = _folder(root, relative)
    if folder is None:
        return ()
    paths: list[str] = []
    for directory, children, names in os.walk(folder, followlinks=False):
        base = Path(directory)
        kept: list[str] = []
        for name in sorted(children):
            try:
                if not _linked(base / name):
                    kept.append(name)
            except OSError:
                continue
        children[:] = kept
        for name in sorted(names):
            if name.endswith(".md"):
                path = (base / name).relative_to(root).as_posix()
                if graph_path(root, path) is not None:
                    paths.append(path)
    return tuple(sorted(paths))


def _relative(value: str, roots: tuple[str, ...]) -> str:
    normalized = value.replace("\\", "/")
    if not normalized or "\0" in normalized:
        return ""
    if not PurePosixPath(normalized).is_absolute() and not PureWindowsPath(value).drive:
        try:
            return index_path(normalized)
        except ValueError:
            return ""
    for root in sorted(roots, key=len, reverse=True):
        prefix = root.replace("\\", "/").rstrip("/") + "/"
        windows = bool(PureWindowsPath(root).drive)
        candidate = normalized.casefold() if windows else normalized
        expected = prefix.casefold() if windows else prefix
        if candidate.startswith(expected):
            try:
                return index_path(normalized[len(prefix) :])
            except ValueError:
                return ""
    return ""


@dataclass(slots=True)
class _ReportGroup:
    provenance: str
    text: str
    sources: dict[str, str]
    conflicting: set[str]


class LocalIndexKnowledge:
    def __init__(self, project: Path, state_project: Path, ledger: Ledger | None = None) -> None:
        self._project = project.resolve()
        self._state = state_project.resolve()
        self._ledger = ledger

    def _trial(self, run_id: str) -> dict[str, object]:
        trial = _json(self._state, f".cuanta/trials/{run_id}/trial.json")
        return trial if trial.get("run_id") == run_id else {}

    def _roots(self, run: Run, parent: Run) -> tuple[str, ...]:
        roots = [str(self._project), str(self._state)]
        if run.mode == SANDBOX_MODE:
            for owner in (run, parent):
                if owner.mode != SANDBOX_MODE:
                    continue
                copy = self._trial(owner.id).get("copy_root")
                if isinstance(copy, str):
                    normalized = copy.replace("\\", "/")
                    if (
                        (
                            PurePosixPath(normalized).is_absolute()
                            or PureWindowsPath(copy).is_absolute()
                        )
                        and ".." not in PurePosixPath(normalized).parts
                        and "\0" not in normalized
                    ):
                        roots.append(copy)
        return tuple(dict.fromkeys(roots))

    def _sources(self, run_id: str, text: str) -> tuple[tuple[str, str], ...]:
        wanted = {row.path for row in report_facts(IndexReport("lookup", text))}
        sources: dict[str, str] = {}
        ledger = self._ledger
        run = ledger.get_run(run_id) if ledger is not None else None
        start_snapshots = ledger.snapshots(run_id, "start") if ledger is not None else ()
        end_snapshots = ledger.snapshots(run_id, "end") if ledger is not None else ()
        start = (
            {index_path(row.path): row.sha256 for row in start_snapshots if _relative(row.path, ())}
            if ledger is not None
            else {}
        )
        end = (
            {index_path(row.path): row.sha256 for row in end_snapshots if _relative(row.path, ())}
            if ledger is not None
            else {}
        )
        metadata = _json(self._state, f".cuanta/runs/{run_id}/run.json")
        readonly = run is not None and run.task_type == "investigation"
        changed = metadata.get("changed_files")
        changed_paths = (
            {_relative(value, ()) for value in changed if isinstance(value, str)}
            if isinstance(changed, list)
            else set()
        )
        trial = self._trial(run_id)
        raw_changes = trial.get("changes")
        changes: dict[str, dict[str, object]] = {}
        conflicting_changes: set[str] = set()
        if isinstance(raw_changes, list):
            for value in raw_changes:
                change = _object(value)
                raw_path = change.get("path")
                path = _relative(raw_path, ()) if isinstance(raw_path, str) else ""
                if path:
                    if path in changes and changes[path] != change:
                        conflicting_changes.add(path)
                    changes[path] = change
        for path in sorted(wanted):
            if path in conflicting_changes:
                continue
            selected_change = changes.get(path)
            proofs: list[tuple[Path, str, str]] = []
            digest = end.get(path)
            if selected_change is not None:
                after = selected_change.get("after")
                if not isinstance(after, str) or not _DIGEST.fullmatch(after):
                    continue
                if digest is not None and digest != after:
                    continue
                proofs.append((self._state, f".cuanta/trials/{run_id}/files/{path}", after))
                if selected_change.get("before") == after:
                    proofs.append((self._state, f".cuanta/trials/{run_id}/base/{path}", after))
            if digest is not None:
                proofs.extend(
                    (
                        (self._state, f".cuanta/blobs/{digest}", digest),
                        (self._project, path, digest),
                        (self._state, path, digest),
                    )
                )
            elif (
                selected_change is None
                and readonly
                and not end_snapshots
                and path not in changed_paths
            ):
                before = start.get(path)
                if before is not None:
                    proofs.append((self._state, f".cuanta/blobs/{before}", before))
            for root, stored, proof in proofs:
                source = self._verified_source(root, stored, proof)
                if source is not None:
                    sources[path] = source
                    break
        return tuple(sorted(sources.items()))

    @staticmethod
    def _verified_source(root: Path, path: str, digest: str) -> str | None:
        if not _DIGEST.fullmatch(digest):
            return None
        payload = _bytes(root, path)
        if payload is None or sha256(payload).hexdigest() != digest or b"\0" in payload:
            return None
        try:
            return payload.decode("utf-8")
        except UnicodeError:
            return None

    def reports(self) -> tuple[IndexReport, ...]:
        documents: list[tuple[Path, str, str]] = []
        for folder in (".cuanta/runs", ".cuanta/trials"):
            for path in _reports(self._state, folder):
                suffix = path.removeprefix(folder + "/").split("/")
                if len(suffix) == 2 and suffix[1] in {"report.md", "run-report.md"}:
                    documents.append((self._state, path, suffix[0]))
        for root in dict.fromkeys((self._project, self._state)):
            for folder in ("docs/investigations", "docs/runs"):
                documents.extend((root, path, "") for path in _reports(root, folder))
        groups: dict[str, _ReportGroup] = {}
        for root, path, run_id in documents:
            text = _text(root, path)
            if text is None:
                continue
            digest = sha256(text.encode()).hexdigest()
            sources = self._sources(run_id, text) if run_id else ()
            group = groups.setdefault(digest, _ReportGroup("report:" + path, text, {}, set()))
            for source, original in sources:
                if source in group.sources and group.sources[source] != original:
                    group.conflicting.add(source)
                group.sources[source] = original
        return tuple(
            IndexReport(
                group.provenance,
                group.text,
                tuple(
                    sorted(
                        (path, text)
                        for path, text in group.sources.items()
                        if path not in group.conflicting
                    )
                ),
            )
            for _, group in sorted(groups.items(), key=lambda item: (item[1].provenance, item[0]))
        )

    def history(self) -> tuple[IndexHistory, ...]:
        ledger = self._ledger
        if ledger is None:
            return ()
        runs = {run.id: run for run in ledger.runs()}
        history: dict[tuple[str, str, str, str], IndexHistory] = {}
        retries: dict[str, int] = {}
        for run in runs.values():
            root = self._root(run, runs)
            retries[root.id] = max(
                (
                    retries.get(root.id, 0),
                    *(max(0, row.retries) for row in ledger.routing_decisions(run_id=run.id)),
                )
            )
        for run in sorted(runs.values(), key=lambda item: item.id):
            root = self._root(run, runs)
            roots = self._roots(run, root)
            paths: set[str] = set()
            for event in ledger.events(EventQuery(run_id=run.id)):
                path = _relative(event.file_path, roots)
                action = (
                    "read"
                    if event.tool_name in _READ
                    else "edit"
                    if event.tool_name in _EDIT
                    else ""
                )
                if path and action and event.success is not False:
                    paths.add(path)
                    self._remember(history, root, path, action, event.ts, retries.get(root.id, 0))
            for path in (
                f".cuanta/runs/{run.id}/report.md",
                f".cuanta/trials/{run.id}/report.md",
            ):
                text = _text(self._state, path)
                for ref in report_facts(IndexReport("lookup", text or "")):
                    relative = _relative(ref.path, roots)
                    if relative:
                        paths.add(relative)
                        self._remember(
                            history, root, relative, "cite", run.ended_at, retries.get(root.id, 0)
                        )
            trial = self._trial(run.id)
            changes = trial.get("changes")
            if isinstance(changes, list):
                for change in changes:
                    changed_path = _object(change).get("path")
                    relative = (
                        _relative(changed_path, roots) if isinstance(changed_path, str) else ""
                    )
                    if relative:
                        paths.add(relative)
                        self._remember(
                            history, root, relative, "edit", run.ended_at, retries.get(root.id, 0)
                        )
            for record in ledger.test_runs(run_id=run.id):
                for signature in ledger.signatures(record.id):
                    location = (
                        signature.location.rsplit(":", 1)[0]
                        if re.search(r":\d+$", signature.location)
                        else signature.location
                    )
                    path = _relative(location, roots)
                    if path:
                        paths.add(path)
                        self._remember(
                            history,
                            root,
                            path,
                            "failure",
                            record.started_at,
                            retries.get(root.id, 0),
                            signature.signature_id,
                        )
            for path in paths:
                if root.outcome:
                    self._remember(
                        history,
                        root,
                        path,
                        "outcome",
                        root.outcome_at or root.ended_at,
                        retries.get(root.id, 0),
                    )
        return tuple(history[key] for key in sorted(history))

    @staticmethod
    def _root(run: Run, runs: dict[str, Run]) -> Run:
        seen = {run.id}
        while run.parent_id in runs and run.parent_id not in seen:
            run = runs[run.parent_id]
            seen.add(run.id)
        return run

    @staticmethod
    def _remember(
        history: dict[tuple[str, str, str, str], IndexHistory],
        run: Run,
        path: str,
        action: str,
        at: str,
        retries: int,
        signature: str = "",
    ) -> None:
        key = path, run.id, action, signature
        value = IndexHistory(
            path, run.id, run.task_type, action, at, run.outcome, retries, signature
        )
        previous = history.get(key)
        if previous is None or at > previous.at:
            history[key] = value
