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
    except (TypeError, ValueError, OverflowError):
        return ""
    if nanos <= 0:
        return ""
    try:
        moment = datetime.fromtimestamp(nanos / 1_000_000_000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return ""
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


@dataclass(frozen=True, slots=True)
class Unreadable:
    name: str
    attributes: dict[str, Any]
    resource: dict[str, Any]
    trace_id: str
    ts: str
    error: Exception


def _text(value: Any) -> str | None:
    if isinstance(value, dict) and isinstance(value.get("stringValue"), str):
        return str(value["stringValue"])
    return None


def _text_attributes(items: Any) -> dict[str, Any]:
    if not isinstance(items, list):
        return {}
    return {
        item["key"]: text
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get("key"), str)
        and (text := _text(item.get("value"))) is not None
    }


def _named(name: Any, body: Any, event_name: Any) -> str:
    if isinstance(name, str) and name:
        if "." not in name and isinstance(body, str) and body.endswith(name):
            return body
        return name
    if isinstance(body, str):
        return body
    return event_name if isinstance(event_name, str) else ""


def _event_name(record: dict[str, Any], attrs: dict[str, Any]) -> str:
    return _named(attrs.get("event.name"), any_value(record.get("body")), record.get("eventName"))


def _log_ts(record: dict[str, Any], attrs: dict[str, Any]) -> str:
    stamp = attrs.get("event.timestamp")
    if isinstance(stamp, str) and stamp:
        return stamp
    return nano_to_iso(record.get("timeUnixNano") or record.get("observedTimeUnixNano"))


def _log_record(record: dict[str, Any], resource: dict[str, Any]) -> LogRecord | Unreadable:
    trace_id = str(record.get("traceId") or "")
    try:
        attrs = attributes(record.get("attributes"))
        name = _event_name(record, attrs)
        body = any_value(record.get("body"))
    except Exception as error:
        known = _text_attributes(record.get("attributes"))
        return Unreadable(
            _named(known.get("event.name"), _text(record.get("body")), record.get("eventName")),
            known,
            resource,
            trace_id,
            _log_ts(record, known),
            error,
        )
    return LogRecord(
        name=name,
        attributes=attrs,
        resource=resource,
        ts=_log_ts(record, attrs),
        trace_id=trace_id,
        body=body,
        raw=record,
    )


def iter_logs(payload: Any) -> Iterator[LogRecord | Unreadable]:
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
                if isinstance(record, dict):
                    yield _log_record(record, resource)


def _number(raw: Any) -> float:
    try:
        return float(raw if raw is not None else 0)
    except (TypeError, ValueError):
        return 0.0


def _metric_point(
    name: str, point: dict[str, Any], resource: dict[str, Any]
) -> MetricPoint | Unreadable:
    ts = nano_to_iso(point.get("timeUnixNano"))
    try:
        value = _number(point.get("asDouble", point.get("asInt", point.get("sum", 0))))
        attrs = attributes(point.get("attributes"))
    except Exception as error:
        return Unreadable(name, _text_attributes(point.get("attributes")), resource, "", ts, error)
    return MetricPoint(name=name, value=value, attributes=attrs, resource=resource, ts=ts)


def iter_metrics(payload: Any) -> Iterator[MetricPoint | Unreadable]:
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
                        if isinstance(point, dict):
                            yield _metric_point(name, point, resource)


def _duration_ms(span: dict[str, Any]) -> int:
    try:
        elapsed = int(span.get("endTimeUnixNano", 0)) - int(span.get("startTimeUnixNano", 0))
    except (TypeError, ValueError):
        return 0
    return max(elapsed // 1_000_000, 0)


def _span_record(span: dict[str, Any], resource: dict[str, Any]) -> SpanRecord | Unreadable:
    name = str(span.get("name") or "")
    trace_id = str(span.get("traceId") or "")
    ts = nano_to_iso(span.get("startTimeUnixNano"))
    try:
        duration = _duration_ms(span)
        attrs = attributes(span.get("attributes"))
    except Exception as error:
        return Unreadable(
            name, _text_attributes(span.get("attributes")), resource, trace_id, ts, error
        )
    return SpanRecord(
        name=name,
        trace_id=trace_id,
        attributes=attrs,
        resource=resource,
        ts=ts,
        duration_ms=duration,
    )


def iter_spans(payload: Any) -> Iterator[SpanRecord | Unreadable]:
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
                if isinstance(span, dict):
                    yield _span_record(span, resource)
