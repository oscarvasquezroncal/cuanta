from __future__ import annotations

from cuanta.domain.affected import (
    changed_files,
    file_neighbours,
    is_test_file,
    narrowed,
    related_tests,
    select,
)
from cuanta.domain.messages import english

FILES = (
    "src/shop/cart.py",
    "src/shop/pricing.py",
    "src/shop/__init__.py",
    "tests/test_cart.py",
    "tests/test_pricing.py",
    "tests/test_checkout.py",
    "web/cart.test.ts",
)


def test_test_files_are_recognised_across_stacks() -> None:
    assert is_test_file("tests/test_cart.py")
    assert is_test_file("pkg/cart_test.go")
    assert is_test_file("web/cart.spec.tsx")
    assert is_test_file("crate/tests/cart.rs")
    assert not is_test_file("src/shop/cart.py")


def test_changed_files_compare_hashes_and_ignore_deleted_paths() -> None:
    baseline = {"a.py": "1", "b.py": "2", "gone.py": "3"}
    current = {"a.py": "1", "b.py": "9", "new.py": "4"}
    assert changed_files(baseline, current) == ("b.py", "new.py")


def test_related_tests_use_names_and_graph_neighbours() -> None:
    neighbours = file_neighbours([("src/shop/pricing.py", "tests/test_checkout.py")])
    picked = related_tests(("src/shop/cart.py", "src/shop/pricing.py"), FILES, neighbours)
    assert picked == (
        "tests/test_cart.py",
        "tests/test_checkout.py",
        "tests/test_pricing.py",
        "web/cart.test.ts",
    )


def test_pytest_selection_runs_last_failed_first() -> None:
    chosen = select("pytest", ("src/shop/pricing.py",), FILES, {}, True)
    assert not chosen.full
    assert chosen.arguments == ("tests/test_pricing.py", "--ff")
    assert english(chosen.reason).startswith("1 related tests for 1 changed files")


def test_selection_falls_back_to_the_full_suite() -> None:
    assert select("pytest", ("x.py",), FILES, {}, False).full
    assert select("cargo", ("src/lib.rs",), FILES, {}, True).full
    assert select("pytest", (), FILES, {}, True).full
    assert select("pytest", ("docs/readme.md",), FILES, {}, True).full


def test_go_selection_narrows_packages() -> None:
    files = ("pkg/cart/cart.go", "pkg/cart/cart_test.go", "main.go")
    chosen = select("go", ("pkg/cart/cart.go",), files, {}, True)
    assert chosen.arguments == ("./pkg/cart",)
    assert narrowed(("go", "test", "./..."), "go", chosen.arguments) == [
        "go",
        "test",
        "./pkg/cart",
    ]


def test_pytest_without_its_cache_plugin_drops_last_failed_first() -> None:
    base = ("pytest", "-p", "no:cacheprovider")
    assert narrowed(base, "pytest", ("tests/a.py", "--ff")) == [*base, "tests/a.py"]
