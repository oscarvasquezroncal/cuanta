from __future__ import annotations

from dataclasses import replace

from cuanta.domain.code_index import IndexedFile, IndexHistory, IndexRow
from cuanta.domain.index_facts import history_row
from cuanta.domain.index_search import rank_files, rule_applies, search_terms


def _file(path: str) -> IndexedFile:
    return IndexedFile(path, "hash:" + path, "typescript", 100)


def _row(path: str, text: str, relation: str = "function", target: str = "") -> IndexRow:
    return IndexRow(
        path + text + relation, path, "hash:" + path, "ast", text, 1, 1, target, relation
    )


def test_split_identifiers_and_bilingual_terms() -> None:
    assert search_terms("createCheckout cart_store carrito PRUEBAS") == (
        "create",
        "checkout",
        "cart",
        "store",
        "cart",
        "tests",
    )
    assert search_terms("HTTPServer rutas estilos") == ("http", "server", "routes", "styles")


def test_scoped_rules_do_not_leak_to_neighbour_directory() -> None:
    rule = _row("src/CLAUDE.md", "limits", "rule", "src/**")
    assert rule_applies(rule, "src/deep/cart.ts")
    assert not rule_applies(rule, "src-other/cart.ts")
    assert not rule_applies(replace(rule, stale=True), "src/cart.ts")


def test_cart_checkout_results_include_import_neighbours_and_reasons() -> None:
    paths = (
        "stores/cart.ts",
        "domain/create-checkout.ts",
        "components/CheckoutDrawer.tsx",
        "components/Drawer.tsx",
        "unrelated/logger.ts",
    )
    files = tuple(_file(path) for path in paths)
    symbols = (
        _row(paths[0], "useCartStore"),
        _row(paths[1], "createCheckout"),
        _row(paths[2], "CheckoutDrawer"),
    )
    edges = (
        _row(paths[2], "imports cart", "imports", paths[0]),
        _row(paths[3], "drawer", "imports", paths[2]),
    )
    hits = rank_files(files, symbols, edges, (), (), (), "carrito checkout")
    assert set(paths[:4]) <= {hit.path for hit in hits[:5]}
    assert paths[-1] not in {hit.path for hit in hits}
    assert all(hit.reasons for hit in hits)
    assert next(hit for hit in hits if hit.path == paths[3]).edges
    assert hits == rank_files(
        tuple(reversed(files)),
        tuple(reversed(symbols)),
        tuple(reversed(edges)),
        (),
        (),
        (),
        "carrito checkout",
    )


def test_facts_and_task_history_have_visible_reasons_and_stale_text_is_excluded() -> None:
    file = _file("cart.ts")
    note = replace(_row(file.path, "fresh total", "finding", "anchor:hash"), provenance="report")
    stale = replace(_row(file.path, "obsolete selector", "note"), stale=True)
    prior = history_row(
        IndexHistory(file.path, "R", "bug", "read", "2026-09-01", "accepted"), file.content_hash
    )
    (hit,) = rank_files((file,), (), (), (note, stale), (), (prior,), "fresh", "bug", "2026-09-01")
    assert hit.matched_terms == ("fresh",) and hit.facts == ("cart.ts:1-1",)
    assert hit.prior == 1.5 and any("prior" in reason for reason in hit.reasons)
    assert rank_files((file,), (), (), (stale,), (), (), "obsolete") == ()
    assert rank_files((file,), (), (), (), (), (), "") == ()
    assert rank_files((file,), (), (), (), (), (), "cart", limit=0) == ()


def test_duplicate_symbols_return_distinct_paths_and_hash_mismatch_is_excluded() -> None:
    files = (_file("a.ts"), _file("b.ts"))
    rows = tuple(_row(file.path, "SameSymbol") for file in files)
    hits = rank_files(files, rows, (), (), (), (), "same symbol")
    assert {hit.path for hit in hits} == {"a.ts", "b.ts"}
    assert rank_files(files, (replace(rows[0], source_hash="old"),), (), (), (), (), "same") == ()
