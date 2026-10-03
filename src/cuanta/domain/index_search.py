from __future__ import annotations

import fnmatch
import math
import re
import unicodedata
from collections import Counter, defaultdict

from cuanta.domain.code_index import IndexedFile, IndexRow, SearchHit
from cuanta.domain.index_facts import history_prior

_CAMEL = re.compile(r"([a-z0-9])([A-Z])|([A-Z])([A-Z][a-z])")
_ALIASES = {
    "carrito": "cart",
    "pago": "payment",
    "pruebas": "tests",
    "estilos": "styles",
    "rutas": "routes",
}


def search_terms(text: str) -> tuple[str, ...]:
    split = _CAMEL.sub(lambda m: f"{m[1] or m[3]} {m[2] or m[4]}", text)
    folded = "".join(
        char for char in unicodedata.normalize("NFKD", split) if not unicodedata.combining(char)
    ).casefold()
    terms = re.findall(r"[a-z0-9]+", folded)
    return tuple(_ALIASES.get(term, term) for term in terms)


def rule_applies(row: IndexRow, path: str) -> bool:
    return not row.stale and (
        row.target in {"**", path}
        or (row.target.endswith("/**") and path.startswith(row.target[:-2]))
        or fnmatch.fnmatchcase(path, row.target)
    )


def rank_files(
    files: tuple[IndexedFile, ...],
    symbols: tuple[IndexRow, ...],
    edges: tuple[IndexRow, ...],
    notes: tuple[IndexRow, ...],
    rules: tuple[IndexRow, ...],
    history: tuple[IndexRow, ...],
    query: str,
    task_type: str = "",
    now: str = "",
    limit: int = 10,
) -> tuple[SearchHit, ...]:
    if not query.strip() or not files or limit <= 0:
        return ()
    current = {file.path: file for file in files}
    documents: dict[str, Counter[str]] = {path: Counter(search_terms(path) * 3) for path in current}
    facts: dict[str, list[str]] = defaultdict(list)
    for row in (*symbols, *notes):
        if (
            row.path not in current
            or row.stale
            or row.source_hash != current[row.path].content_hash
        ):
            continue
        documents[row.path].update(
            search_terms(row.text) * (3 if row.relation in {"note", "finding", "summary"} else 2)
        )
        if row.relation == "finding" and row.target.startswith("anchor:"):
            facts[row.path].append(f"{row.path}:{row.line}-{row.end_line or row.line}")
    for row in rules:
        if (
            row.path not in current
            or row.source_hash != current[row.path].content_hash
            or row.stale
        ):
            continue
        terms = search_terms(row.text)
        for path, document in documents.items():
            if rule_applies(row, path):
                document.update(terms)
    wanted = frozenset(search_terms(query))
    frequencies = Counter(term for doc in documents.values() for term in wanted.intersection(doc))
    average = sum(sum(doc.values()) for doc in documents.values()) / len(documents) or 1.0
    lexical: dict[str, float] = {}
    matches: dict[str, tuple[str, ...]] = {}
    for path, doc in documents.items():
        matched = tuple(sorted(wanted.intersection(doc)))
        matches[path] = matched
        score = 0.0
        length = sum(doc.values())
        for term in matched:
            count = doc[term]
            rarity = math.log1p((len(files) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
            score += rarity * count * 2.2 / (count + 1.2 * (0.25 + 0.75 * length / average))
        lexical[path] = score
    seeds = {
        path
        for path in sorted(lexical, key=lambda path: (-lexical[path], path))[:5]
        if lexical[path] > 0
    }
    proximity: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        if (
            edge.stale
            or edge.path not in current
            or edge.target not in current
            or edge.source_hash != current[edge.path].content_hash
        ):
            continue
        if edge.path in seeds and edge.target != edge.path:
            proximity[edge.target].add(f"{edge.path} -> {edge.target} ({edge.relation})")
        if edge.target in seeds and edge.path != edge.target:
            proximity[edge.path].add(f"{edge.path} -> {edge.target} ({edge.relation})")
    grouped: dict[str, list[IndexRow]] = defaultdict(list)
    for row in history:
        if row.path in current:
            grouped[row.path].append(row)
    hits: list[SearchHit] = []
    for path in sorted(current):
        prior = history_prior(tuple(grouped[path]), task_type, now)
        links = tuple(sorted(proximity[path]))
        score = lexical[path] + min(len(links), 4) * 0.3 + min(prior, 4) * 0.15
        if score <= 0:
            continue
        matched_facts = tuple(sorted(set(facts[path]))) if matches[path] else ()
        reasons = tuple(
            value
            for value in (
                "matched: " + ", ".join(matches[path]) if matches[path] else "",
                "graph: " + "; ".join(links) if links else "",
                "facts: " + ", ".join(matched_facts) if matched_facts else "",
                f"decayed {task_type or 'all-task'} prior: {prior:.3f}" if prior else "",
            )
            if value
        )
        hits.append(
            SearchHit(path, round(score, 8), matches[path], links, matched_facts, prior, reasons)
        )
    return tuple(sorted(hits, key=lambda hit: (-hit.score, hit.path))[:limit])
