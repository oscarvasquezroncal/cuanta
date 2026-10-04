from __future__ import annotations

import tomllib
from collections.abc import Mapping

import pytest

from cuanta.domain.errors import DomainFailure, ExitCode
from cuanta.domain.index_limit import (
    INDEX_FILE_LIMIT,
    LARGEST_FOLDERS,
    IndexTooLarge,
    oversized_index,
)
from cuanta.domain.messages import english

STEPS = {"a": 5, "b": 4, "c": 3, "d": 2, "e": 1, "f": 1}
REAL_RUN_SHAPE = {".venv312": 120, ".venv_ci": 60, ".odoo_ref": 45, "creator": 8, "tmp": 5}


def tree(folders: Mapping[str, int], root_files: int = 0) -> tuple[str, ...]:
    paths = [
        f"{name}/sub/m{number}.py" for name, count in folders.items() for number in range(count)
    ]
    paths.extend(f"top{number}.py" for number in range(root_files))
    return tuple(sorted(paths))


def test_the_ceiling_is_twenty_thousand_files_and_names_five_folders() -> None:
    assert INDEX_FILE_LIMIT == 20_000
    assert LARGEST_FOLDERS == 5


def test_at_or_under_the_limit_nothing_stops() -> None:
    paths = tree(STEPS, root_files=1)
    assert len(paths) == 17
    assert oversized_index(paths, (), 17) is None
    assert oversized_index(paths, (), 100) is None
    assert oversized_index((), (), 0) is None


def test_the_five_largest_top_level_folders_are_named_by_count_then_name() -> None:
    oversized = oversized_index(tree(STEPS, root_files=1), (), 10)
    assert oversized is not None
    assert oversized.files == 17
    assert oversized.limit == 10
    assert oversized.largest == (("a", 5), ("b", 4), ("c", 3), ("d", 2), ("e", 1))


def test_root_files_count_in_the_total_but_are_never_named() -> None:
    oversized = oversized_index(tree({"src": 3}, root_files=9), (), 10)
    assert oversized is not None
    assert oversized.files == 12
    assert oversized.largest == (("src", 3),)
    assert oversized.exclude == ("src",)


def test_the_real_run_shape_suggests_only_the_hidden_folders() -> None:
    oversized = oversized_index(tree(REAL_RUN_SHAPE), (), 200)
    assert oversized is not None
    assert [name for name, _ in oversized.largest] == list(REAL_RUN_SHAPE)
    assert oversized.exclude == (".venv312", ".venv_ci", ".odoo_ref")
    assert oversized.lines == '[detect]\nexclude = [".venv312", ".venv_ci", ".odoo_ref"]'


def test_a_hidden_folder_git_tracks_is_suggested_only_when_needed() -> None:
    paths = (*tree({".odoo_ref": 22_000, "creator": 700, "tests": 100, ".github": 4}), "README.md")
    untracked = oversized_index(paths, (), 20_000)
    assert untracked is not None and untracked.exclude == (".odoo_ref", ".github")
    oversized = oversized_index(paths, (), 20_000, frozenset({".github"}))
    assert oversized is not None
    assert oversized.exclude == (".odoo_ref",)
    assert oversized.lines == '[detect]\nexclude = [".odoo_ref"]'
    shape = tree({".big": 30, ".tracked": 20, "src": 5})
    needed = oversized_index(shape, (), 10, frozenset({".tracked"}))
    assert needed is not None and needed.exclude == (".big", ".tracked")


def test_configured_exclusions_come_first_then_what_is_needed_largest_first() -> None:
    oversized = oversized_index(tree(STEPS, root_files=1), ("docs/private.md",), 10)
    assert oversized is not None
    assert oversized.exclude == ("docs/private.md", "a", "b")
    assert oversized.lines.endswith('[detect]\nexclude = ["docs/private.md", "a", "b"]')


def test_a_configured_folder_is_not_suggested_twice() -> None:
    oversized = oversized_index(tree(REAL_RUN_SHAPE), (".ODOO_REF", "tmp/*"), 200)
    assert oversized is not None
    assert oversized.exclude == (".ODOO_REF", "tmp/*", ".venv312", ".venv_ci")


def test_the_lines_are_valid_toml_for_any_folder_name() -> None:
    names = {'we"ird': 3, "back\\slash": 3, "tab\there": 3, "ñandú": 3, "del\x7f": 3}
    hidden = {f".{name}": count for name, count in names.items()}
    oversized = oversized_index(tree(hidden), ("C:\\data",), 5)
    assert oversized is not None
    parsed = tomllib.loads(oversized.lines)
    assert parsed["detect"]["exclude"] == list(oversized.exclude)
    assert parsed["detect"]["exclude"][0] == "C:\\data"
    assert oversized.lines.splitlines()[0] == "[detect]"


def test_the_failure_names_the_folders_and_gives_the_lines_to_paste() -> None:
    oversized = oversized_index(tree(STEPS, root_files=1), ("docs/private.md",), 10)
    assert oversized is not None
    failure = IndexTooLarge(oversized)
    assert isinstance(failure, DomainFailure)
    assert failure.exit_code is ExitCode.DOMAIN_FAILURE
    assert failure.oversized is oversized
    assert failure.message == english(failure.reason)
    assert failure.hint == english(failure.advice)
    assert "before reading any file" in failure.message
    assert "17 files" in failure.message
    assert "a (5) · b (4) · c (3) · d (2) · e (1)" in failure.message
    assert "f (" not in failure.message
    assert ".gitignore" in failure.hint
    assert "replace" in failure.hint
    assert failure.hint.endswith('\n[detect]\nexclude = ["docs/private.md", "a", "b"]')


def test_large_counts_are_grouped_in_the_message() -> None:
    oversized = oversized_index(tree({".venv312": 25_000}), (), INDEX_FILE_LIMIT)
    assert oversized is not None
    message = IndexTooLarge(oversized).message
    assert "25,000 files" in message
    assert "20,000" in message
    assert ".venv312 (25,000)" in message


@pytest.mark.parametrize("limit", [0, 1])
def test_a_flat_tree_over_the_limit_still_explains_itself(limit: int) -> None:
    oversized = oversized_index(tree({}, root_files=3), (), limit)
    assert oversized is not None
    assert oversized.largest == ()
    assert oversized.exclude == ()
    failure = IndexTooLarge(oversized)
    assert "top level" in failure.message
    assert tomllib.loads(oversized.lines) == {"detect": {"exclude": []}}
