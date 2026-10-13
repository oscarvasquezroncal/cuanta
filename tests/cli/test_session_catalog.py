from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path

import pytest

from cuanta.cli.output import GlobalOptions, OutputMode, OutputSettings
from cuanta.cli.presenters.plain import PlainPresenter
from cuanta.cli.runtime import Session
from cuanta.cli.theme import ThemeName
from cuanta.tui import i18n


def open_plain(project: Path, language: str) -> Session:
    settings = OutputSettings(
        OutputMode.PLAIN, ThemeName.DARK, False, False, False, language=language
    )
    return Session(GlobalOptions(), settings, PlainPresenter(io.StringIO()), project)


@pytest.fixture
def builds(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    built: list[str] = []
    original = i18n.Catalog

    def counting(language: str) -> i18n.Catalog:
        built.append(language)
        return original(language)

    monkeypatch.setattr(i18n, "Catalog", counting)
    return built


def test_two_reads_of_the_catalog_build_it_once(tmp_path: Path, builds: list[str]) -> None:
    session = open_plain(tmp_path, "es")
    first = session.catalog
    second = session.catalog
    assert first is second
    assert builds == ["es"]
    assert first.language == "es"


def test_a_session_in_another_language_gets_its_own_catalog(
    tmp_path: Path, builds: list[str]
) -> None:
    spanish = open_plain(tmp_path, "es")
    english = open_plain(tmp_path, "en")
    assert spanish.catalog is not english.catalog
    assert spanish.catalog.language == "es"
    assert english.catalog.language == "en"
    assert spanish.catalog is spanish.catalog
    assert english.catalog is english.catalog
    assert builds == ["es", "en"]


def test_sessions_that_differ_only_by_the_cache_stay_equal(tmp_path: Path) -> None:
    session = open_plain(tmp_path, "en")
    twin = replace(session)
    loaded = session.catalog
    assert session == twin
    assert twin.catalog is not loaded
