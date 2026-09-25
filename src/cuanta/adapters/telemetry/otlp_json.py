from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def any_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "boolValue", "doubleValue"):
        if key in value:
            return value[key]
    if "intValue" in value:
        raw = value["intValue"]
        try:
            return int(raw)
        except (TypeError, ValueError):
            return raw
    if "arrayValue" in value:
        values = (
            value["arrayValue"].get("values", []) if isinstance(value["arrayValue"], dict) else []
        )
        return [any_value(item) for item in values]
    if "kvlistValue" in value:
        kvlist = value["kvlistValue"]
        return attributes(kvlist.get("values", []) if isinstance(kvlist, dict) else [])
    if "bytesValue" in value:
        return value["bytesValue"]
    return None


def attributes(items: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not isinstance(items, list):
        return result
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("key"), str):
            result[item["key"]] = any_value(item.get("value"))
    return result


def nano_to_iso(value: Any) -> str:
    try:
        nanos = int(value)
    except (TypeError, ValueError):
        return ""
    if nanos <= 0:
        return ""
    moment = datetime.fromtimestamp(nanos / 1_000_000_000, tz=UTC)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class LogRecord:
    name: str
    attributes: dict[str, Any]
    resource: dict[str, Any]
    ts: str
    trace_id: str
    body: Any
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MetricPoint:
    name: str
    value: float
    attributes: dict[str, Any]
    resource: dict[str, Any]
    ts: str


@dataclass(frozen=True, slots=True)
class SpanRecord:
    name: str
    trace_id: str
    attributes: dict[str, Any]
    resource: dict[str, Any]
    ts: str
    duration_ms: int


def _event_name(record: dict[str, Any], attrs: dict[str, Any]) -> str:
    body = any_value(record.get("body"))
    name = attrs.get("event.name")
    if isinstance(name, str) and name:
        if "." not in name and isinstance(body, str) and body.endswith(name):
            return body
        return name
    if isinstance(body, str):
        return body
    event_name = record.get("eventName")
    return event_name if isinstance(event_name, str) else ""


def iter_logs(payload: Any) -> Iterator[LogRecord]:
    if not isinstance(payload, dict):
        return
    for resource_logs in payload.get("resourceLogs") or []:
        if not isinstance(resource_logs, dict):
            continue
        resource = attributes((resource_logs.get("resource") or {}).get("attributes"))
        for scope_logs in resource_logs.get("scopeLogs") or []:
            if not isinstance(scope_logs, dict):
                continue
            for record in scope_logs.get("logRecords") or []:
                if not isinstance(record, dict):
                    continue
                attrs = attributes(record.get("attributes"))
                timestamp = record.get("timeUnixNano") or record.get("observedTimeUnixNano")
                ts = (
                    attrs.get("event.timestamp")
                    if isinstance(attrs.get("event.timestamp"), str)
                    else ""
                )
                yield LogRecord(
                    name=_event_name(record, attrs),
                    attributes=attrs,
                    resource=resource,
                    ts=ts or nano_to_iso(timestamp),
                    trace_id=str(record.get("traceId") or ""),
                    body=any_value(record.get("body")),
                    raw=record,
                )


def iter_metrics(payload: Any) -> Iterator[MetricPoint]:
    if not isinstance(payload, dict):
        return
    for resource_metrics in payload.get("resourceMetrics") or []:
        if not isinstance(resource_metrics, dict):
            continue
        resource = attributes((resource_metrics.get("resource") or {}).get("attributes"))
        for scope in resource_metrics.get("scopeMetrics") or []:
            if not isinstance(scope, dict):
                continue
            for metric in scope.get("metrics") or []:
                if not isinstance(metric, dict):
                    continue
                name = str(metric.get("name") or "")
                for kind in ("sum", "gauge", "histogram"):
                    body = metric.get(kind)
                    if not isinstance(body, dict):
                        continue
                    for point in body.get("dataPoints") or []:
                        if not isinstance(point, dict):
                            continue
                        raw_value = point.get("asDouble", point.get("asInt", point.get("sum", 0)))
                        try:
                            value = float(raw_value if raw_value is not None else 0)
                        except (TypeError, ValueError):
                            value = 0.0
                        yield MetricPoint(
                            name=name,
                            value=value,
                            attributes=attributes(point.get("attributes")),
                            resource=resource,
                            ts=nano_to_iso(point.get("timeUnixNano")),
                        )


def iter_spans(payload: Any) -> Iterator[SpanRecord]:
    if not isinstance(payload, dict):
        return
    for resource_spans in payload.get("resourceSpans") or []:
        if not isinstance(resource_spans, dict):
            continue
        resource = attributes((resource_spans.get("resource") or {}).get("attributes"))
        for scope in resource_spans.get("scopeSpans") or []:
            if not isinstance(scope, dict):
                continue
            for span in scope.get("spans") or []:
                if not isinstance(span, dict):
                    continue
                try:
                    duration = (
                        int(span.get("endTimeUnixNano", 0)) - int(span.get("startTimeUnixNano", 0))
                    ) // 1_000_000
                except (TypeError, ValueError):
                    duration = 0
                yield SpanRecord(
                    name=str(span.get("name") or ""),
                    trace_id=str(span.get("traceId") or ""),
                    attributes=attributes(span.get("attributes")),
                    resource=resource,
                    ts=nano_to_iso(span.get("startTimeUnixNano")),
                    duration_ms=max(duration, 0),
                )
