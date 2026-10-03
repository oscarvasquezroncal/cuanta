from __future__ import annotations

import pytest

from cuanta.domain.mandate_file import (
    LONG_STORY,
    LonePath,
    lone_path,
    mandate_path,
    parse_mandate_file,
    trimmed,
    typed_path,
    unquoted,
    whole_mandate,
)
from tests.real_run import phased_mandate


def test_a_long_pasted_mandate_is_split_exactly_like_a_mandate_file() -> None:
    text = phased_mandate()
    assert whole_mandate(text) == parse_mandate_file(text)


@pytest.mark.parametrize(
    "story",
    [
        "# Arreglar el carrito\nEl total suma dos veces.",
        "Arreglar el carrito\n===\nEl total suma dos veces.",
        "TIPO: error\nQUÉ: arreglar el total\n",
        "WHAT: fix the total\nWHY: it doubles\n",
        "x" * (LONG_STORY + 1),
    ],
    ids=["heading", "setext_title", "spanish_labels", "english_labels", "long"],
)
def test_a_title_heading_a_what_label_or_a_long_text_make_a_whole_mandate(story: str) -> None:
    assert whole_mandate(story) == parse_mandate_file(story)


@pytest.mark.parametrize(
    "story",
    [
        "Fix the cart total: AssertionError: expected 10 got 12. Don't touch payments.",
        "The checkout crashes.\nTraceback (most recent call last):\nKeyError: 'price'",
        "Tests: the cart test fails after the discount change",
        "```\n# not a heading\n```\nfix the parser",
        "#123 is broken since the deploy",
        "x" * LONG_STORY,
        "Fix this function, it returns None:\n# compute the total\ndef total(items):\n    sum(items)",
        "The deploy script fails as root:\n# ./deploy.sh\npermission denied: /var/run/app.sock",
        "Ruff rejects the config:\n[tool.ruff]\n# keep it at 100\nline-length = 100",
        "The lint job never runs:\nlint:\n  # run the linter\n  script: ruff check .",
        "Add a dark mode toggle to the settings page\n\n## Notes\n"
        "Use the existing theme tokens. Don't touch payments.",
        "Arregla el total del carrito\n\n## Notas\nNo toques los pagos.",
        "Fix the login\nWhy: users cannot sign in",
        "Type: the user types an email\nand the form breaks",
        "El botón falla\nDónde: en el carrito",
        "Fix the cart total. Don't touch payments.\nTests: the cart test",
    ],
    ids=[
        "one_line",
        "traceback",
        "label_without_what",
        "fenced_heading",
        "issue",
        "limit",
        "code_comment",
        "root_prompt",
        "toml_comment",
        "yaml_comment",
        "notes_section",
        "notas_section",
        "why_label",
        "type_sentence",
        "where_label",
        "tests_label",
    ],
)
def test_a_short_story_stays_with_the_intake(story: str) -> None:
    assert whole_mandate(story) is None


@pytest.mark.parametrize(
    ("text", "path"),
    [
        ("docs/mandato.md", "docs/mandato.md"),
        ("  mandato.MD \n", "mandato.MD"),
        ('"D:\\mandatos\\mandato largo.md"', "D:\\mandatos\\mandato largo.md"),
        ("D:\\mandatos\\mandato largo.txt", "D:\\mandatos\\mandato largo.txt"),
        ("'/srv/mandatos/plan.markdown'", "/srv/mandatos/plan.markdown"),
        ("~/mandatos/plan.md", "~/mandatos/plan.md"),
        ("\\\\server\\share\\mandato.md", "\\\\server\\share\\mandato.md"),
        ("/Users/me/My\\ Docs/mandato.md ", "/Users/me/My Docs/mandato.md"),
        ("~\\Documents\\mandato.md", "~\\Documents\\mandato.md"),
    ],
)
def test_a_lone_path_to_a_text_file_is_a_mandate_path(text: str, path: str) -> None:
    assert mandate_path(text) == path


@pytest.mark.parametrize(
    "text",
    [
        "Fix the bug in notes.txt",
        "docs/mandato.md\nand more text",
        "src/cart.py",
        "https://example.com/readme.md",
        "docs/my mandate.md",
        '""',
        "",
    ],
)
def test_a_story_that_only_mentions_a_file_is_not_a_path(text: str) -> None:
    assert mandate_path(text) == ""
    assert lone_path(text) is None


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("/fix the typo in README.md", LonePath("/fix the typo in README.md", True)),
        ("~ fix the typo in notes.md", LonePath("~ fix the typo in notes.md", True)),
        ("/fix the typo in docs/README.md", LonePath("/fix the typo in docs/README.md", True)),
        ("/Users/me/My\\ Docs/mandato.md", LonePath("/Users/me/My Docs/mandato.md")),
        ("'/srv/My Docs/plan.md'", LonePath("/srv/My Docs/plan.md")),
        ("D:\\mandatos\\mandato largo.md", LonePath("D:\\mandatos\\mandato largo.md")),
        ("\\\\server\\share\\mandato largo.md", LonePath("\\\\server\\share\\mandato largo.md")),
        ("/srv/mandatos/plan.md", LonePath("/srv/mandatos/plan.md")),
        (
            "~\\Documents\\My Docs\\mandato.md",
            LonePath("~\\Documents\\My Docs\\mandato.md"),
        ),
    ],
    ids=[
        "slash_sentence",
        "tilde_sentence",
        "slash_sentence_with_folder",
        "escaped_spaces",
        "quoted",
        "drive",
        "unc",
        "plain",
        "windows_home",
    ],
)
def test_only_an_unquoted_spaced_posix_path_may_be_the_story(text: str, found: LonePath) -> None:
    assert lone_path(text) == found


@pytest.mark.parametrize(
    ("text", "path"),
    [
        ("/Users/me/My\\ Docs/mandato.md ", "/Users/me/My Docs/mandato.md"),
        ("~/My\\ Docs/plan\\ \\(v2\\).md", "~/My Docs/plan (v2).md"),
        ("D:\\mandatos\\a.md", "D:\\mandatos\\a.md"),
        ("\\\\server\\share\\a.md", "\\\\server\\share\\a.md"),
        ("docs\\a.md", "docs\\a.md"),
        (' "D:\\mandatos\\a b.md" ', "D:\\mandatos\\a b.md"),
        ("'/srv/My\\ Docs/a.md'", "/srv/My\\ Docs/a.md"),
        ("~\\Documents\\mandato.md", "~\\Documents\\mandato.md"),
        ("~\\Documents\\My Docs\\a.md ", "~\\Documents\\My Docs\\a.md"),
    ],
    ids=[
        "macos_drop",
        "tilde",
        "drive",
        "unc",
        "relative",
        "quoted_drive",
        "quoted_posix",
        "windows_home",
        "windows_home_spaced",
    ],
)
def test_a_typed_path_loses_shell_escapes_only_when_it_is_an_unquoted_posix_path(
    text: str, path: str
) -> None:
    assert typed_path(text) == path


def test_quotes_around_a_copied_path_are_removed() -> None:
    assert unquoted(' "D:\\mandatos\\a.md" ') == "D:\\mandatos\\a.md"
    assert unquoted("'docs/a.md'") == "docs/a.md"
    assert unquoted('"docs/a.md') == '"docs/a.md'
    assert unquoted("  docs/a.md  ") == "docs/a.md"


def test_trimmed_drops_blank_edge_lines_and_keeps_the_rest_exactly() -> None:
    assert trimmed("\n  \n    code block\nlast line  \n\n") == "    code block\nlast line  "
    assert trimmed("") == ""
