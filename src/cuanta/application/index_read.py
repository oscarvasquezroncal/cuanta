from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import replace

from cuanta.application.code_index import IndexService
from cuanta.domain.code_index import HandlingCard, IndexRow, SearchHit, index_path
from cuanta.domain.errors import CuantaError
from cuanta.domain.index_cards import handling_card
from cuanta.domain.index_search import rank_files, rule_applies, search_terms
from cuanta.domain.instinct import Ask, Primitive, Receipt, Score
from cuanta.domain.redaction import redact_for_remote
from cuanta.ports.instinct import BatchInstinct


class IndexRead:
    def __init__(self, service: IndexService, now: Callable[[], str]) -> None:
        self.service = service
        self._now = now
        self.rerank_receipt: Receipt | None = None
        self.rerank_reason = "disabled"

    def update(self) -> None:
        self.service.update()

    def close(self) -> None:
        self.service.close()

    def find(
        self,
        query: str,
        limit: int = 10,
        task_type: str = "",
        reranker: BatchInstinct | None = None,
        share_paths: bool = False,
    ) -> tuple[SearchHit, ...]:
        index = self.service.index
        self.rerank_receipt = None
        self.rerank_reason = "disabled"
        selected = rank_files(
            index.files(),
            index.rows("symbols"),
            index.rows("edges"),
            index.rows("notes"),
            index.rows("rules"),
            index.rows("history"),
            query,
            task_type,
            self._now(),
            60 if reranker is not None and share_paths else limit,
        )
        if reranker is None or not share_paths:
            return selected[:limit]
        if len(selected) < 30:
            self.rerank_reason = "fewer than 30 candidates"
            return selected[:limit]
        asks: list[Ask] = []
        metadata: list[str] = []
        for number, hit in enumerate(selected):
            key = f"candidate-{number:02d}"
            symbols = tuple(
                sorted(
                    {
                        row.text
                        for row in index.rows("symbols", hit.path)
                        if not row.stale and re.fullmatch(r"[A-Za-z_$][\w.$:-]{0,80}", row.text)
                    }
                )
            )[:8]
            metadata.append(
                redact_for_remote(f"{key}: {index_path(hit.path)}; symbols={','.join(symbols)}")
            )
            asks.append(
                Ask(
                    key,
                    Primitive.SCORE,
                    f"Rank {key} relevance to the search terms in state.",
                    low=0,
                    high=9,
                )
            )
        context = {
            "terms": tuple(search_terms(redact_for_remote(query)))[:16],
            "candidates": tuple(metadata),
        }
        try:
            answers, receipt = reranker.ask_many(tuple(asks), context)
        except (CuantaError, ValueError, TypeError, KeyError):
            self.rerank_reason = "unavailable; deterministic ranking retained"
            return selected[:limit]
        self.rerank_receipt = receipt
        if receipt.backend != "jev":
            self.rerank_reason = "unavailable; deterministic ranking retained"
            return selected[:limit]
        updated: list[SearchHit] = []
        for number, hit in enumerate(selected):
            answer = answers.get(f"candidate-{number:02d}")
            if isinstance(answer, Score) and math.isfinite(answer.value) and 0 <= answer.value <= 9:
                updated.append(
                    replace(
                        hit,
                        score=round(hit.score + answer.value, 8),
                        reasons=(*hit.reasons, f"jev relevance: {answer.value:.3f}"),
                    )
                )
            else:
                self.rerank_reason = "invalid answer; deterministic ranking retained"
                return selected[:limit]
        self.rerank_reason = "one metadata-only batch"
        return tuple(sorted(updated, key=lambda hit: (-hit.score, hit.path))[:limit])

    def card(self, path: str) -> HandlingCard:
        path = index_path(path)
        index = self.service.index
        file = next((file for file in index.files() if file.path == path), None)
        if file is None:
            raise ValueError("The file is not indexed")
        edges = index.rows("edges")
        return handling_card(
            file,
            index.rows("symbols", path),
            tuple(row for row in edges if row.target == path),
            index.rows("edges", path),
            index.rows("notes", path),
            tuple(row for row in index.rows("rules") if rule_applies(row, path)),
            tuple(row for row in index.rows("test_links") if row.target == path),
            index.rows("history", path),
        )

    def facts(self, path: str = "", stale: bool = False) -> tuple[IndexRow, ...]:
        path = index_path(path) if path else ""
        return tuple(
            row
            for row in self.service.index.rows("notes", path)
            if row.relation in {"note", "finding"} and row.stale == stale
        )

    def impact(self, path: str, depth: int = 1) -> tuple[tuple[str, str], ...]:
        path = index_path(path)
        index = self.service.index
        current = {file.path: file for file in index.files()}
        if path not in current:
            raise ValueError("The file is not indexed")
        neighbours: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for edge in index.rows("edges"):
            if (
                edge.path in current
                and edge.target in current
                and not edge.stale
                and edge.source_hash == current[edge.path].content_hash
                and edge.path != edge.target
            ):
                reason = f"{edge.path} -> {edge.target} ({edge.relation})"
                neighbours[edge.path].add((edge.target, reason))
                neighbours[edge.target].add((edge.path, reason))
        seen, frontier = {path}, {path}
        results: dict[str, str] = {}
        for _ in range(max(0, min(depth, 5))):
            following: set[str] = set()
            for parent in sorted(frontier):
                for other, reason in sorted(neighbours[parent]):
                    if other not in seen:
                        seen.add(other)
                        following.add(other)
                        results[other] = reason
            frontier = following
        return tuple(sorted(results.items()))
