from __future__ import annotations

import pytest

from cuanta.domain.messages import english
from cuanta.domain.self_edit import self_edit_note
from cuanta.tui.i18n import Catalog


@pytest.mark.parametrize(
    ("package", "project", "windows", "inside"),
    [
        ("C:\\work\\cuanta\\src\\cuanta", "C:\\work\\cuanta", True, True),
        ("c:/WORK/Cuanta/src/cuanta", "C:\\work\\cuanta\\", True, True),
        ("C:\\work\\cuanta\\.venv\\Lib\\site-packages\\cuanta", "C:\\work\\cuanta", True, False),
        ("C:\\tools\\cuanta\\src\\cuanta", "C:\\work\\cuanta", True, False),
        ("C:\\work\\cuanta-two\\src\\cuanta", "C:\\work\\cuanta", True, False),
        ("D:\\cuanta\\src\\cuanta", "C:\\work\\cuanta", True, False),
        ("/srv/app/src/cuanta", "/srv/app", False, True),
        ("/srv/App/src/cuanta", "/srv/app", False, False),
        ("/srv/app/.venv/lib/python3.12/site-packages/cuanta", "/srv/app", False, False),
    ],
)
def test_only_an_editable_cuanta_inside_the_project_is_named(
    package: str, project: str, windows: bool, inside: bool
) -> None:
    assert (self_edit_note(package, project, windows) is not None) is inside


def test_the_note_suggests_the_sandbox_or_the_installed_cuanta_in_both_languages() -> None:
    found = self_edit_note("/srv/app/src/cuanta", "/srv/app", False)
    assert found is not None
    assert "--sandbox" in english(found) and "editable" in english(found)
    assert "--sandbox" in Catalog("es").message(found)
    assert "editable" in Catalog("es").message(found)
