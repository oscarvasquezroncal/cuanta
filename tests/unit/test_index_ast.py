from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest

from cuanta.adapters.graph.index_ast import AstIndexExtractor
from cuanta.domain.code_index import IndexedFile


def _file(path: str, text: str, language: str) -> IndexedFile:
    return IndexedFile(
        path, hashlib.sha256(text.encode()).hexdigest(), language, len(text.encode())
    )


def test_tsx_next_store_hooks_env_exports_and_directed_imports() -> None:
    text = """import { create as makeStore } from "zustand";
import { Cart } from "@/components/Cart";
export const useCart = makeStore(() => ({ items: [] }));
export function useCheckout() { return process.env.NEXT_PUBLIC_CHECKOUT; }
export default function Page() { return <Cart />; }
"""
    file = _file("src/app/(shop)/checkout/page.tsx", text, "typescript")
    result = AstIndexExtractor().extract(file, text, (file.path, "src/components/Cart.tsx"))
    assert result.coverage == "ast"
    assert {(row.text, row.relation) for row in result.symbols} >= {
        ("useCart", "store"),
        ("useCheckout", "hook"),
        ("Page", "component"),
        ("page", "route"),
    }
    assert next(row.target for row in result.symbols if row.relation == "route") == "/checkout"
    assert {(row.relation, row.target) for row in result.edges} >= {
        ("imports", "module:zustand"),
        ("imports", "src/components/Cart.tsx"),
        ("env", "env:NEXT_PUBLIC_CHECKOUT"),
        ("exports", file.path + ":3:useCart"),
    }
    assert all(
        row.path == file.path and row.source_hash == file.content_hash
        for row in result.symbols + result.edges
    )
    assert all(
        row.provenance == "ast" and row.confidence == 1 for row in result.symbols + result.edges
    )


def test_javascript_relative_and_reexport_resolution_preserves_direction() -> None:
    text = """import run from "../services/run";
export { run as checkout } from "../services/run";
export class CartService {}
const make = () => run();
"""
    file = _file("src/ui/cart.js", text, "javascript")
    result = AstIndexExtractor().extract(file, text, (file.path, "src/services/run/index.js"))
    assert {(row.relation, row.target) for row in result.edges} >= {
        ("imports", "src/services/run/index.js"),
        ("exports", "src/services/run/index.js"),
    }
    assert {(row.text, row.relation) for row in result.symbols} >= {
        ("CartService", "service"),
        ("make", "function"),
    }
    assert all(row.path == file.path for row in result.edges)


def test_python_imports_classes_and_same_name_definitions_keep_identity() -> None:
    text = """from . import cart
from shop.services import checkout
import external_package
class Cart:
    def run(self): pass
def run(): pass
"""
    file = _file("src/shop/main.py", text, "python")
    paths = (file.path, "src/shop/cart.py", "src/shop/services/checkout.py")
    result = AstIndexExtractor().extract(file, text, paths)
    assert {row.target for row in result.edges if row.relation == "imports"} == {
        "src/shop/cart.py",
        "src/shop/services/checkout.py",
        "module:external_package",
    }
    same_names = tuple(row for row in result.symbols if row.text == "run")
    assert len(same_names) == 2
    assert len({row.id for row in same_names}) == 2
    other = _file("src/shop/other.py", "def run(): pass", "python")
    other_result = AstIndexExtractor().extract(other, "def run(): pass", (*paths, other.path))
    assert other_result.symbols[0].id not in {row.id for row in same_names}


def test_go_import_and_type_function_ast() -> None:
    text = """package main
import "example.com/project/internal/cart"
import "fmt"
type Basket struct {}
func Checkout() {}
"""
    file = _file("cmd/main.go", text, "go")
    result = AstIndexExtractor().extract(file, text, (file.path, "internal/cart/store.go"))
    assert {(row.text, row.relation) for row in result.symbols if row.relation != "import"} == {
        ("Basket", "type"),
        ("Checkout", "function"),
    }
    assert {row.target for row in result.edges if row.relation == "imports"} == {
        "internal/cart/store.go",
        "module:fmt",
    }
    assert {row.text for row in result.edges if row.relation == "exports"} == {"Basket", "Checkout"}


@pytest.mark.parametrize(
    ("language", "path", "text", "coverage"),
    [
        ("python", "broken.py", "def broken(", "reduced"),
        ("typescript", "broken.ts", "const a = ;", "reduced"),
        ("rust", "lib.rs", "fn run() {}", "unsupported"),
    ],
)
def test_invalid_or_unsupported_source_reports_reduced_coverage_without_structure(
    language: str, path: str, text: str, coverage: str
) -> None:
    result = AstIndexExtractor().extract(_file(path, text, language), text, (path,))
    assert result.coverage == coverage
    assert result.symbols == result.edges == ()


def test_package_manifest_preserves_commands_and_marks_verification_scripts() -> None:
    text = '{"scripts":{"build":"next build","dev":"next dev","test:unit":"vitest run","lint":"eslint ."}}'
    file = _file("package.json", text, "json")
    result = AstIndexExtractor().extract(file, text, (file.path,))
    assert result.coverage == "manifest"
    assert {(row.text, row.target) for row in result.symbols} == {
        ("npm run build", "next build"),
        ("npm run dev", "next dev"),
        ("npm run test:unit", "vitest run"),
        ("npm run lint", "eslint ."),
    }
    assert {row.target for row in result.edges} == {
        "npm run build",
        "npm run lint",
        "npm run test:unit",
    }


def test_dynamic_imports_and_non_zustand_create_are_not_claimed() -> None:
    text = "const useCart = create(() => ({})); import(variableName); const x = process.env[key];"
    file = _file("store.ts", text, "typescript")
    result = AstIndexExtractor().extract(file, text, (file.path,))
    assert next(row.relation for row in result.symbols if row.text == "useCart") == "hook"
    assert result.edges == ()


def test_import_cannot_escape_project_and_ambiguous_go_package_stays_unresolved() -> None:
    extractor = AstIndexExtractor()
    text = 'import hidden from "../../../hidden";'
    file = _file("src/main.ts", text, "typescript")
    assert (
        extractor.extract(file, text, ("hidden.ts", file.path)).edges[0].target
        == "module:../../../hidden"
    )
    go = 'package main\nimport "example.com/project/internal/cart"'
    go_file = _file("main.go", go, "go")
    result = extractor.extract(
        go_file, go, (go_file.path, "internal/cart/a.go", "internal/cart/b.go")
    )
    assert result.edges[0].target == "module:example.com/project/internal/cart"


def test_retained_import_declarations_can_resolve_added_and_deleted_targets() -> None:
    extractor = AstIndexExtractor()
    text = 'import { Cart } from "@/components/Cart";'
    file = _file("src/app/page.tsx", text, "typescript")
    original = extractor.extract(file, text, (file.path,))
    declaration = next(row for row in original.symbols if row.relation == "import")
    assert declaration.id == file.path + ":1:import:@/components/Cart"
    assert declaration.target == "module:@/components/Cart"
    assert declaration.source_hash == file.content_hash
    assert (
        extractor.resolve(
            declaration.path, declaration.text, (file.path, "src/components/Cart.tsx")
        )
        == "src/components/Cart.tsx"
    )
    assert (
        extractor.resolve(declaration.path, declaration.text, (file.path,))
        == "module:@/components/Cart"
    )
    assert extractor.resolve("src/main.py", "...outside", ("outside.py",)) == "module:...outside"


def test_layout_ts_route_and_invalid_manifest_keep_explicit_coverage() -> None:
    extractor = AstIndexExtractor()
    text = "export function GET() { return new Response('ok'); }"
    file = _file("src/app/api/status/route.ts", text, "typescript")
    result = extractor.extract(file, text, (file.path,))
    assert next(row.target for row in result.symbols if row.relation == "route") == "/api/status"
    layout = _file("app/@modal/layout.jsx", "export default () => <div/>;", "javascript")
    layout_result = extractor.extract(layout, "export default () => <div/>;", (layout.path,))
    assert next(row.target for row in layout_result.symbols if row.relation == "route") == "/"
    for value in ("{", "[]"):
        manifest = _file("package.json", value, "json")
        assert extractor.extract(manifest, value, (manifest.path,)).coverage == "reduced"
    manifest = _file("package.json", '{"scripts":{"bad":false}}', "json")
    assert extractor.extract(manifest, '{"scripts":{"bad":false}}', (manifest.path,)).symbols == ()


def test_route_and_lowercase_page_function_have_distinct_ids() -> None:
    text = "export function page() { return null; }"
    file = _file("app/page.tsx", text, "typescript")
    result = AstIndexExtractor().extract(file, text, (file.path,))
    rows = tuple(row for row in result.symbols if row.text == "page")
    assert {row.id for row in rows} == {"app/page.tsx:1:page", "app/page.tsx:1:route:page"}
    assert {row.relation for row in rows} == {"function", "route"}


def test_deep_frontend_ast_does_not_use_python_recursive_walk() -> None:
    text = "const nested = " + "(" * 1100 + "value" + ")" * 1100 + ";"
    file = _file("nested.ts", text, "typescript")
    result = AstIndexExtractor().extract(file, text, (file.path,))
    assert result.coverage == "ast"
    assert result.symbols[0].text == "nested"


def test_python_parser_recursion_limit_reports_reduced_coverage() -> None:
    file = _file("deep.py", "value = 1", "python")
    with patch("cuanta.adapters.graph.index_ast.ast.parse", side_effect=RecursionError):
        result = AstIndexExtractor().extract(file, "value = 1", (file.path,))
    assert result.coverage == "reduced"
    assert result.symbols == result.edges == ()


def test_go_only_exports_uppercase_package_names_and_methods() -> None:
    text = """package cart
type Basket struct {}
func Checkout() {}
func helper() { type Hidden struct {} }
func (b Basket) Add() {}
const Limit = 2
var Stock, local int
"""
    file = _file("cart.go", text, "go")
    result = AstIndexExtractor().extract(file, text, (file.path,))
    assert {row.text for row in result.edges if row.relation == "exports"} == {
        "Basket",
        "Checkout",
        "Add",
        "Limit",
        "Stock",
    }
    symbols = {row.id for row in result.symbols}
    assert all(row.target in symbols for row in result.edges if row.relation == "exports")


def test_python_literal_all_limits_exports_and_keeps_explicit_private_name() -> None:
    text = """__all__ = ["_public", "Checkout"]
def _public(): pass
class Checkout: pass
def hidden(): pass
"""
    file = _file("checkout.py", text, "python")
    result = AstIndexExtractor().extract(file, text, (file.path,))
    assert {row.text for row in result.edges if row.relation == "exports"} == {
        "_public",
        "Checkout",
    }
    assert {row.target for row in result.edges if row.relation == "exports"} == {
        "checkout.py:2:_public",
        "checkout.py:3:Checkout",
    }


def test_go_eof_semicolon_insertion_keeps_alias_coverage() -> None:
    text = "package cart\ntype Exported = int"
    file = _file("cart.go", text, "go")
    result = AstIndexExtractor().extract(file, text, (file.path,))
    assert result.coverage == "ast"
    assert {(row.text, row.relation) for row in result.symbols} == {("Exported", "type")}
    assert result.edges[0].target == "cart.go:2:Exported"


def test_python_default_exports_only_public_module_bindings() -> None:
    text = """from external import run as imported
VALUE = 2
_private = 3
def checkout():
    local = 1
    return local
"""
    file = _file("checkout.py", text, "python")
    result = AstIndexExtractor().extract(file, text, (file.path,))
    assert {row.text for row in result.edges if row.relation == "exports"} == {
        "imported",
        "VALUE",
        "checkout",
    }


@pytest.mark.parametrize(
    "assignment",
    [
        "__all__ = make_names()",
        "__all__ = ['run']\n__all__.append(name)",
        "__all__ = ['run']\n__all__ += extra",
        "if condition:\n    __all__ = ['run']",
        "__all__ = ['run']\nif condition:\n    __all__ = ['other']",
        "__all__ = ['run']\n__all__[0] = name",
        "__all__ = ['run']\ndel __all__",
    ],
)
def test_python_dynamic_all_does_not_invent_exports(assignment: str) -> None:
    text = assignment + "\ndef run(): pass"
    file = _file("dynamic.py", text, "python")
    result = AstIndexExtractor().extract(file, text, (file.path,))
    assert not any(row.relation == "exports" for row in result.edges)


def test_python_repeated_same_line_bindings_have_one_canonical_identity() -> None:
    text = "first, _, _ = values\n_ = 1; _ = 2\n_ = 3\n"
    file = _file("bindings.py", text, "python")
    result = AstIndexExtractor().extract(file, text, (file.path,))
    assert tuple(row.id for row in result.symbols) == (
        "bindings.py:1:first",
        "bindings.py:1:_",
        "bindings.py:2:_",
        "bindings.py:3:_",
    )
    assert len({row.id for row in result.symbols}) == len(result.symbols)
    other = _file("other.py", text, "python")
    other_result = AstIndexExtractor().extract(other, text, (file.path, other.path))
    assert not {row.id for row in result.symbols} & {row.id for row in other_result.symbols}


def test_frontend_repeated_same_line_bindings_and_exports_have_unique_identities() -> None:
    text = "export function run() {} export function run() {}\nexport function run() {}"
    file = _file("bindings.js", text, "javascript")
    result = AstIndexExtractor().extract(file, text, (file.path,))
    assert tuple(row.id for row in result.symbols) == ("bindings.js:1:run", "bindings.js:2:run")
    assert len({row.id for row in result.edges}) == len(result.edges)


@pytest.mark.parametrize(
    ("path", "language", "header", "declaration", "name"),
    [
        ("large.js", "javascript", "", "export function Checkout() {}", "Checkout"),
        ("large.ts", "typescript", "", "export function Checkout() {}", "Checkout"),
        (
            "large.tsx",
            "typescript",
            "",
            "export function Component() { return <section />; }",
            "Component",
        ),
        ("large.go", "go", "package cart\n", "func Checkout() {}", "Checkout"),
    ],
)
def test_long_native_ast_files_preserve_lines_beyond_small_integer_cache(
    path: str, language: str, header: str, declaration: str, name: str
) -> None:
    text = header + "\n" * 300 + declaration + "\n"
    file = _file(path, text, language)
    extractor = AstIndexExtractor()
    expected_line = 301 + header.count("\n")
    for _ in range(3):
        result = extractor.extract(file, text, (path,))
        assert result.coverage == "ast"
        symbol = next(row for row in result.symbols if row.text == name)
        assert symbol.line == symbol.end_line == expected_line
        exported = next(row for row in result.edges if row.relation == "exports")
        assert exported.target == f"{path}:{expected_line}:{name}"
