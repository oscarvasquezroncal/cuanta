from __future__ import annotations

import hashlib
from dataclasses import asdict

from cuanta.application.change_plan import IndexChangePlan
from cuanta.application.index_read import IndexRead
from cuanta.domain.capsules import LineRange, slice_lines
from cuanta.domain.change_plan import ChangePlan, guarded, path_matches
from cuanta.domain.code_index import INDEX_TABLES
from cuanta.domain.depth import Depth, parse_depth, profile
from cuanta.domain.index_cards import handling_card
from cuanta.domain.index_facts import revalidate_fact
from cuanta.domain.index_search import rule_applies
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.pack import ContextPack, Layer, PackItem, compile_pack
from cuanta.domain.stable import stable_json


class IndexContextPack:
    def __init__(self, reader: IndexRead, cache: dict[str, ContextPack] | None = None) -> None:
        self._reader = reader
        self._cache = cache if cache is not None else {}

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
            plan if plan is not None else IndexChangePlan(index, lambda: now).compile(request)
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
            "version": 1,
        }
        key = hashlib.sha256(stable_json(identity).encode()).hexdigest()
        current = {
            file.path: file.content_hash for file in self._reader.service.inventory.candidates()
        }
        coherent = current == {file.path: file.content_hash for file in index.files()}
        if coherent and key in self._cache:
            return self._cache[key]
        self._cache.pop(key, None)
        items = self._items(request, protection, chosen.depth, chosen.read_budget, role)
        result = compile_pack(items, chosen.pack_tokens, key)
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
    ) -> tuple[PackItem, ...]:
        index = self._reader.service.index
        files = {file.path: file for file in index.files()}
        query = " ".join((request.what, request.where)).strip() or request.why
        hits = IndexRead(self._reader.service, lambda: index.meta().get("updated_at", "")).find(
            query, read_budget, request.type
        )
        edit = {item.path for item in plan.edit}
        roles = role.lower().replace("_", "-")
        edit_only = "senior" in roles or "tester" in roles
        paths = tuple(dict.fromkeys((*sorted(edit), *(hit.path for hit in hits), *plan.read)))
        scores = {hit.path: max(1.0, hit.score) for hit in hits}
        reasons = {hit.path: "; ".join(hit.reasons) for hit in hits}
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
        window = {Depth.QUICK: 12, Depth.NORMAL: 24, Depth.DEEP: 40}[depth]
        for path in paths[:read_budget]:
            if path not in files or (edit_only and (path not in edit or guarded(path, plan))):
                continue
            file = files[path]
            text = self._reader.service.inventory.read(path)
            if text is None or hashlib.sha256(text.encode()).hexdigest() != file.content_hash:
                continue
            protected = any(path_matches(path, pattern) for pattern in plan.guard)
            score = scores.get(path, 1.0) + (10.0 if path in edit else 0)
            reason = reasons.get(path, "compiled change plan")
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
                for row in index.rows("notes", path)
                if row.relation == "summary"
                and not row.stale
                and row.source_hash == file.content_hash
            )
            edges = index.rows("edges")
            card = (
                handling_card(file, index.rows("symbols", path), (), (), (), (), (), ())
                if protected
                else handling_card(
                    file,
                    index.rows("symbols", path),
                    tuple(row for row in edges if row.target == path),
                    index.rows("edges", path),
                    (*fresh, *summaries),
                    tuple(row for row in index.rows("rules") if rule_applies(row, path)),
                    tuple(row for row in index.rows("test_links") if row.target == path),
                    index.rows("history", path),
                )
            )
            label = "Readonly context.\n" if protected or plan.read_only else ""
            items.append(
                PackItem(
                    "card:" + path,
                    Layer.L1,
                    label + card.text,
                    score + 5,
                    reason,
                    path,
                    write=path in edit and not protected and not plan.read_only,
                )
            )
            for row in fresh:
                items.append(
                    PackItem(
                        row.id,
                        Layer.L1,
                        row.text,
                        score + 8,
                        "fresh anchored fact",
                        path,
                        row.line,
                        row.end_line,
                        path in edit and not plan.read_only,
                    )
                )
            if edit_only or protected:
                continue
            symbols = tuple(
                row
                for row in index.rows("symbols", path)
                if not row.stale and row.source_hash == file.content_hash and row.line > 0
            )
            matched = tuple(
                row
                for row in symbols
                if any(
                    term in row.text.casefold()
                    for term in query.casefold().split()
                    if len(term) > 2
                )
            )
            anchor = next(iter((*fresh, *matched, *symbols)), None)
            start = max(1, (anchor.line if anchor else 1) - 3)
            lines = text.splitlines()
            end = min(len(lines), start + window - 1)
            if end < start:
                continue
            excerpt = "\n".join(
                f"{number}: {line[:400]}" + (" [line truncated]" if len(line) > 400 else "")
                for number, line in slice_lines(lines, LineRange(start, end))
            )
            items.append(
                PackItem(
                    "window:" + path,
                    Layer.L2,
                    excerpt,
                    score,
                    reason + "; bounded indexed source window",
                    path,
                    start,
                    end,
                )
            )
        items.append(
            PackItem(
                "request",
                Layer.VOLATILE,
                stable_json(asdict(request)),
                100,
                "current request follows stable context",
            )
        )
        return tuple(items)
