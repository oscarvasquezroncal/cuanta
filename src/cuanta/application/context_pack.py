from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace

from cuanta.application.change_plan import IndexChangePlan
from cuanta.application.index_read import IndexRead
from cuanta.domain.anchors import (
    AnchorCheck,
    AnchorState,
    RequestAnchor,
    anchor_candidates,
    anchor_windows,
    packed_state,
    request_anchors,
)
from cuanta.domain.capsules import LineRange, slice_lines
from cuanta.domain.change_plan import (
    ChangePlan,
    guarded,
    mentioned_paths,
    path_matches,
    request_query,
)
from cuanta.domain.code_index import INDEX_TABLES, IndexedFile, IndexRow
from cuanta.domain.depth import Depth, parse_depth, profile
from cuanta.domain.implementation import project_rules
from cuanta.domain.index_cards import handling_card
from cuanta.domain.index_facts import revalidate_fact
from cuanta.domain.index_search import rule_applies
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.pack import ContextPack, Layer, PackItem, compile_pack
from cuanta.domain.spectrum import BYTES_PER_TOKEN
from cuanta.domain.stable import stable_json

PACK_VERSION = 2
QUERY_TERMS = 256
REQUEST_SHARE = 4
LINE_LIMIT = 400
ANCHOR_PREFIX = "anchor:"
WINDOWS = {Depth.QUICK: 12, Depth.NORMAL: 24, Depth.DEEP: 40}
RULE_FILES = (
    "tsconfig.json",
    "eslint.config.js",
    "eslint.config.mjs",
    "eslint.config.cjs",
    "eslint.config.ts",
    ".eslintrc.json",
    ".eslintrc.js",
    ".eslintrc.cjs",
)

Placed = tuple[AnchorCheck, str]
PACKED_STATES = frozenset({AnchorState.PACKED, AnchorState.PARTIAL})


def _numbered(lines: list[str], first: int, last: int) -> str:
    return "\n".join(
        f"{number}: {line[:LINE_LIMIT]}" + (" [line truncated]" if len(line) > LINE_LIMIT else "")
        for number, line in slice_lines(lines, LineRange(first, last))
    )


def _escaped(text: str) -> int:
    return len(json.dumps(text, ensure_ascii=False).encode("utf-8")) - 2


def _head(text: str, room: int) -> str:
    head = text[: max(0, room)]
    while head and (size := _escaped(head)) > room:
        head = head[: max(0, len(head) * room // size - 1)]
    return head


def bounded_request(request: MandateRequest, pack_tokens: int) -> str:
    limit = int(pack_tokens * BYTES_PER_TOKEN) // REQUEST_SHARE
    values = asdict(request)
    text = stable_json(values)
    for name in sorted(values, key=lambda key: (-len(values[key]), key)):
        if len(text.encode("utf-8")) <= limit:
            break
        original = values[name]
        marker = f"\n[{len(original):,} characters in total]"
        values[name] = marker
        room = limit - len(stable_json(values).encode("utf-8"))
        values[name] = _head(original, room) + marker
        text = stable_json(values)
    return text


@dataclass(slots=True)
class _Anchoring:
    anchors: tuple[RequestAnchor, ...]
    by_path: dict[str, list[RequestAnchor]] = field(default_factory=dict)
    placed: dict[RequestAnchor, Placed] = field(default_factory=dict)

    def place(
        self,
        anchor: RequestAnchor,
        path: str,
        state: AnchorState,
        key: str = "",
        lines: tuple[int, int] = (0, 0),
    ) -> None:
        self.placed[anchor] = (AnchorCheck(anchor, path, state, *lines), key)

    def checks(self) -> tuple[Placed, ...]:
        return tuple(self.placed[anchor] for anchor in self.anchors if anchor in self.placed)


def _anchoring(request: MandateRequest, files: dict[str, IndexedFile]) -> _Anchoring:
    anchoring = _Anchoring(request_anchors(request))
    for anchor in anchoring.anchors:
        found = anchor_candidates(anchor.path, files)
        if len(found) == 1:
            anchoring.by_path.setdefault(found[0], []).append(anchor)
        else:
            state = AnchorState.MISSING if not found else AnchorState.AMBIGUOUS
            anchoring.place(anchor, "", state)
    return anchoring


def _settled(placed: tuple[Placed, ...], included: set[str]) -> tuple[AnchorCheck, ...]:
    return tuple(
        replace(check, state=AnchorState.OMITTED, first=0, last=0)
        if check.state in PACKED_STATES and key not in included
        else check
        for check, key in placed
    )


def _protected(path: str, plan: ChangePlan) -> bool:
    return any(path_matches(path, pattern) for pattern in plan.guard)


def _named(request: MandateRequest, files: dict[str, IndexedFile]) -> tuple[str, ...]:
    text = "\n".join((request.what, request.why, request.where, request.constraints, request.tests))
    return tuple(path for path in mentioned_paths(text, tuple(sorted(files))) if path in files)


@dataclass(frozen=True, slots=True)
class _Scope:
    plan: ChangePlan
    edit: frozenset[str]
    edit_only: bool
    window: int
    terms: tuple[str, ...]
    scores: dict[str, float]
    reasons: dict[str, str]


class IndexContextPack:
    def __init__(
        self,
        reader: IndexRead,
        cache: dict[str, ContextPack] | None = None,
        excluded: frozenset[str] = frozenset(),
    ) -> None:
        self._reader = reader
        self._cache = cache if cache is not None else {}
        self._excluded = excluded

    def compile(
        self,
        request: MandateRequest,
        depth: str = "",
        role: str = "",
        plan: ChangePlan | None = None,
    ) -> ContextPack:
        index = self._reader.service.index
        chosen = profile(parse_depth(depth), request.type)
        now = index.meta().get("updated_at", "")
        protection = (
            plan
            if plan is not None
            else IndexChangePlan(index, lambda: now, self._excluded).compile(request)
        )
        state = {
            "files": tuple(
                asdict(file) for file in sorted(index.files(), key=lambda file: file.path)
            ),
            "rows": {
                table: tuple(
                    asdict(row) for row in sorted(index.rows(table), key=lambda row: row.id)
                )
                for table in INDEX_TABLES
            },
            "commands": index.meta().get("verify_commands", "[]"),
        }
        identity = {
            "index": hashlib.sha256(stable_json(state).encode()).hexdigest(),
            "request": asdict(request),
            "depth": chosen.depth.value,
            "role": role,
            "plan": asdict(protection),
            "version": PACK_VERSION,
            **({"excluded": sorted(self._excluded)} if self._excluded else {}),
        }
        key = hashlib.sha256(stable_json(identity).encode()).hexdigest()
        current = {
            file.path: file.content_hash for file in self._reader.service.inventory.candidates()
        }
        coherent = current == {file.path: file.content_hash for file in index.files()}
        if coherent and key in self._cache:
            return self._cache[key]
        self._cache.pop(key, None)
        items, placed = self._items(
            request, protection, chosen.depth, chosen.read_budget, role, chosen.pack_tokens
        )
        result = compile_pack(items, chosen.pack_tokens, key)
        result = replace(result, anchors=_settled(placed, {item.key for item in result.items}))
        if len(self._cache) >= 32:
            self._cache.pop(next(iter(self._cache)))
        if coherent:
            self._cache[key] = result
        return result

    def _items(
        self,
        request: MandateRequest,
        plan: ChangePlan,
        depth: Depth,
        read_budget: int,
        role: str,
        pack_tokens: int,
    ) -> tuple[tuple[PackItem, ...], tuple[Placed, ...]]:
        index = self._reader.service.index
        indexed = {file.path: file for file in index.files()}
        files = {path: file for path, file in indexed.items() if path not in self._excluded}
        query = (
            request_query(request) or " ".join((request.what, request.where)).strip() or request.why
        )
        found = IndexRead(self._reader.service, lambda: index.meta().get("updated_at", "")).find(
            query, read_budget + len(self._excluded), request.type
        )
        hits = tuple(hit for hit in found if hit.path in files)[:read_budget]
        roles = role.lower().replace("_", "-")
        scope = _Scope(
            plan,
            frozenset(item.path for item in plan.edit),
            "senior" in roles or "tester" in roles,
            WINDOWS[depth],
            tuple(dict.fromkeys(term for term in query.casefold().split() if len(term) > 2))[
                :QUERY_TERMS
            ],
            {hit.path: max(1.0, hit.score) for hit in hits},
            {hit.path: "; ".join(hit.reasons) for hit in hits},
        )
        anchoring = _anchoring(request, indexed)
        for path in self._excluded:
            anchoring.by_path.pop(path, None)
        first, named = self._first(anchoring, scope, _named(request, files))
        ordered = tuple(
            path
            for path in dict.fromkeys(
                (*first, *named, *sorted(scope.edit), *(hit.path for hit in hits), *plan.read)
            )
            if path not in self._excluded
        )
        items = self._base(plan)
        for path in ordered[: max(read_budget, len(first))]:
            if path not in files or (
                scope.edit_only and (path not in scope.edit or guarded(path, plan))
            ):
                continue
            here = tuple(anchoring.by_path[path]) if path in first else ()
            items.extend(self._file(files[path], scope, here, anchoring))
        items.append(
            PackItem(
                "request",
                Layer.VOLATILE,
                bounded_request(request, pack_tokens),
                100,
                "current request follows stable context",
            )
        )
        return tuple(items), anchoring.checks()

    def _first(
        self, anchoring: _Anchoring, scope: _Scope, named: tuple[str, ...]
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if not scope.edit_only:
            return tuple(anchoring.by_path), named
        for path, anchors in anchoring.by_path.items():
            if path not in scope.edit and _protected(path, scope.plan):
                for anchor in anchors:
                    anchoring.place(anchor, path, AnchorState.PROTECTED)
        first = tuple(
            path
            for path in anchoring.by_path
            if path in scope.edit and not guarded(path, scope.plan)
        )
        return first, tuple(path for path in named if path in scope.edit)

    def _base(self, plan: ChangePlan) -> list[PackItem]:
        policy = stable_json(
            {"read_only": plan.read_only, "guard": plan.guard, "verify": plan.verify}
        )
        items = [
            PackItem(
                "policy",
                Layer.L0,
                "Indexed context. Protected paths are readonly.\n" + policy,
                100,
                "compiled protection and verification",
            )
        ]
        sources = {
            path: text
            for path in RULE_FILES
            if (text := self._reader.service.inventory.read(path)) is not None
        }
        rules = project_rules(sources)
        if rules:
            items.append(PackItem("project-rules", Layer.L0, rules[:3200], 100, "project checks"))
        return items

    def _file(
        self,
        file: IndexedFile,
        scope: _Scope,
        here: tuple[RequestAnchor, ...],
        anchoring: _Anchoring,
    ) -> list[PackItem]:
        path = file.path
        text = self._reader.service.inventory.read(path)
        if text is None or hashlib.sha256(text.encode()).hexdigest() != file.content_hash:
            for anchor in here:
                anchoring.place(anchor, path, AnchorState.STALE)
            return []
        plan = scope.plan
        protected = _protected(path, plan)
        score = scope.scores.get(path, 1.0) + (10.0 if path in scope.edit else 0)
        reason = scope.reasons.get(path, "compiled change plan")
        facts = () if protected else self._reader.facts(path)
        fresh = tuple(
            row
            for row in facts
            if not row.stale
            and row.source_hash == file.content_hash
            and not revalidate_fact(row, file, text).stale
        )
        summaries = tuple(
            row
            for row in self._reader.service.index.rows("notes", path)
            if row.relation == "summary" and not row.stale and row.source_hash == file.content_hash
        )
        items = [self._card(file, scope, protected, score, reason, here, (*fresh, *summaries))]
        items.extend(
            PackItem(
                row.id,
                Layer.L1,
                row.text,
                score + 8,
                "fresh anchored fact",
                path,
                row.line,
                row.end_line,
                path in scope.edit and not plan.read_only,
            )
            for row in fresh
        )
        lines = text.splitlines()
        ranges = self._ranges(path, lines, scope, here, protected, score, anchoring)
        items.extend(ranges)
        if scope.edit_only or protected or ranges:
            return items
        window = self._window(file, lines, fresh, scope, score, reason)
        return [*items, window] if window is not None else items

    def _ranges(
        self,
        path: str,
        lines: list[str],
        scope: _Scope,
        here: tuple[RequestAnchor, ...],
        protected: bool,
        score: float,
        anchoring: _Anchoring,
    ) -> list[PackItem]:
        spans, beyond = anchor_windows(path, here, len(lines), scope.window)
        for anchor in beyond:
            anchoring.place(anchor, path, AnchorState.OUT_OF_RANGE)
        items: list[PackItem] = []
        for span in spans:
            key = ANCHOR_PREFIX + span.anchor.label
            for member in span.members:
                if protected:
                    anchoring.place(member, path, AnchorState.PROTECTED)
                else:
                    state, first, last = packed_state(member, span, len(lines))
                    anchoring.place(member, path, state, key, (first, last))
            if not protected:
                items.append(
                    PackItem(
                        key,
                        Layer.L2,
                        _numbered(lines, span.first, span.last),
                        score,
                        "anchored request range",
                        path,
                        span.first,
                        span.last,
                        priority=1,
                    )
                )
        return items

    def _window(
        self,
        file: IndexedFile,
        lines: list[str],
        fresh: tuple[IndexRow, ...],
        scope: _Scope,
        score: float,
        reason: str,
    ) -> PackItem | None:
        symbols = tuple(
            row
            for row in self._reader.service.index.rows("symbols", file.path)
            if not row.stale and row.source_hash == file.content_hash and row.line > 0
        )
        matched = tuple(
            row for row in symbols if any(term in row.text.casefold() for term in scope.terms)
        )
        found = next(iter((*fresh, *matched, *symbols)), None)
        start = max(1, (found.line if found else 1) - 3)
        end = min(len(lines), start + scope.window - 1)
        if end < start:
            return None
        return PackItem(
            "window:" + file.path,
            Layer.L2,
            _numbered(lines, start, end),
            score,
            reason + "; bounded indexed source window",
            file.path,
            start,
            end,
        )

    def _card(
        self,
        file: IndexedFile,
        scope: _Scope,
        protected: bool,
        score: float,
        reason: str,
        here: tuple[RequestAnchor, ...],
        notes: tuple[IndexRow, ...],
    ) -> PackItem:
        index = self._reader.service.index
        path = file.path
        symbols = index.rows("symbols", path)
        if protected:
            card = handling_card(file, symbols, (), (), (), (), (), ())
        else:
            card = handling_card(
                file,
                symbols,
                tuple(row for row in index.rows("edges") if row.target == path),
                index.rows("edges", path),
                notes,
                tuple(row for row in index.rows("rules") if rule_applies(row, path)),
                tuple(row for row in index.rows("test_links") if row.target == path),
                index.rows("history", path),
            )
        label = "Readonly context.\n" if protected or scope.plan.read_only else ""
        writable = path in scope.edit and not protected and not scope.plan.read_only
        if not here:
            return PackItem(
                "card:" + path, Layer.L1, label + card.text, score + 5, reason, path, write=writable
            )
        labels = ", ".join(
            dict.fromkeys(RequestAnchor(path, anchor.start, anchor.end).label for anchor in here)
        )
        return PackItem(
            ANCHOR_PREFIX + path,
            Layer.L1,
            label + card.text,
            score + 5,
            f"explicit request anchor {labels}",
            path,
            write=writable,
            priority=2,
        )
