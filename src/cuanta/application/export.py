from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import asdict, dataclass
from typing import Any

from cuanta.domain.errors import DomainFailure
from cuanta.ports.ledger import EventQuery, Ledger

TABLES = ("runs", "events", "test_runs", "decisions", "baselines")
FORMATS = ("json", "csv")
RAW_FIELD = "raw"
CSV_ENCODING = "utf-8-sig"
ZIP_STAMP = (2026, 1, 1, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class ExportFile:
    suffix: str
    data: bytes
    tables: tuple[str, ...]
    rows: int


def rows_for(
    ledger: Ledger, table: str, run_id: str = "", include_raw: bool = False
) -> list[dict[str, Any]]:
    if table == "runs":
        runs = ledger.runs()
        return [asdict(run) for run in runs if not run_id or run.id == run_id]
    if table == "events":
        rows = [asdict(event) for event in ledger.events(EventQuery(run_id=run_id))]
        if not include_raw:
            for row in rows:
                row.pop(RAW_FIELD, None)
        return rows
    if table == "test_runs":
        return [asdict(record) for record in ledger.test_runs(run_id=run_id)]
    if table == "decisions":
        return [asdict(decision) for decision in ledger.decisions(run_id=run_id)]
    if table == "baselines":
        return [asdict(baseline) for baseline in ledger.baselines(run_id=run_id)]
    raise DomainFailure(f"unknown table {table}", f"use one of {', '.join(TABLES)}")


def _selected(table: str) -> tuple[str, ...]:
    if table == "all":
        return TABLES
    if table not in TABLES:
        raise DomainFailure(f"unknown table {table}", f"use all or one of {', '.join(TABLES)}")
    return (table,)


def _csv_text(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(rows)
    return buffer.getvalue()


def _json_text(document: dict[str, list[dict[str, Any]]]) -> str:
    return json.dumps(document, indent=2, default=str, ensure_ascii=False) + "\n"


def export(
    ledger: Ledger, fmt: str, table: str, run_id: str = "", include_raw: bool = False
) -> str:
    selected = _selected(table)
    if fmt == "json":
        return _json_text({name: rows_for(ledger, name, run_id, include_raw) for name in selected})
    if fmt == "csv":
        if len(selected) > 1:
            raise DomainFailure("csv writes one table per file", "add --out to get a ZIP")
        return _csv_text(rows_for(ledger, selected[0], run_id, include_raw))
    raise DomainFailure(f"unknown format {fmt}", "use json or csv")


def export_file(
    ledger: Ledger, fmt: str, table: str, run_id: str = "", include_raw: bool = False
) -> ExportFile:
    selected = _selected(table)
    if fmt == "json":
        document = {name: rows_for(ledger, name, run_id, include_raw) for name in selected}
        rows = sum(len(items) for items in document.values())
        return ExportFile(".json", _json_text(document).encode("utf-8"), selected, rows)
    if fmt != "csv":
        raise DomainFailure(f"unknown format {fmt}", "use json or csv")
    if len(selected) == 1:
        rows_list = rows_for(ledger, selected[0], run_id, include_raw)
        data = _csv_text(rows_list).encode(CSV_ENCODING)
        return ExportFile(".csv", data, selected, len(rows_list))
    buffer = io.BytesIO()
    total = 0
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in selected:
            rows_list = rows_for(ledger, name, run_id, include_raw)
            total += len(rows_list)
            info = zipfile.ZipInfo(f"{name}.csv", date_time=ZIP_STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, _csv_text(rows_list).encode(CSV_ENCODING))
    return ExportFile(".zip", buffer.getvalue(), selected, total)


def target_name(path: str, suffix: str) -> str:
    stem, dot, extension = path.rpartition(".")
    if dot and "/" not in extension and "\\" not in extension:
        return f"{stem}{suffix}"
    return f"{path}{suffix}"
