from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from cuanta.domain.code_index import IndexedFile, IndexRow
from cuanta.domain.index_rules import scoped_rules
from cuanta.domain.index_rules import test_links as links_for_tests


def _file(path: str, text: str = "source") -> IndexedFile:
    return IndexedFile(path, hashlib.sha256(text.encode()).hexdigest(), "markdown", len(text))


def test_root_and_nested_rulebooks_preserve_scope_sections_and_provenance() -> None:
    text = "# Project\nIntro\n\n## Safety\nPreserve types.\n### Local\nNever write secrets.\n"
    root = scoped_rules(_file("CLAUDE.md", text), text)
    nested = scoped_rules(_file("src/domain/CLAUDE.md", text), text)
    assert {row.target for row in root} == {"**"}
    assert {row.target for row in nested} == {"src/domain/**"}
    assert [(row.line, row.end_line) for row in root] == [(1, 3), (4, 5), (6, 7)]
    assert root[-1].provenance == "CLAUDE.md § Project > Safety > Local"
    assert root[-1].text == "### Local\nNever write secrets."
    assert all(row.relation == "rule" and not row.stale for row in root + nested)
    assert all(row.path == "src/domain/CLAUDE.md" for row in nested)


def test_markdown_fenced_headings_do_not_change_rule_scope() -> None:
    text = "Rules first.\n```markdown\n## Fake\n```\n## Actual\nReal rule.\n"
    rows = scoped_rules(_file("CLAUDE.md", text), text)
    assert len(rows) == 2
    assert rows[0].line == 1 and rows[0].end_line == 4
    assert rows[1].provenance == "CLAUDE.md § Actual"


def test_improvements_keep_identifiers_paths_status_and_source_lines() -> None:
    text = (
        "# Improvements\n\n"
        "- [ ] IMP-007 — preserve state — src/store.ts:42 — severity: high\n"
        "- [x] IMP-008 — fixed — `src/page.tsx` and `../outside.ts`\n"
    )
    file = _file("docs/IMPROVEMENTS.md", text)
    rows = scoped_rules(file, text)
    assert len(rows) == 2
    assert (rows[0].target, rows[0].line, rows[0].end_line) == ("src/store.ts", 3, 3)
    assert "IMP-007" in rows[0].provenance
    assert rows[1].target == "src/page.tsx"
    assert "[x] IMP-008" in rows[1].text
    assert all(row.source_hash == file.content_hash for row in rows)


def test_fenced_improvement_examples_do_not_become_open_items() -> None:
    text = (
        "\n# Improvements\n```markdown\n"
        "- [ ] IMP-001 — example — src/example.py:1\n```\n"
        "- [ ] IMP-002 — actual — src/actual.py:2\n"
    )
    rows = scoped_rules(_file("docs/IMPROVEMENTS.md", text), text)
    assert len(rows) == 1
    assert rows[0].line == 6 and rows[0].target == "src/actual.py"
    assert "IMP-002" in rows[0].provenance


def test_flag_registry_links_each_table_entry_at_its_source_line() -> None:
    text = (
        "# Flags\nRegistry.\n| Key | Where | Purpose |\n|---|---|---|\n"
        "| `MODE` | `src/mode.py` | select mode |\n"
        "| `LIMIT` | `src/limit.py`, `src/config.py` | turn limit |\n"
    )
    rows = scoped_rules(_file("docs/FLAGS.md", text), text)
    assert [(row.line, row.end_line, row.target) for row in rows] == [
        (5, 5, "src/mode.py"),
        (6, 6, "src/config.py"),
        (6, 6, "src/limit.py"),
    ]
    assert rows[0].text == "| `MODE` | `src/mode.py` | select mode |"
    assert rows[0].provenance == "docs/FLAGS.md § Flags > `MODE`"


@pytest.mark.parametrize(
    ("path", "relation"),
    [
        ("docs/GROUND_TRUTH.md", "ground-truth"),
        ("docs/FLAGS.md", "flag"),
        ("docs/HISTORIAS.md", "story"),
        ("HISTORIAS.md", "story"),
    ],
)
def test_document_sections_use_only_explicit_file_references(path: str, relation: str) -> None:
    text = (
        "# Records\nGeneral record.\n"
        "## Detail\nSee `src/cart.ts` and [test](tests/cart.test.ts).\n"
        "Ignore `npm run build`, `../external.py` and [url](https://example.com/data.py).\n"
    )
    rows = scoped_rules(_file(path, text), text)
    assert [(row.line, row.target) for row in rows] == [
        (1, "**"),
        (3, "src/cart.ts"),
        (3, "tests/cart.test.ts"),
    ]
    assert all(row.relation == relation and row.provenance.startswith(path) for row in rows)


def test_unknown_documents_and_unsafe_rulebook_paths_produce_no_rules() -> None:
    assert not scoped_rules(_file("docs/README.md"), "source")
    assert not scoped_rules(_file("../CLAUDE.md"), "source")
    assert not scoped_rules(_file("CLAUDE.md", ""), "")


def test_source_mismatch_keeps_rule_provenance_but_marks_it_stale() -> None:
    rows = scoped_rules(_file("CLAUDE.md", "old"), "## Updated\nnew\n")
    assert rows and all(row.stale for row in rows)


def test_test_import_links_keep_direction_verification_and_source_hash() -> None:
    source = _file("src/cart.ts")
    test = _file("tests/cart.test.ts", "test")
    edge = IndexRow(
        "import",
        test.path,
        test.content_hash,
        "ast",
        line=2,
        end_line=2,
        target=source.path,
        relation="imports",
    )
    rows = links_for_tests((source, test), (edge,), ("npm test", "npx tsc --noEmit", "npm test"))
    assert len(rows) == 1
    assert rows[0].path == test.path and rows[0].target == source.path
    assert rows[0].source_hash == test.content_hash
    assert rows[0].text == "npm test\nnpx tsc --noEmit"
    assert rows[0].provenance == "ast import tests/cart.test.ts:2"
    assert rows[0].relation == "tests"


def test_test_import_links_reject_stale_unknown_nonimports_and_nontests() -> None:
    source = _file("src/cart.py")
    test = _file("tests/test_cart.py", "test")
    edge = IndexRow(
        "import",
        test.path,
        test.content_hash,
        "ast",
        line=1,
        end_line=1,
        target=source.path,
        relation="imports",
    )
    invalid = (
        replace(edge, stale=True),
        replace(edge, source_hash="old"),
        replace(edge, target="module:external"),
        replace(edge, relation="exports"),
        replace(edge, path=source.path, source_hash=source.content_hash, target=test.path),
    )
    assert not links_for_tests((source, test), invalid, ())
    assert (
        len(links_for_tests((source, test), (edge, replace(edge, id="duplicate", line=2)), ())) == 1
    )
