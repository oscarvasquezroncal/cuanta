from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, replace
from datetime import UTC, datetime

from cuanta.domain.code_index import IndexedFile, IndexHistory, IndexReport, IndexRow, index_path

_REFERENCE = re.compile(
    r"(?<![\w/\\:])((?:[\w.@-]+[/\\])*[\w.@-]+\.[\w]+):([1-9]\d*)"
    r"(?:[-\u2013\u2014]([1-9]\d*))?(?::\d+)?"
)


def anchor_hash(text: str, line: int = 0, end_line: int = 0) -> str:
    if line == 0:
        return hashlib.sha256(text.encode()).hexdigest()
    lines = text.splitlines(keepends=True)
    end = end_line or line
    if line < 1 or end < line or end > len(lines):
        return ""
    return hashlib.sha256("".join(lines[line - 1 : end]).encode()).hexdigest()


def report_facts(report: IndexReport) -> tuple[IndexRow, ...]:
    sources = dict(report.sources)
    digest = hashlib.sha256(report.text.encode()).hexdigest()
    records: dict[str, IndexRow] = {}
    for finding in report.text.splitlines():
        for match in _REFERENCE.finditer(finding):
            try:
                path = index_path(match[1])
            except ValueError:
                continue
            line, end = int(match[2]), int(match[3] or match[2])
            if end < line:
                continue
            source = sources.get(path)
            anchor = anchor_hash(source, line, end) if source is not None else ""
            identity = hashlib.sha256(
                f"{report.provenance}\0{digest}\0{path}\0{line}\0{end}\0{finding}".encode()
            ).hexdigest()
            records[identity] = IndexRow(
                id="finding:" + identity,
                path=path,
                source_hash=hashlib.sha256(source.encode()).hexdigest()
                if source is not None
                else "unverified",
                provenance=f"{report.provenance}#{digest}",
                text=finding.strip(),
                line=line,
                end_line=end,
                target="anchor:" + anchor if anchor else "",
                relation="finding",
                stale=not bool(anchor),
            )
    return tuple(records.values())


def revalidate_fact(row: IndexRow, file: IndexedFile | None, text: str | None) -> IndexRow:
    if file is None or text is None or not row.target.startswith("anchor:"):
        return replace(row, stale=True)
    current_hash = hashlib.sha256(text.encode()).hexdigest()
    if current_hash != file.content_hash:
        return replace(row, stale=True)
    fresh = anchor_hash(text, row.line, row.end_line) == row.target.removeprefix("anchor:")
    return replace(row, source_hash=current_hash if fresh else row.source_hash, stale=not fresh)


def agent_note(
    file: IndexedFile, text: str, note: str, line: int = 0, end_line: int = 0
) -> IndexRow:
    anchor = anchor_hash(text, line, end_line)
    if (
        not note.strip()
        or not anchor
        or hashlib.sha256(text.encode()).hexdigest() != file.content_hash
    ):
        raise ValueError("A note requires current source and a valid anchor")
    identity = hashlib.sha256(f"{file.path}\0{line}\0{end_line}\0{note}".encode()).hexdigest()
    return IndexRow(
        id="note:" + identity,
        path=file.path,
        source_hash=file.content_hash,
        provenance="agent-note:" + file.content_hash,
        text=note.strip(),
        line=line,
        end_line=end_line or line,
        target="anchor:" + anchor,
        relation="note",
    )


def history_row(record: IndexHistory, source_hash: str) -> IndexRow:
    path = index_path(record.path)
    identity = hashlib.sha256(
        f"{record.run_id}\0{path}\0{record.action}\0{record.signature}".encode()
    ).hexdigest()
    return IndexRow(
        id="history:" + identity,
        path=path,
        source_hash=source_hash,
        provenance="ledger:" + record.run_id,
        text=json.dumps(asdict(record), ensure_ascii=False, sort_keys=True),
        target=record.task_type,
        relation=record.action,
    )


def history_prior(rows: tuple[IndexRow, ...], task_type: str, now: str) -> float:
    current = _timestamp(now)
    if current is None:
        return 0.0
    runs: dict[str, float] = {}
    for row in rows:
        if task_type and row.target != task_type:
            continue
        try:
            data = json.loads(row.text)
            at = _timestamp(str(data.get("at", "")))
            if at is None:
                continue
            age = max(0.0, (current - at).total_seconds() / 86400)
            weight = math.exp(-math.log(2) * age / 30)
            outcome = data.get("outcome", "")
            weight *= 1.5 if outcome == "accepted" else 0.5 if outcome == "rejected" else 1.0
            weight /= 1 + max(0, int(data.get("retries", 0)))
            weight *= 0.5 if row.stale else 1.0
            runs[row.provenance] = max(runs.get(row.provenance, 0.0), weight)
        except (ValueError, TypeError, AttributeError):
            continue
    return sum(runs.values())


def _timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    except ValueError:
        return None
