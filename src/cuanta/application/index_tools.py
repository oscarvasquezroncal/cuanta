from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import asdict

from cuanta.application.index_read import IndexRead
from cuanta.domain.code_index import IndexedFile, IndexRow, index_path
from cuanta.domain.index_facts import revalidate_fact
from cuanta.domain.spectrum import estimated_tokens

_RANGE = re.compile(r"([1-9]\d*)[:-]([1-9]\d*)")
_SYMBOL_RELATIONS = frozenset(
    {"function", "class", "method", "variable", "type", "component", "service", "store", "hook"}
)


def _descriptor(
    name: str,
    description: str,
    properties: dict[str, object],
    required: tuple[str, ...],
    selectors: tuple[str, ...] = (),
) -> dict[str, object]:
    schema: dict[str, object] = {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }
    if selectors:
        schema["oneOf"] = [{"required": [selector]} for selector in selectors]
    return {
        "name": name,
        "description": description,
        "inputSchema": schema,
        "annotations": {
            "readOnlyHint": name != "note",
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }


_STRING: dict[str, object] = {"type": "string", "minLength": 1}
TOOLS: tuple[dict[str, object], ...] = (
    _descriptor(
        "find",
        "Find indexed files with deterministic relevance reasons, without model calls.",
        {"query": _STRING, "k": {"type": "integer", "minimum": 1, "maximum": 30}},
        ("query",),
    ),
    _descriptor(
        "card", "Get a compact handling card for an indexed file.", {"path": _STRING}, ("path",)
    ),
    _descriptor(
        "impact",
        "Get direct indexed neighbours of a path or one unambiguous symbol.",
        {"path": _STRING, "symbol": _STRING},
        (),
        ("path", "symbol"),
    ),
    _descriptor(
        "facts", "Get source-validated, anchored facts for a file.", {"path": _STRING}, ("path",)
    ),
    _descriptor(
        "page",
        "Read at most 200 numbered lines by inclusive a:b (or a-b) range or one symbol. "
        "Long lines are clipped.",
        {
            "path": _STRING,
            "lines": {"type": "string", "pattern": r"^[1-9]\d*[:-][1-9]\d*$"},
            "symbol": _STRING,
            "level": {"type": "string", "enum": ["L2"]},
        },
        ("path",),
        ("lines", "symbol"),
    ),
    _descriptor(
        "tests_for",
        "Get indexed tests and verification commands for a file.",
        {"path": _STRING},
        ("path",),
    ),
    _descriptor(
        "note",
        "Save a fact anchored to the current file, inclusive a:b lines, or one symbol.",
        {"path": _STRING, "text": _STRING, "anchor": _STRING},
        ("path", "text"),
    ),
)


def _string(args: Mapping[str, object], name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _range(value: str) -> tuple[int, int]:
    match = _RANGE.fullmatch(value)
    if match is None:
        raise ValueError("Line ranges must use inclusive a:b or a-b syntax")
    start, end = int(match[1]), int(match[2])
    if end < start:
        raise ValueError("Line ranges must be ordered")
    return start, end


class IndexTools:
    def __init__(self, reader: IndexRead) -> None:
        self.reader = reader

    def call(self, name: str, args: Mapping[str, object]) -> object:
        allowed = {
            "find": {"query", "k"},
            "card": {"path"},
            "impact": {"path", "symbol"},
            "facts": {"path"},
            "page": {"path", "lines", "symbol", "level"},
            "tests_for": {"path"},
            "note": {"path", "text", "anchor"},
        }
        if name not in allowed:
            raise ValueError("Unknown index tool")
        if set(args) - allowed[name]:
            raise ValueError("Unexpected tool arguments")
        if name == "find":
            query = _string(args, "query")
            k = args.get("k", 10)
            if type(k) is not int or not 1 <= k <= 30:
                raise ValueError("k must be an integer from 1 to 30")
            self.reader.update()
            return {"hits": [asdict(hit) for hit in self.reader.find(query, k)], "cost_usd": 0}
        if name == "impact":
            if ("path" in args) == ("symbol" in args):
                raise ValueError("Choose exactly one of path or symbol")
            selector = "path" if "path" in args else "symbol"
            value = _string(args, selector)
            self.reader.update()
            path = index_path(value) if selector == "path" else self._symbol(value).path
            self._source(path)
            return {
                "path": path,
                "neighbours": [
                    {"path": other, "reason": reason} for other, reason in self.reader.impact(path)
                ],
                "cost_usd": 0,
            }
        path = index_path(_string(args, "path"))
        if name == "page":
            return self._page(path, args)
        if name == "note":
            return self._note(path, args)
        self.reader.update()
        file, text = self._source(path)
        if name == "card":
            return {"card": asdict(self.reader.card(path)), "cost_usd": 0}
        if name == "facts":
            facts = tuple(
                validated
                for row in self.reader.facts(path)
                if not (validated := revalidate_fact(row, file, text)).stale
            )
            return {"path": path, "facts": [asdict(row) for row in facts], "cost_usd": 0}
        files = {item.path: item for item in self.reader.service.index.files()}
        links = tuple(
            row
            for row in self.reader.service.index.rows("test_links")
            if row.target == path
            and row.path in files
            and not row.stale
            and row.source_hash == files[row.path].content_hash
        )
        return {"path": path, "tests": [asdict(row) for row in links], "cost_usd": 0}

    def _source(self, path: str) -> tuple[IndexedFile, str]:
        file = next((item for item in self.reader.service.index.files() if item.path == path), None)
        text = self.reader.service.inventory.read(path) if file else None
        if file is None or text is None:
            raise ValueError("The file is not an indexed readable source")
        if hashlib.sha256(text.encode()).hexdigest() != file.content_hash:
            raise ValueError("The source changed while the index tool was reading it")
        return file, text

    def _symbol(self, name: str, path: str = "") -> IndexRow:
        files = {item.path: item for item in self.reader.service.index.files()}
        selected = {
            (row.path, row.line, row.end_line): row
            for row in self.reader.service.index.rows("symbols", path)
            if row.text == name
            and row.relation in _SYMBOL_RELATIONS
            and row.path in files
            and not row.stale
            and row.source_hash == files[row.path].content_hash
            and row.line > 0
            and row.end_line >= row.line
        }
        if len(selected) != 1:
            raise ValueError("The symbol must identify exactly one current declaration")
        return next(iter(selected.values()))

    def _page(self, path: str, args: Mapping[str, object]) -> dict[str, object]:
        if ("lines" in args) == ("symbol" in args):
            raise ValueError("Choose exactly one of lines or symbol")
        if args.get("level", "L2") != "L2":
            raise ValueError("Only L2 source excerpts are supported")
        selector = "lines" if "lines" in args else "symbol"
        value = _string(args, selector)
        bounds = _range(value) if selector == "lines" else None
        if bounds and bounds[1] - bounds[0] + 1 > 200:
            raise ValueError("Source excerpts are limited to 200 lines")
        self.reader.update()
        file, text = self._source(path)
        lines = text.splitlines()
        if bounds is None:
            symbol = self._symbol(value, path)
            bounds = symbol.line, symbol.end_line
        start, requested_end = bounds
        if requested_end > len(lines):
            raise ValueError("The requested line range is outside the current source")
        end = min(requested_end, start + 199)
        selected = lines[start - 1 : end]
        rendered = "\n".join(
            f"{number}: {line[:300]}{' [line clipped]' if len(line) > 300 else ''}"
            for number, line in enumerate(selected, start)
        )
        truncated = requested_end != end or any(len(line) > 300 for line in selected)
        return {
            "path": path,
            "line": start,
            "end_line": end,
            "requested_end_line": requested_end,
            "text": rendered,
            "level": "L2",
            "source_hash": file.content_hash,
            "truncated": truncated,
            "estimated_tokens": estimated_tokens(len(rendered.encode())),
            "cost_usd": 0,
        }

    def _note(self, path: str, args: Mapping[str, object]) -> dict[str, object]:
        text = _string(args, "text")
        if len(text) > 4000:
            raise ValueError("Notes are limited to 4000 characters")
        anchor = _string(args, "anchor") if "anchor" in args else ""
        self.reader.update()
        self._source(path)
        if anchor:
            if _RANGE.fullmatch(anchor):
                line, end = _range(anchor)
            else:
                symbol = self._symbol(anchor, path)
                line, end = symbol.line, symbol.end_line
        else:
            line = end = 0
        row = self.reader.service.note(path, text, line, end)
        return {"note": asdict(row), "cost_usd": 0}
